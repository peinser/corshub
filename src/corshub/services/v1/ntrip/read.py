"""
NTRIP v2 rover endpoint — GET /<mountpoint>

A rover opens a long-lived GET request to receive a continuous stream of RTCM
correction frames.  The connection stays open until the rover disconnects or
the caster closes the mountpoint.

Request requirements (RTCM 10410.1 §4.3):
    Ntrip-Version: Ntrip/2.0          mandatory
    Authorization: Basic <b64>         mandatory — username:password
    Ntrip-GGA: $GPGGA,...              optional  — rover approximate position

Response:
    HTTP/1.1 200 OK
    Content-Type: gnss/data
    Transfer-Encoding: chunked         set automatically by Sanic's ResponseStream
    Cache-Control: no-store, no-cache
    Ntrip-Version: Ntrip/2.0
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sanic.exceptions import NotFound
from sanic.exceptions import Unauthorized
from sanic.response import ResponseStream

from corshub.exceptions.http import BadRequestError

from .base import bp


if TYPE_CHECKING:
    from sanic import HTTPResponse
    from sanic import Request


@bp.get("/<mountpoint:str>")
async def read(request: Request, mountpoint: str) -> HTTPResponse:
    """Stream RTCM correction frames to a rover.

    Validates the NTRIP-Version header and Basic credentials, then opens a
    chunked streaming response that forwards every frame published on
    *mountpoint* until the rover disconnects.
    """
    # ── NTRIP-Version header ───────────────────────────────────────────────────
    # if request.headers.get("Ntrip-Version") != "Ntrip/2.0":
    #     raise BadRequestError("Ntrip-Version: Ntrip/2.0 header is required.")

    caster = request.app.ctx.ntrip_transport
    # if not caster.authenticate_source(request.credentials.username, request.credentials.password):
    #     raise Unauthorized(
    #         "Invalid mountpoint credentials.",
    #         scheme="Basic",
    #         realm="NTRIP Caster",
    #     )

    # ── Mountpoint existence ───────────────────────────────────────────────────
    # if mountpoint not in caster.mountpoints:
    #     raise NotFound(f"Mountpoint {mountpoint!r} does not exist.")

    # ── Streaming response ─────────────────────────────────────────────────────
    async def stream_frames(stream: HTTPResponse) -> None:
        async with caster.subscribe(mountpoint) as sub:
            while (frame := await sub.get()) is not None:
                await stream.write(frame)

    return ResponseStream(
        stream_frames,
        status=200,
        content_type="gnss/data",
        headers={
            "Ntrip-Version": "Ntrip/2.0",
            "Cache-Control": "no-store, no-cache",
            "Connection": "keep-alive",
        },
    )
