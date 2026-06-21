"""Tests for RTCM session tokens."""

from __future__ import annotations

import time

import jwt
import pytest

from corshub.rtcm.tokens import issue_session_token
from corshub.rtcm.tokens import verify_session_token


# HS256 secrets must be >= 32 bytes (RFC 7518 3.2); pyjwt warns otherwise.
_SECRET = "test-secret-that-is-at-least-32-bytes"


def _issue(ttl: int = 60, mountpoint: str = "BASE1", username: str = "rover1") -> str:
    return issue_session_token(secret=_SECRET, username=username, mountpoint=mountpoint, ttl_seconds=ttl)


class TestSessionTokens:
    def test_issue_then_verify_returns_claims(self) -> None:
        claims = verify_session_token(_issue(mountpoint="BASE7", username="bob"), _SECRET)
        assert claims["mountpoint"] == "BASE7"
        assert claims["sub"] == "bob"
        assert claims["aud"] == "rtcm-udp"

    def test_wrong_secret_rejected(self) -> None:
        with pytest.raises(jwt.InvalidTokenError):
            verify_session_token(_issue(), "a-different-secret-of-sufficient-length")

    def test_expired_token_rejected(self) -> None:
        with pytest.raises(jwt.ExpiredSignatureError):
            verify_session_token(_issue(ttl=-1), _SECRET)

    def test_tampered_token_rejected(self) -> None:
        token = _issue()
        tampered = token[:-2] + ("aa" if token[-2:] != "aa" else "bb")
        with pytest.raises(jwt.InvalidTokenError):
            verify_session_token(tampered, _SECRET)

    def test_wrong_audience_rejected(self) -> None:
        now = int(time.time())
        other = jwt.encode(
            {"sub": "x", "mountpoint": "BASE1", "aud": "something-else", "iat": now, "exp": now + 60},
            _SECRET,
            algorithm="HS256",
        )
        with pytest.raises(jwt.InvalidTokenError):
            verify_session_token(other, _SECRET)

    def test_missing_required_claim_rejected(self) -> None:
        now = int(time.time())
        no_mountpoint = jwt.encode(
            {"sub": "x", "aud": "rtcm-udp", "iat": now, "exp": now + 60},
            _SECRET,
            algorithm="HS256",
        )
        with pytest.raises(jwt.InvalidTokenError):
            verify_session_token(no_mountpoint, _SECRET)
