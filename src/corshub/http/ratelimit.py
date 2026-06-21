"""
Token-bucket rate limiting for authentication endpoints.

`bcrypt` verification is deliberately CPU-heavy, which makes any endpoint that
runs it a denial-of-service amplifier: a flood of auth attempts can saturate the
event loop's executor. This limiter throttles auth attempts per client
fingerprint (source IP) before the bcrypt path is reached.

Each fingerprint gets a bucket of `capacity` tokens, refilled at
`refill_per_second`. One attempt consumes one token; when the bucket is empty the
request is rejected with 429. Buckets live in a bounded TTL cache so cycling
source addresses cannot exhaust memory.
"""

from __future__ import annotations

import time

from dataclasses import dataclass
from typing import TYPE_CHECKING

from cachetools import TTLCache

from corshub import env
from corshub.exceptions.http import RateLimitedError


if TYPE_CHECKING:
    from collections.abc import Callable

    from sanic import Request


@dataclass
class _Bucket:
    tokens: float
    updated: float


class RateLimiter:
    """A token-bucket limiter keyed by an arbitrary fingerprint."""

    def __init__(
        self,
        capacity: float,
        refill_per_second: float,
        *,
        maxsize: int = 100_000,
        idle_ttl: float = 3600.0,
        time_func: Callable[[], float] = time.monotonic,
    ) -> None:
        self._capacity = capacity
        self._refill = refill_per_second
        self._time = time_func
        self._buckets: TTLCache[str, _Bucket] = TTLCache(maxsize=maxsize, ttl=idle_ttl)

    def allow(self, key: str, *, cost: float = 1.0) -> bool:
        """Consume *cost* tokens for *key*; return False if insufficient."""
        now = self._time()
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = _Bucket(tokens=self._capacity, updated=now)
        else:
            elapsed = now - bucket.updated
            bucket.tokens = min(self._capacity, bucket.tokens + elapsed * self._refill)
            bucket.updated = now

        allowed = bucket.tokens >= cost
        if allowed:
            bucket.tokens -= cost

        self._buckets[key] = bucket  # store and refresh idle TTL
        return allowed


def client_fingerprint(request: Request) -> str:
    """Best-effort client identity for rate limiting.

    Honors the proxy configuration (PROXIES_COUNT / REAL_IP_HEADER) via Sanic's
    ``remote_addr``, falling back to the peer address.
    """
    return request.remote_addr or request.ip or "unknown"


def enforce_auth_rate_limit(request: Request) -> None:
    """Charge one auth attempt for the request's client; raise 429 if throttled.

    A no-op when no limiter is configured (``app.ctx.auth_rate_limiter`` is None).
    """
    limiter = getattr(request.app.ctx, "auth_rate_limiter", None)
    if limiter is None:
        return
    if not limiter.allow(client_fingerprint(request)):
        raise RateLimitedError("Too many authentication attempts. Slow down.")


def _flag(key: str, default: str) -> bool:
    return env.extract(key, default=default).strip().lower() in {"1", "true", "yes", "on"}


def initialize(app: object) -> None:
    """Attach an ``auth_rate_limiter`` to ``app.ctx`` from the environment.

    Sets it to None when ``AUTH_RATELIMIT_ENABLED`` is false, in which case
    ``enforce_auth_rate_limit`` becomes a no-op.
    """
    if not _flag("AUTH_RATELIMIT_ENABLED", "true"):
        app.ctx.auth_rate_limiter = None  # type: ignore[attr-defined]
        return

    app.ctx.auth_rate_limiter = RateLimiter(  # type: ignore[attr-defined]
        capacity=env.extract("AUTH_RATELIMIT_CAPACITY", default="5", dtype=float),
        refill_per_second=env.extract("AUTH_RATELIMIT_REFILL_PER_SECOND", default="1.0", dtype=float),
    )
