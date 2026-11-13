"""The onboarding bot must only ever add validated registry entries.

Loads the real ``.github/scripts/onboard.py`` with sops, git and the GitHub API
stubbed out, then drives ``issue()`` with decrypted ``ops/values.yaml`` trees:
main's (trusted) and the PR's (untrusted).
"""

from __future__ import annotations

import copy
import datetime
import importlib.util
import sys

from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


_SCRIPT = Path(__file__).resolve().parents[1] / ".github" / "scripts" / "onboard.py"
_PR_FILE = Path("/pr/values.yaml")
_MAIN: dict[str, Any] = {
    "image": {"tag": "1"},
    "opa": {
        "enabled": True,
        "registry": {
            "base_stations": {"legit": {"mountpoint": "MP1", "password_hash": "h1"}},
            "rovers": {"owned": {"mountpoints": ["*"], "password_hash": "h2"}},
        },
    },
}
_FINGERPRINTS = "rovers:\n  owned:\n    github_user: alice\n    key_fingerprints: []\n"


class _Bot:
    """The loaded script plus everything it tried to do."""

    def __init__(self, module: ModuleType) -> None:
        self.module = module
        self.comments: list[str] = []
        self.writes: list[tuple[tuple[str, ...], Any]] = []


def _load(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, author: str) -> _Bot:
    env = {
        "PR_NUMBER": "1",
        "GITHUB_REPOSITORY": "o/r",
        "GH_TOKEN": "t",
        "PR_AUTHOR": author,
        "PR_VALUES_FILE": str(_PR_FILE),
        "PENDING_COMMENTS_FILE": str(tmp_path / "pending-comments.json"),
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    spec = importlib.util.spec_from_file_location("onboard", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "onboard", module)  # dataclasses resolve the module by name
    spec.loader.exec_module(module)

    bot = _Bot(module)
    fingerprints = tmp_path / "key-fingerprints.yaml"
    fingerprints.write_text(_FINGERPRINTS)
    monkeypatch.setattr(module, "FINGERPRINTS_FILE", fingerprints)
    monkeypatch.setattr(module, "post_comment", bot.comments.append)
    monkeypatch.setattr(module, "sops_set", lambda path, *keys, value: bot.writes.append((keys, value)))
    monkeypatch.setattr(
        module,
        "generate_credential",
        lambda *_: module.Credential(pw_hash="NEWHASH", encrypted="AGE", fingerprints=[]),
    )
    return bot


def _run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, pr: dict[str, Any], author: str = "mallory") -> _Bot:
    bot = _load(monkeypatch, tmp_path, author)
    trees = {_PR_FILE: pr, bot.module.VALUES_FILE: copy.deepcopy(_MAIN)}
    monkeypatch.setattr(bot.module, "sops_decrypt", lambda path, **_: copy.deepcopy(trees[path]))
    try:
        bot.module.issue()
    except SystemExit as exc:
        assert exc.code == 1
    return bot


def _pr(role: str | None = None, username: str = "", entry: Any = None) -> dict[str, Any]:
    pr = copy.deepcopy(_MAIN)
    if role is not None:
        pr["opa"]["registry"][role][username] = entry
    return pr


def _rejected(bot: _Bot, text: str) -> None:
    assert bot.writes == [], "nothing may be written for a rejected PR"
    assert len(bot.comments) == 1
    assert bot.comments[0].startswith("Registration rejected")
    assert text in bot.comments[0]


