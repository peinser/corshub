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
from corshub.ntrip.v2.headers import CONTENT_TYPE_GNSS
from corshub.ntrip.v2.headers import NTRIP_VERSION
from corshub.ntrip.v2.headers import NTRIP_VERSION_2

from .base import bp


if TYPE_CHECKING:
    from sanic import HTTPResponse
    from sanic import Request


@bp.get("/<mountpoint_id:str>")
async def read(request: Request, mountpoint_id: str) -> HTTPResponse:
    """Stream RTCM correction frames to a rover.

    Validates the Ntrip-Version header and Basic credentials, then opens a
    chunked streaming response that forwards every frame published on
    *mountpoint_id* until the rover disconnects.
    """
    if request.headers.get(NTRIP_VERSION, "").lower() != NTRIP_VERSION_2.lower():
        raise BadRequestError(f"{NTRIP_VERSION}: {NTRIP_VERSION_2} header is required.")

    if not request.credentials:
        raise Unauthorized("Basic credentials required.", scheme="Basic")

    caster = request.app.ctx.ntrip_caster

    mountpoint = caster.mountpoints.get(mountpoint_id)
    if not mountpoint:
        raise NotFound(f"Mountpoint {mountpoint_id!r} does not exist.")

    # TODO: use a separate rover user table; for now rovers share the mountpoint credentials.
    if not caster.authenticate_source(request.credentials.username, request.credentials.password):
        raise Unauthorized("Invalid credentials.", scheme="Basic")

    # Check if NMEA validation needs to be done (GGA header by the rover needs to be present).
    if mountpoint.nmea:
        ...  # TODO Handle NMEA validation using the Ntrip-GGA header and the mountpoint's configured position and mask.
        raise BadRequestError("NMEA validation requires Ntrip-GGA header, which is not present or invalid.")

    async def stream_frames(stream: HTTPResponse) -> None:
        async with caster.subscribe(mountpoint_id) as sub:
            while (frame := await sub.get()) is not None:
                await stream.write(frame)

    return ResponseStream(
        stream_frames,
        status=200,
        content_type=CONTENT_TYPE_GNSS,
        headers={
            NTRIP_VERSION: NTRIP_VERSION_2,
            "Cache-Control": "no-store, no-cache",
        },
    )
