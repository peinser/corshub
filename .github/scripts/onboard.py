#!/usr/bin/env python3
"""
Onboarding bot: generates NTRIP credentials for new and rotating entries
in ops/values.yaml.

Onboarding: new entry without password_hash:
  1. Fetches the PR opener's SSH public keys from GitHub.
  2. Generates a cryptographically random password and bcrypt-hashes it.
  3. age-encrypts the plaintext password to the opener's SSH keys.
  4. Writes the hash into ops/values.yaml via sops --set (encrypted in-place).
  5. Posts the encrypted credential as a PR comment.
  6. Records the SSH key fingerprints in ops/key-fingerprints.yaml.

Rotation: existing entry with password_hash removed by its original owner.
  Same as onboarding, but verifies that the PR opener matches the GitHub user
  pinned in ops/key-fingerprints.yaml for that entry before re-issuing.

Tampering: password_hash removed by someone other than the original owner.
  Rejected with an explanatory comment.

Environment (all set by onboard.yml):
  SOPS_AGE_KEY        AGE private key, passed through to sops transparently.
  GH_TOKEN            GitHub token with contents:write + pull-requests:write.
  GITHUB_REPOSITORY   owner/repo.
  PR_NUMBER           Pull request number.
  GITHUB_ACTOR        GitHub username of the PR opener.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import bcrypt
import yaml


VALUES_FILE       = Path("ops/values.yaml")
FINGERPRINTS_FILE = Path("ops/key-fingerprints.yaml")

PR_NUMBER    = os.environ["PR_NUMBER"]
REPO         = os.environ["GITHUB_REPOSITORY"]
GH_TOKEN     = os.environ["GH_TOKEN"]
GITHUB_ACTOR = os.environ["GITHUB_ACTOR"]

SUBPROCESS_TIMEOUT = 30  # seconds


def sops_decrypt(path: Path) -> dict[str, Any]:
    result = subprocess.run(
        ["sops", "-d", "--output-type", "yaml", str(path)],
        capture_output=True, text=True, check=True,
        timeout=SUBPROCESS_TIMEOUT,
    )
    return yaml.safe_load(result.stdout) or {}


def sops_set(path: Path, *keys: str, value: str) -> None:
    """Write an encrypted scalar into a SOPS-managed YAML file in-place."""
    # json.dumps escapes any special characters in key names.
    json_path = "".join(f"[{json.dumps(k)}]" for k in keys)
    subprocess.run(
        ["sops", "--set", f"{json_path} {json.dumps(value)}", str(path)],
        check=True, timeout=SUBPROCESS_TIMEOUT,
    )


def decrypt_main_values() -> dict[str, Any]:
    """Return the decrypted ops/values.yaml from the main branch."""
    try:
        result = subprocess.run(
            ["git", "show", "origin/main:ops/values.yaml"],
            capture_output=True, text=True, check=True,
            timeout=SUBPROCESS_TIMEOUT,
        )
    except subprocess.CalledProcessError:
        # File does not exist on main yet (first PR to this repo).
        return {}

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml") as f:
        f.write(result.stdout)
        f.flush()
        # Do NOT catch CalledProcessError here: if sops fails (wrong key,
        # corrupted file), treat it as fatal. Silently returning {} would cause
        # every existing entry to look new and re-issue all credentials.
        return sops_decrypt(Path(f.name))


def _github_get(path: str) -> Any:
    url = f"https://api.github.com{path}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {GH_TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=SUBPROCESS_TIMEOUT) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"GitHub API {url} returned {exc.code}: {exc.read().decode()}"
        ) from exc


def fetch_ssh_keys(github_user: str) -> list[str]:
    safe = urllib.parse.quote(github_user, safe="")
    data = _github_get(f"/users/{safe}/keys")
    return [entry["key"] for entry in data if "key" in entry]


def post_comment(body: str) -> None:
    result = subprocess.run(
        ["gh", "pr", "comment", PR_NUMBER, "--repo", REPO, "--body", body],
        capture_output=True, text=True,
        env={**os.environ, "GH_TOKEN": GH_TOKEN},
        timeout=SUBPROCESS_TIMEOUT,
    )
    if result.returncode != 0:
        raise RuntimeError(f"gh pr comment failed: {result.stderr.strip()}")


def key_fingerprint(pubkey: str) -> str | None:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".pub") as f:
        f.write(pubkey)
        f.flush()
        r = subprocess.run(
            ["ssh-keygen", "-lf", f.name],
            capture_output=True, text=True,
            timeout=SUBPROCESS_TIMEOUT,
        )
    return r.stdout.split()[1] if r.returncode == 0 and r.stdout.split() else None


def age_encrypt(plaintext: str, pubkeys: list[str]) -> str:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".pub") as f:
        f.write("\n".join(pubkeys))
        f.flush()
        result = subprocess.run(
            ["age", "-R", f.name],
            input=plaintext, capture_output=True, text=True, check=True,
            timeout=SUBPROCESS_TIMEOUT,
        )
    return result.stdout


@dataclass
class Credential:
    pw_hash:      str
    encrypted:    str
    fingerprints: list[str]


def generate_credential(username: str, role: str, errors: list[str]) -> Credential | None:
    """
    Fetch the PR opener's SSH keys, generate a random password, bcrypt-hash
    it, and age-encrypt the plaintext. Returns a Credential on success or
    appends to errors and returns None on failure.

    Does NOT write to disk or post any comment. All side effects are left
    to the caller so they occur only after the hash is durably stored.
    """
    try:
        ssh_keys = fetch_ssh_keys(GITHUB_ACTOR)
    except (RuntimeError, OSError) as exc:
        errors.append(
            f"[{username}] ({role}): could not fetch SSH keys for "
            f"@{GITHUB_ACTOR}: {exc}"
        )
        return None

    age_keys = [k for k in ssh_keys if k.startswith(("ssh-ed25519", "ssh-rsa"))]
    if not age_keys:
        errors.append(
            f"[{username}] ({role}): no ed25519 or RSA SSH keys found for "
            f"@{GITHUB_ACTOR}. Add one at https://github.com/settings/keys."
        )
        return None

    token   = secrets.token_urlsafe(32)
    pw_hash = bcrypt.hashpw(token.encode(), bcrypt.gensalt(12)).decode()

    try:
        encrypted = age_encrypt(token, age_keys)
    except subprocess.CalledProcessError as exc:
        errors.append(
            f"[{username}] ({role}): age encryption failed: {exc.stderr.strip()}"
        )
        return None

    fingerprints = [fp for k in age_keys if (fp := key_fingerprint(k))]
    return Credential(pw_hash=pw_hash, encrypted=encrypted, fingerprints=fingerprints)


def _post_credential_comment(username: str, role: str, encrypted: str) -> None:
    body = (
        f"Credentials issued for `{username}` ({role})\n\n"
        f"Encrypted with the SSH keys registered to @{GITHUB_ACTOR} on GitHub.\n\n"
        f"```\n{encrypted.strip()}\n```\n\n"
        "Decrypt with:\n"
        "```sh\n"
        "echo '<paste above>' | age -d -i ~/.ssh/your-private-key\n"
        "```\n\n"
        "Store this password securely. It will not be shown again.\n"
        "To rotate, open a new PR using the Credential rotation template."
    )
    post_comment(body)


def _entries(values: dict, role: str) -> dict[str, dict]:
    return (values.get("opa", {}).get("registry", {}) or {}).get(role, {}) or {}


def _pinned_owner(fp_data: dict, role: str, username: str) -> str | None:
    return fp_data.get(role, {}).get(username, {}).get("github_user")


@dataclass
class ChangeSet:
    new:      dict[str, dict]
    rotation: dict[str, dict]
    tampered: list[str]


def detect_changes(
    pr:      dict[str, dict],
    main:    dict[str, dict],
    fp_data: dict,
    role:    str,
) -> ChangeSet:
    new: dict[str, dict] = {}
    rotation: dict[str, dict] = {}
    tampered: list[str] = []

    for username, entry in pr.items():
        has_hash = "password_hash" in entry
        in_main  = username in main

        if not in_main and not has_hash:
            new[username] = entry
        elif in_main and not has_hash:
            if _pinned_owner(fp_data, role, username) == GITHUB_ACTOR:
                rotation[username] = entry
            else:
                tampered.append(f"`{username}` ({role})")

    return ChangeSet(new=new, rotation=rotation, tampered=tampered)


def main() -> None:
    pr_values   = sops_decrypt(VALUES_FILE)
    main_values = decrypt_main_values()
    fp_data     = yaml.safe_load(FINGERPRINTS_FILE.read_text()) or {} \
                  if FINGERPRINTS_FILE.exists() else {}

    stations = detect_changes(
        _entries(pr_values,   "base_stations"),
        _entries(main_values, "base_stations"),
        fp_data, "base_stations",
    )
    rovers = detect_changes(
        _entries(pr_values,   "rovers"),
        _entries(main_values, "rovers"),
        fp_data, "rovers",
    )

    all_tampered = stations.tampered + rovers.tampered
    if all_tampered:
        post_comment(
            "Rotation rejected. The following entries had `password_hash` "
            f"removed but @{GITHUB_ACTOR} is not their registered owner: "
            f"{', '.join(all_tampered)}.\n\n"
            "Credential rotation must be initiated by the original owner."
        )
        sys.exit(1)

    to_process: list[tuple[str, str]] = [
        ("base_stations", u) for u in {**stations.new, **stations.rotation}
    ] + [
        ("rovers", u) for u in {**rovers.new, **rovers.rotation}
    ]

    if not to_process:
        print("No new or rotating entries -- nothing to do.")
        return

    errors: list[str] = []
    results: dict[tuple[str, str], Credential] = {}

    for role, username in to_process:
        cred = generate_credential(username, role, errors)
        if cred:
            results[(role, username)] = cred

    if errors:
        post_comment("\n\n".join(errors))
        if not results:
            sys.exit(1)

    # Write hashes before posting comments. If sops fails, no comment is sent
    # and the workflow fails cleanly without leaving a dangling credential.
    for (role, username), cred in results.items():
        sops_set(VALUES_FILE, "opa", "registry", role, username, "password_hash",
                 value=cred.pw_hash)
        fp_data.setdefault(role, {})[username] = {
            "github_user":      GITHUB_ACTOR,
            "key_fingerprints": cred.fingerprints,
        }

    FINGERPRINTS_FILE.write_text(
        yaml.dump(fp_data, default_flow_style=False, allow_unicode=True)
    )

    # Post credential comments only after hashes are durably stored.
    for (role, username), cred in results.items():
        _post_credential_comment(username, role, cred.encrypted)

    n_stations = sum(1 for role, _ in results if role == "base_stations")
    n_rovers   = sum(1 for role, _ in results if role == "rovers")
    print(f"Done. Issued credentials for {n_stations} base station(s) and {n_rovers} rover(s).")


if __name__ == "__main__":
    main()
