"""
RTCM UDP session bootstrap - POST /api/v1/rtcm/session

Authenticates a rover with HTTP Basic (the same OPA + bcrypt path as the NTRIP
routes) and mints a short-lived session token. The rover presents that token in
its UDP ``Hello``; no bcrypt runs on the UDP path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sanic import response
from sanic.exceptions import NotFound
from sanic.exceptions import Unauthorized

from corshub.exceptions.http import BadRequestError
from corshub.http.ratelimit import enforce_auth_rate_limit
from corshub.rtcm.tokens import issue_session_token

from .base import bp


if TYPE_CHECKING:
    from sanic import HTTPResponse
    from sanic import Request


@bp.post("/session")
async def create_session(request: Request) -> HTTPResponse:
    config = request.app.ctx.rtcm_config
    if not config.enabled:
        raise NotFound("RTCM UDP egress is not enabled.")

    if not request.credentials:
        raise Unauthorized("Basic credentials required.", scheme="Basic")

    try:
        body = request.json or {}
    except Exception as ex:
        raise BadRequestError("Request body must be JSON.") from ex

    mountpoint = body.get("mountpoint")
    if not mountpoint or not isinstance(mountpoint, str):
        raise BadRequestError("Field 'mountpoint' (string) is required.")

    # Throttle auth attempts per client before the bcrypt path.
    enforce_auth_rate_limit(request)

    caster = request.app.ctx.ntrip_caster
    allowed, _ = await caster.authenticate_rover(
        request.credentials.username,
        request.credentials.password,
        mountpoint,
    )
    if not allowed:
        raise Unauthorized("Invalid credentials.", scheme="Basic")

    token = issue_session_token(
        secret=config.token_secret,
        username=request.credentials.username,
        mountpoint=mountpoint,
        ttl_seconds=config.token_ttl,
    )

    signing_key = getattr(request.app.ctx, "rtcm_signing_key", None)
    return response.json(
        {
            "token": token,
            "udp_endpoint": config.udp_endpoint,
            "signing_kid": signing_key.kid if signing_key is not None else None,
        }
    )