class TestRejected:
    def test_config_injection_outside_registry(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        pr = _pr("rovers", "newbie", {"mountpoints": ["*"]})
        pr["extraEnv"] = [{"name": "OPA_URL", "value": "http://attacker:8181"}]
        _rejected(_run(monkeypatch, tmp_path, pr), "other parts of `ops/values.yaml`")

    def test_change_inside_opa_but_outside_registry(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        pr = _pr()
        pr["opa"]["enabled"] = False
        _rejected(_run(monkeypatch, tmp_path, pr), "other parts of `ops/values.yaml`")

    def test_mountpoint_hijack(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        bot = _run(monkeypatch, tmp_path, _pr("base_stations", "evil", {"mountpoint": "MP1"}))
        _rejected(bot, "mountpoint `MP1` is already registered")

    def test_modifying_an_existing_entry(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        pr = _pr()
        pr["opa"]["registry"]["rovers"]["owned"]["mountpoints"] = ["MP1"]
        _rejected(_run(monkeypatch, tmp_path, pr), "existing entries cannot be changed or removed")

    def test_removing_an_existing_entry(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        pr = _pr()
        del pr["opa"]["registry"]["base_stations"]["legit"]
        _rejected(_run(monkeypatch, tmp_path, pr), "existing entries cannot be changed or removed")

    def test_rotation_by_someone_else(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        pr = _pr()
        del pr["opa"]["registry"]["rovers"]["owned"]["password_hash"]
        _rejected(_run(monkeypatch, tmp_path, pr, author="mallory"), "only its registered owner")

    def test_self_supplied_password_hash(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        bot = _run(monkeypatch, tmp_path, _pr("rovers", "newbie", {"mountpoints": ["*"], "password_hash": "x"}))
        _rejected(bot, "do not add `password_hash`")

    def test_unknown_field(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        bot = _run(monkeypatch, tmp_path, _pr("rovers", "newbie", {"mountpoints": ["*"], "max_session_seconds": 0}))
        _rejected(bot, "only these fields are allowed")

    def test_station_cannot_declare_itself_verified(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        bot = _run(monkeypatch, tmp_path, _pr("base_stations", "newstation", {"mountpoint": "MP2", "verified": True}))
        _rejected(bot, "only these fields are allowed")

    def test_invalid_username_is_not_echoed(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        username = "x [click](https://evil.example) @someone"
        bot = _run(monkeypatch, tmp_path, _pr("rovers", username, {"mountpoints": ["*"]}))
        _rejected(bot, "invalid username")
        assert "evil.example" not in bot.comments[0]
        assert "@someone" not in bot.comments[0]

    @pytest.mark.parametrize("mountpoint", ["NEAREST", "nsb", "a/b", "", 7])
    def test_invalid_or_reserved_mountpoint(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mountpoint: Any
    ) -> None:
        bot = _run(monkeypatch, tmp_path, _pr("base_stations", "newstation", {"mountpoint": mountpoint}))
        _rejected(bot, "`mountpoint` must be")

    @pytest.mark.parametrize("mountpoints", [[], "*", ["ok", "bad name"], None])
    def test_invalid_rover_mountpoints(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mountpoints: Any) -> None:
        bot = _run(monkeypatch, tmp_path, _pr("rovers", "newbie", {"mountpoints": mountpoints}))
        _rejected(bot, "`mountpoints` must be")

    @pytest.mark.parametrize(
        "dates",
        [
            {"valid_until": "2027-02-30"},
            {"valid_until": "soon"},
            {"valid_from": "2027-01-01", "valid_until": "2026-01-01"},
        ],
    )
    def test_invalid_dates(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, dates: dict[str, str]) -> None:
        bot = _run(monkeypatch, tmp_path, _pr("rovers", "newbie", {"mountpoints": ["*"], **dates}))
        _rejected(bot, "valid_")


class TestAccepted:
    def test_new_rover_written_from_validated_fields_only(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        entry = {"mountpoints": ["*", "*"], "valid_from": datetime.date(2026, 11, 13), "valid_until": "2027-11-13"}
        bot = _run(monkeypatch, tmp_path, _pr("rovers", "newbie", entry))
        assert bot.writes == [
            (
                ("opa", "registry", "rovers", "newbie"),
                {
                    "mountpoints": ["*"],
                    "valid_from": "2026-11-13",  # an unquoted YAML date is written back as a string
                    "valid_until": "2027-11-13",
                    "password_hash": "NEWHASH",
                },
            )
        ]
        assert "newbie" in bot.module.FINGERPRINTS_FILE.read_text()

    def test_credentials_are_only_posted_by_announce(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        bot = _run(monkeypatch, tmp_path, _pr("rovers", "newbie", {"mountpoints": ["*"]}))
        # The workflow pushes between these two steps; a failed push never announces.
        assert bot.comments == []
        bot.module.announce()
        assert len(bot.comments) == 1
        assert bot.comments[0].startswith("Credentials issued for `newbie` (rovers)")

    def test_announce_without_issued_credentials_posts_nothing(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        bot = _load(monkeypatch, tmp_path, "mallory")
        bot.module.announce()
        assert bot.comments == []

    def test_new_base_station_with_free_mountpoint(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        bot = _run(monkeypatch, tmp_path, _pr("base_stations", "newstation", {"mountpoint": "MP2"}))
        assert bot.writes == [
            (("opa", "registry", "base_stations", "newstation"), {"mountpoint": "MP2", "password_hash": "NEWHASH"})
        ]

    def test_rotation_by_owner_only_replaces_the_hash(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        pr = _pr()
        del pr["opa"]["registry"]["rovers"]["owned"]["password_hash"]
        bot = _run(monkeypatch, tmp_path, pr, author="alice")
        assert bot.writes == [(("opa", "registry", "rovers", "owned", "password_hash"), "NEWHASH")]
