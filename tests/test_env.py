"""Boolean environment flags."""

from __future__ import annotations

import pytest

from corshub import env


@pytest.mark.parametrize(
    ("value", "expected"), [("1", True), ("TRUE", True), (" on ", True), ("0", False), ("no", False)]
)
def test_flag_values(monkeypatch: pytest.MonkeyPatch, value: str, expected: bool) -> None:
    monkeypatch.setenv("CORSHUB_TEST_FLAG", value)
    assert env.flag("CORSHUB_TEST_FLAG") is expected


@pytest.mark.parametrize("default", [True, False])
def test_unset_or_empty_uses_default(monkeypatch: pytest.MonkeyPatch, default: bool) -> None:
    monkeypatch.delenv("CORSHUB_TEST_FLAG", raising=False)
    assert env.flag("CORSHUB_TEST_FLAG", default=default) is default
    monkeypatch.setenv("CORSHUB_TEST_FLAG", "")
    assert env.flag("CORSHUB_TEST_FLAG", default=default) is default
