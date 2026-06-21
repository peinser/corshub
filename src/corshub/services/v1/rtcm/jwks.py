"""
RTCM signing-key publication — GET /api/v1/rtcm/jwks.json

Serves the Ed25519 signing public key as a standard JWK Set (kty=OKP,
crv=Ed25519). Rovers select the key by the ``kid`` advertised in ``HelloAck``
and verify correction frames against it. Returns 404 when signing is disabled.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sanic import response
from sanic.exceptions import NotFound

from .base import bp


if TYPE_CHECKING:
    from sanic import HTTPResponse
    from sanic import Request


@bp.get("/jwks.json")
async def jwks(request: Request) -> HTTPResponse:
    signing_key = getattr(request.app.ctx, "rtcm_signing_key", None)
    if signing_key is None:
        raise NotFound("RTCM signing is not enabled.")

    return response.json(signing_key.jwks(), content_type="application/jwk-set+json")
