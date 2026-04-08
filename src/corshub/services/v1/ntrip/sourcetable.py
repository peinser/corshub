"""
NTRIP v2 source table endpoint — GET /

Rovers and NTRIP clients fetch this to discover available mountpoints before
opening a correction stream.

Request requirements (RTCM 10410.1 §4.3):
    Ntrip-Version: Ntrip/2.0          mandatory

Response:
    HTTP/1.1 200 OK
    Content-Type: gnss/sourcetable
    Cache-Control: no-store, no-cache
    Ntrip-Version: Ntrip/2.0

    STR;<name>;...\\r\\n
    ...
    ENDSOURCETABLE\\r\\n
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sanic import response

from corshub.ntrip.v2.headers import NTRIP_VERSION_2
from corshub.ntrip.v2.sourcetable import format_sourcetable

from .base import bp


if TYPE_CHECKING:
    from sanic import HTTPResponse
    from sanic import Request


@bp.get("/")
async def sourcetable(request: Request) -> HTTPResponse:
    """Return the NTRIP source table listing all registered mountpoints."""
    caster = request.app.ctx.ntrip_caster
    body = format_sourcetable(caster)

    return response.text(
        body,
        status=200,
        content_type="gnss/sourcetable",
        headers={
            "Ntrip-Version": NTRIP_VERSION_2,
            "Cache-Control": "no-store, no-cache",
        },
    )
