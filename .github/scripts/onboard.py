#!/usr/bin/env python3
"""
Onboarding bot: generates NTRIP credentials for new and rotating entries
in ops/values.yaml.

The PR's ops/values.yaml is untrusted input and is only ever *read*. It is
validated against main's file as parsed YAML (never as a text diff):
  * nothing outside opa.registry.base_stations / opa.registry.rovers may change;
  * existing entries may not change, except an owner removing its password_hash
    to rotate;
  * new entries must match a strict schema (fields, username, mountpoint, dates).
The bot then writes only the validated fields into main's own ops/values.yaml,
so nothing else from the PR can reach main or the deployment.

Onboarding: new entry without password_hash:
  1. Fetches the PR author's SSH public keys from GitHub.
  2. Generates a cryptographically random password and bcrypt-hashes it.
  3. age-encrypts the plaintext password to the author's SSH keys.
  4. Writes the validated entry plus hash into ops/values.yaml via sops set.
  5. Posts the encrypted credential as a PR comment.
  6. Records the SSH key fingerprints in ops/key-fingerprints.yaml.

Rotation: existing entry with password_hash removed by its original owner.
  Same as onboarding, but verifies that the PR author matches the GitHub user
  pinned in ops/key-fingerprints.yaml for that entry before re-issuing.

Environment (all set by onboard.yml):
  SOPS_AGE_KEY        AGE private key, passed through to sops transparently.
  GH_TOKEN            GitHub token with contents:write + pull-requests:write.
  GITHUB_REPOSITORY   owner/repo.
  PR_NUMBER           Pull request number.
  PR_AUTHOR           GitHub login of the PR author. Not github.actor: that is
                      whoever triggered the run, e.g. a maintainer re-running it.
  PR_VALUES_FILE      Path of the PR's ops/values.yaml, extracted as data.
  PENDING_COMMENTS_FILE  Where `issue` leaves the credential comments for `announce`.

Usage (two steps, so a credential is only shown once it is stored on main):
  onboard.py issue      validate, write ops/values.yaml + fingerprints, save comments
  onboard.py announce   post the saved credential comments (run after git push)
"""

from __future__ import annotations

import copy
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

from collections import Counter
from dataclasses import dataclass
from dataclasses import field
from datetime import date
from datetime import datetime
from pathlib import Path
from typing import Any

import bcrypt
import yaml


def _no_duplicate_mapping(
    loader: yaml.SafeLoader, node: yaml.MappingNode
) -> dict[str, Any]:
    loader.flatten_mapping(node)
    pairs = loader.construct_pairs(node)
    seen: set[str] = set()
    for key, _ in pairs:
        if key in seen:
            raise yaml.YAMLError(f"duplicate key: {key!r}")
        seen.add(key)
    return dict(pairs)


class _StrictLoader(yaml.SafeLoader):
    pass


_StrictLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _no_duplicate_mapping,
)


VALUES_FILE       = Path("ops/values.yaml")              # main's file; the only one written
FINGERPRINTS_FILE = Path("ops/key-fingerprints.yaml")
PR_VALUES_FILE    = Path(os.environ["PR_VALUES_FILE"])  # untrusted, read-only
PENDING_COMMENTS_FILE = Path(os.environ["PENDING_COMMENTS_FILE"])

PR_NUMBER = os.environ["PR_NUMBER"]
REPO      = os.environ["GITHUB_REPOSITORY"]
GH_TOKEN  = os.environ["GH_TOKEN"]
PR_AUTHOR = os.environ["PR_AUTHOR"]

SUBPROCESS_TIMEOUT = 30  # seconds

ROLES = ("base_stations", "rovers")
ALLOWED_FIELDS = {
    "base_stations": {"mountpoint", "valid_from", "valid_until"},
    "rovers":        {"mountpoints", "valid_from", "valid_until"},
}
# Usernames are echoed into bot comments and used as Basic-auth user ids:
# no markup, no whitespace, no ':'.
USERNAME_RE   = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
# Same rule as the caster's Mountpoint name.
MOUNTPOINT_RE = re.compile(r"[A-Za-z0-9_-]{1,100}")
# Caster routes that would shadow a mountpoint of the same name, and the caster's
# built-in synthetic debug mountpoint.
RESERVED_MOUNTPOINTS = {"NEAR", "NEAREST", "NSB", "SYNTHETIC"}


