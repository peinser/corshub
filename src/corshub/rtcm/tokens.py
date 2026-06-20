"""
Short-lived session tokens for the RTCM UDP handshake.

Authentication (Basic -> OPA -> bcrypt) happens once over HTTPS at the bootstrap
endpoint, which mints one of these tokens. The rover then presents the token in
its UDP ``Hello``; the UDP server verifies it cheaply (HMAC, constant work) with
no bcrypt and no database lookup, keeping the unauthenticated UDP path free of
DoS-amplifying work.

The token binds the authorized mountpoint and a short expiry. It is intentionally
*not* a full identity token — it authorizes one correction session, nothing else.
"""

from __future__ import annotations

import time

from typing import Any

import jwt


_ALGORITHM = "HS256"
_AUDIENCE = "rtcm-udp"


def issue_session_token(
    *,
    secret: str,
    username: str,
    mountpoint: str,
    ttl_seconds: int,
) -> str:
    """Mint a signed session token authorizing *username* on *mountpoint*."""
    now = int(time.time())
    claims = {
        "sub": username,
        "mountpoint": mountpoint,
        "aud": _AUDIENCE,
        "iat": now,
        "exp": now + ttl_seconds,
    }
    return jwt.encode(claims, secret, algorithm=_ALGORITHM)


def verify_session_token(token: str, secret: str) -> dict[str, Any]:
    """Verify a session token and return its claims.

    Raises ``jwt.InvalidTokenError`` (or a subclass) on any failure: bad
    signature, wrong audience, expiry, or a missing required claim.
    """
    return jwt.decode(
        token,
        secret,
        algorithms=[_ALGORITHM],
        audience=_AUDIENCE,
        options={"require": ["exp", "mountpoint", "sub"]},
    )
