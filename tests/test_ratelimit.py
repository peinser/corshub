"""Tests for the token-bucket auth rate limiter."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from corshub.exceptions.http import RateLimitedError
from corshub.http.ratelimit import RateLimiter
from corshub.http.ratelimit import client_fingerprint
from corshub.http.ratelimit import enforce_auth_rate_limit


class TestRateLimiter:
    def test_allows_up_to_capacity_then_denies(self) -> None:
        rl = RateLimiter(capacity=3, refill_per_second=0.0)
        assert [rl.allow("ip") for _ in range(3)] == [True, True, True]
        assert rl.allow("ip") is False

    def test_refill_over_time(self) -> None:
        clock = [0.0]
        rl = RateLimiter(capacity=2, refill_per_second=1.0, time_func=lambda: clock[0])
        assert rl.allow("ip") and rl.allow("ip")  # 2 -> 0
        assert rl.allow("ip") is False
        clock[0] = 1.0  # one token refilled
        assert rl.allow("ip") is True
        assert rl.allow("ip") is False

    def test_refill_is_capped_at_capacity(self) -> None:
        clock = [0.0]
        rl = RateLimiter(capacity=2, refill_per_second=1.0, time_func=lambda: clock[0])
        assert rl.allow("ip") and rl.allow("ip")  # drain
        clock[0] = 100.0  # would add 100 tokens, capped at 2
        assert rl.allow("ip") and rl.allow("ip")
        assert rl.allow("ip") is False

    def test_keys_are_independent(self) -> None:
        rl = RateLimiter(capacity=1, refill_per_second=0.0)
        assert rl.allow("a") is True
        assert rl.allow("a") is False
        assert rl.allow("b") is True


class TestEnforce:
    def _request(self, limiter: RateLimiter | None) -> SimpleNamespace:
        ctx = SimpleNamespace(auth_rate_limiter=limiter)
        return SimpleNamespace(app=SimpleNamespace(ctx=ctx), remote_addr="203.0.113.7", ip="203.0.113.7")

    def test_no_limiter_is_noop(self) -> None:
        enforce_auth_rate_limit(self._request(None))  # must not raise

    def test_raises_when_throttled(self) -> None:
        req = self._request(RateLimiter(capacity=1, refill_per_second=0.0))
        enforce_auth_rate_limit(req)  # consumes the single token
        with pytest.raises(RateLimitedError):
            enforce_auth_rate_limit(req)


class TestFingerprint:
    def test_prefers_remote_addr(self) -> None:
        assert client_fingerprint(SimpleNamespace(remote_addr="9.9.9.9", ip="1.1.1.1")) == "9.9.9.9"

    def test_falls_back_to_ip(self) -> None:
        assert client_fingerprint(SimpleNamespace(remote_addr="", ip="1.1.1.1")) == "1.1.1.1"
