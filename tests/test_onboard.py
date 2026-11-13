"""The onboarding bot must not issue credentials that hijack a mountpoint.

Loads the real ``.github/scripts/onboard.py`` with sops, git and the GitHub API
stubbed out, then drives ``main()`` with a decrypted ``ops/values.yaml``.
"""

from __future__ import annotations

import importlib.util
import sys

from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


_SCRIPT = Path(__file__).resolve().parents[1] / ".github" / "scripts" / "onboard.py"
_MAIN_VALUES = {"opa": {"registry": {"base_stations": {"legit": {"mountpoint": "MP1", "password_hash": "h"}}}}}


class _CredentialIssuedError(Exception):
    pass


def _load(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    for key, value in {
        "PR_NUMBER": "1",
        "GITHUB_REPOSITORY": "o/r",
        "GH_TOKEN": "t",
        "GITHUB_ACTOR": "mallory",
    }.items():
        monkeypatch.setenv(key, value)
    spec = importlib.util.spec_from_file_location("onboard", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "onboard", module)  # dataclasses resolve the module by name
    spec.loader.exec_module(module)
    return module


def _run(monkeypatch: pytest.MonkeyPatch, new_station: dict[str, Any], comments: list[str]) -> None:
    """Run main() for a PR adding *new_station* as `newcomer`, collecting posted comments."""
    onboard = _load(monkeypatch)
    pr_values = {"opa": {"registry": {"base_stations": {**_MAIN_VALUES["opa"]["registry"]["base_stations"]}}}}
    pr_values["opa"]["registry"]["base_stations"]["newcomer"] = new_station

    def issue(*_: object) -> None:
        raise _CredentialIssuedError

    monkeypatch.setattr(onboard, "sops_decrypt", lambda *_, **__: pr_values)
    monkeypatch.setattr(onboard, "decrypt_main_values", lambda: _MAIN_VALUES)
    monkeypatch.setattr(onboard, "post_comment", comments.append)
    monkeypatch.setattr(onboard, "generate_credential", issue)
    onboard.main()


def test_new_station_claiming_existing_mountpoint_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    comments: list[str] = []
    with pytest.raises(SystemExit) as exit_info:
        _run(monkeypatch, {"mountpoint": "MP1"}, comments)
    assert exit_info.value.code == 1
    assert len(comments) == 1
    assert "`newcomer` (mountpoint `MP1`)" in comments[0]


def test_new_station_with_free_mountpoint_gets_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(_CredentialIssuedError):
        _run(monkeypatch, {"mountpoint": "MP2"}, [])