def sops_decrypt(path: Path, *, strict: bool = False) -> dict[str, Any]:
    result = subprocess.run(
        ["sops", "-d", "--output-type", "yaml", str(path)],
        capture_output=True, text=True, check=True,
        timeout=SUBPROCESS_TIMEOUT,
    )
    loader = _StrictLoader if strict else yaml.SafeLoader
    return yaml.load(result.stdout, Loader=loader) or {}


def sops_set(path: Path, *keys: str, value: Any) -> None:
    """Write *value* (a scalar or a whole entry) into a SOPS-managed YAML file in-place.

    sops re-encrypts the tree, so a nested password_hash is encrypted by the
    file's encrypted_regex.
    """
    # json.dumps escapes any special characters in key names.
    json_path = "".join(f"[{json.dumps(k)}]" for k in keys)
    subprocess.run(
        ["sops", "--set", f"{json_path} {json.dumps(value)}", str(path)],
        check=True, timeout=SUBPROCESS_TIMEOUT,
    )


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


def _github_post(path: str, payload: dict[str, Any]) -> Any:
    url = f"https://api.github.com{path}"
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {GH_TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=SUBPROCESS_TIMEOUT) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"GitHub API {url} returned {exc.code}: {exc.read().decode()}"
        ) from exc


def post_comment(body: str) -> None:
    _github_post(f"/repos/{REPO}/issues/{PR_NUMBER}/comments", {"body": body})


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
            ["age", "-a", "-R", f.name],
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
    Fetch the PR author's SSH keys, generate a random password, bcrypt-hash
    it, and age-encrypt the plaintext. Returns a Credential on success or
    appends to errors and returns None on failure.

    Does NOT write to disk or post any comment. All side effects are left
    to the caller so they occur only after the hash is durably stored.
    """
    try:
        ssh_keys = fetch_ssh_keys(PR_AUTHOR)
    except (RuntimeError, OSError) as exc:
        errors.append(
            f"[{username}] ({role}): could not fetch SSH keys for "
            f"@{PR_AUTHOR}: {exc}"
        )
        return None

    age_keys = [k for k in ssh_keys if k.startswith(("ssh-ed25519", "ssh-rsa"))]
    if not age_keys:
        errors.append(
            f"[{username}] ({role}): no ed25519 or RSA SSH keys found for "
            f"@{PR_AUTHOR}. Add one at https://github.com/settings/keys."
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


def _credential_comment(
    username: str, role: str, encrypted: str, fingerprints: list[str]
) -> str:
    fp_lines = "\n".join(f"- `{fp}`" for fp in fingerprints)
    decrypt_cmd = (
        "age -d -i ~/.ssh/your-private-key <<'EOF'\n"
        f"{encrypted.strip()}\n"
        "EOF"
    )
    body = (
        f"Credentials issued for `{username}` ({role})\n\n"
        f"Encrypted to the following SSH keys registered to @{PR_AUTHOR}:\n\n"
        f"{fp_lines}\n\n"
        "Run the following to decrypt (replace `~/.ssh/your-private-key` with the "
        "matching private key):\n\n"
        f"```sh\n{decrypt_cmd}\n```\n\n"
        "Store this password securely. It will not be shown again.\n"
        "To rotate, open a new PR using the Credential rotation template."
    )
    return body


def _pinned_owner(fp_data: dict, role: str, username: str) -> str | None:
    return fp_data.get(role, {}).get(username, {}).get("github_user")


# -- validation ----------------------------------------------------------------
#
# Error messages are posted as PR comments under the bot's name, so they never
# echo a value that failed validation (it could carry markdown, links or mentions).


def _role_entries(values: dict[str, Any], role: str) -> Any:
    """opa.registry.<role>, an empty mapping when absent, or whatever non-mapping is there."""
    opa = values.get("opa")
    registry = opa.get("registry") if isinstance(opa, dict) else None
    entries = registry.get(role) if isinstance(registry, dict) else None
    return {} if entries is None else entries


def _without_entries(values: dict[str, Any]) -> dict[str, Any]:
    """*values* minus the two registry maps the bot manages."""
    stripped = copy.deepcopy(values)
    opa = stripped.get("opa")
    registry = opa.get("registry") if isinstance(opa, dict) else None
    if isinstance(registry, dict):
        for role in ROLES:
            registry.pop(role, None)
    return stripped


def _as_date(value: Any) -> str | None:
    """Return *value* as a 'YYYY-MM-DD' string, or None if it is not a calendar date.

    An unquoted date parses to datetime.date; the OPA policy needs the string.
    """
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError:
            return None
    return None


def _valid_mountpoint(value: Any) -> bool:
    return (
        isinstance(value, str)
        and MOUNTPOINT_RE.fullmatch(value) is not None
        and value.upper() not in RESERVED_MOUNTPOINTS
    )


def _check_new_entry(role: str, username: str, entry: Any) -> tuple[dict[str, Any] | None, list[str]]:
    """Validate a new registry entry; return (clean entry with only allowed fields, errors)."""
    label = f"`{username}` ({role})"
    if not isinstance(entry, dict):
        return None, [f"{label}: the entry must be a mapping."]

    errors: list[str] = []
    if "password_hash" in entry:
        errors.append(f"{label}: do not add `password_hash`; the bot generates it.")
    elif set(entry) - ALLOWED_FIELDS[role]:
        allowed = ", ".join(f"`{name}`" for name in sorted(ALLOWED_FIELDS[role]))
        errors.append(f"{label}: only these fields are allowed: {allowed}.")

    clean: dict[str, Any] = {}
    for name in ("valid_from", "valid_until"):
        if name in entry:
            parsed = _as_date(entry[name])
            if parsed is None:
                errors.append(f"{label}: `{name}` must be a date written as YYYY-MM-DD.")
            else:
                clean[name] = parsed
    if "valid_from" in clean and "valid_until" in clean and clean["valid_until"] <= clean["valid_from"]:
        errors.append(f"{label}: `valid_until` must be after `valid_from`.")

    if role == "rovers":
        mountpoints = entry.get("mountpoints")
        if (
            not isinstance(mountpoints, list)
            or not mountpoints
            or not all(mp == "*" or _valid_mountpoint(mp) for mp in mountpoints)
        ):
            errors.append(
                f"{label}: `mountpoints` must be a non-empty list of mountpoint names "
                "(letters, digits, `_`, `-`) or `'*'`."
            )
        else:
            clean["mountpoints"] = list(dict.fromkeys(mountpoints))
    else:
        mountpoint = entry.get("mountpoint")
        if not _valid_mountpoint(mountpoint):
            errors.append(
                f"{label}: `mountpoint` must be 1-100 letters, digits, `_` or `-`, "
                "and not a reserved name (NEAR, NEAREST, NSB, SYNTHETIC)."
            )
        else:
            clean["mountpoint"] = mountpoint

    return (None if errors else clean), errors


@dataclass
class Plan:
    new:      dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    rotation: list[tuple[str, str]] = field(default_factory=list)


def plan_changes(
    pr_values:   dict[str, Any],
    main_values: dict[str, Any],
    fp_data:     dict,
) -> tuple[Plan, list[str]]:
    """Compare the PR's values with main's; return what to issue, or why the PR is rejected."""
    plan = Plan()
    errors: list[str] = []

    if _without_entries(pr_values) != _without_entries(main_values):
        errors.append(
            "Only new entries under `opa.registry.base_stations` and `opa.registry.rovers` "
            "may be added. This PR also changes other parts of `ops/values.yaml`."
        )

    for role in ROLES:
        main_entries = _role_entries(main_values, role)
        pr_entries   = _role_entries(pr_values, role)
        if not isinstance(pr_entries, dict):
            errors.append(f"`opa.registry.{role}` must be a mapping of usernames to entries.")
            continue

        for username, main_entry in main_entries.items():
            pr_entry = pr_entries.get(username)
            if pr_entry == main_entry:
                continue
            hash_removed = (
                "password_hash" in main_entry
                and pr_entry == {k: v for k, v in main_entry.items() if k != "password_hash"}
            )
            if not hash_removed:
                errors.append(f"`{username}` ({role}): existing entries cannot be changed or removed.")
            elif _pinned_owner(fp_data, role, username) != PR_AUTHOR:
                errors.append(f"`{username}` ({role}): only its registered owner can rotate this credential.")
            else:
                plan.rotation.append((role, username))

        for username, entry in pr_entries.items():
            if username in main_entries:
                continue
            if not isinstance(username, str) or USERNAME_RE.fullmatch(username) is None:
                errors.append(
                    f"A new entry under `{role}` has an invalid username: use 1-64 letters, "
                    "digits, `.`, `_` or `-`, starting with a letter or digit."
                )
                continue
            clean, entry_errors = _check_new_entry(role, username, entry)
            errors.extend(entry_errors)
            if clean is not None:
                plan.new[(role, username)] = clean

    # A new base station must not claim a mountpoint another station publishes to:
    # its credentials would let it push forged corrections to that mountpoint's rovers.
    claims = Counter(entry.get("mountpoint") for entry in _role_entries(main_values, "base_stations").values())
    claims.update(clean["mountpoint"] for (role, _), clean in plan.new.items() if role == "base_stations")
    for (role, username), clean in plan.new.items():
        if role == "base_stations" and claims[clean["mountpoint"]] > 1:
            errors.append(
                f"`{username}` (base_stations): mountpoint `{clean['mountpoint']}` "
                "is already registered to another station."
            )

    return plan, errors


def issue() -> None:
    try:
        pr_values = sops_decrypt(PR_VALUES_FILE, strict=True)
    except yaml.YAMLError as exc:
        post_comment(
            f"ops/values.yaml contains duplicate keys: {exc}\n\n"
            "Each username must appear at most once under each role."
        )
        sys.exit(1)
    except subprocess.CalledProcessError:
        post_comment(
            "ops/values.yaml in this PR could not be decrypted. Only add plain-text "
            "fields; never edit `ENC[...]` values or the `sops:` section."
        )
        sys.exit(1)

    # main's file is the base checkout; a failure here is fatal (wrong key or a
    # corrupted file), never something to paper over.
    main_values = sops_decrypt(VALUES_FILE)
    fp_data     = yaml.safe_load(FINGERPRINTS_FILE.read_text()) or {} \
                  if FINGERPRINTS_FILE.exists() else {}

    plan, problems = plan_changes(pr_values, main_values, fp_data)
    if problems:
        post_comment(
            "Registration rejected:\n\n"
            + "\n".join(f"- {problem}" for problem in problems)
            + "\n\nFix the PR and push again."
        )
        sys.exit(1)

    to_process = [*plan.new, *plan.rotation]
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

    # Write hashes before anything is announced. If sops fails, the workflow
    # fails cleanly without leaving a dangling credential.
    for (role, username), cred in results.items():
        if (role, username) in plan.new:
            # The whole entry is rebuilt from validated fields only.
            sops_set(VALUES_FILE, "opa", "registry", role, username,
                     value={**plan.new[(role, username)], "password_hash": cred.pw_hash})
        else:
            sops_set(VALUES_FILE, "opa", "registry", role, username, "password_hash",
                     value=cred.pw_hash)
        fp_data.setdefault(role, {})[username] = {
            "github_user":      PR_AUTHOR,
            "key_fingerprints": cred.fingerprints,
        }

    FINGERPRINTS_FILE.write_text(
        yaml.dump(fp_data, default_flow_style=False, allow_unicode=True)
    )

    # Not posted here: `announce` posts them once the commit is on main, so a
    # failed push never leaves the user holding a password that was not stored.
    PENDING_COMMENTS_FILE.write_text(json.dumps([
        _credential_comment(username, role, cred.encrypted, cred.fingerprints)
        for (role, username), cred in results.items()
    ]))

    n_stations = sum(1 for role, _ in results if role == "base_stations")
    n_rovers   = sum(1 for role, _ in results if role == "rovers")
    print(f"Done. Issued credentials for {n_stations} base station(s) and {n_rovers} rover(s).")


def announce() -> None:
    if not PENDING_COMMENTS_FILE.exists():
        return  # nothing was issued
    for body in json.loads(PENDING_COMMENTS_FILE.read_text()):
        post_comment(body)


COMMANDS = {"issue": issue, "announce": announce}

if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in COMMANDS:
        sys.exit("usage: onboard.py issue|announce")
    COMMANDS[sys.argv[1]]()
