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

import asyncio

from typing import TYPE_CHECKING

from sanic.exceptions import NotFound
from sanic.exceptions import Unauthorized
from sanic.response import ResponseStream

from corshub.exceptions.http import BadRequestError
from corshub.ntrip.v2.headers import CONTENT_TYPE_GNSS
from corshub.ntrip.v2.headers import NTRIP_GGA
from corshub.ntrip.v2.headers import NTRIP_VERSION
from corshub.ntrip.v2.headers import NTRIP_VERSION_2
from corshub.ntrip.v2.headers import haversine
from corshub.ntrip.v2.headers import parse_ntrip_gga

from .base import bp


if TYPE_CHECKING:
    from sanic import HTTPResponse
    from sanic import Request


@bp.get("/<mountpoint:str>")
async def read(request: Request, mountpoint: str) -> HTTPResponse:
    """Stream RTCM correction frames to a rover.

    Validates the Ntrip-Version header and Basic credentials, then opens a
    chunked streaming response that forwards every frame published on
    *mountpoint* until the rover disconnects.
    """
    if request.headers.get(NTRIP_VERSION, "").lower() != NTRIP_VERSION_2.lower():
        raise BadRequestError(f"{NTRIP_VERSION}: {NTRIP_VERSION_2} header is required.")

    if not request.credentials:
        raise Unauthorized("Basic credentials required.", scheme="Basic")

    caster = request.app.ctx.ntrip_caster

    allowed, max_session_seconds = await caster.authenticate_rover(
        request.credentials.username, request.credentials.password, mountpoint
    )
    if not allowed:
        raise Unauthorized("Invalid credentials.", scheme="Basic")

    mp = caster.mountpoints.get(mountpoint)
    if not mp or not await caster.available(mountpoint):
        raise NotFound(f"Mountpoint {mountpoint!r} does not exist or is not available.")

    # When the mountpoint requires NMEA, validate the rover's Ntrip-GGA header.
    if mp.nmea:
        position = parse_ntrip_gga(request.headers.get(NTRIP_GGA))
        if position is None:
            raise BadRequestError("Ntrip-GGA header is absent or not a valid GGA sentence.")
        if mp.mask > 0.0:
            rover_lat, rover_lon = position
            dist = haversine(mp.latitude, mp.longitude, rover_lat, rover_lon)
            if dist > mp.mask:
                raise BadRequestError(f"Rover is {dist:.1f} km from {mountpoint!r}, exceeds mask of {mp.mask:.1f} km.")

    async def stream_frames(stream: HTTPResponse) -> None:
        # The transport may disappear between the availability check above and this
        # point if the base station disconnects concurrently.  Treat that as a
        # normal end-of-stream rather than a server error.
        #
        # asyncio.timeout(None) sets no deadline and is a no-op, so registered
        # users (max_session_seconds=None) pass through without any timeout.
        # Users with a policy-imposed limit (e.g. anonymous) are disconnected
        # cleanly once the deadline expires.
        try:
            async with asyncio.timeout(max_session_seconds):
                async with caster.subscribe(mountpoint) as sub:
                    while (frame := await sub.get()) is not None:
                        await stream.write(frame)

        except KeyError, TimeoutError:
            pass  # Mountpoint closed, or session limit reached.

    return ResponseStream(
        stream_frames,
        status=200,
        content_type=CONTENT_TYPE_GNSS,
        headers={
            NTRIP_VERSION: NTRIP_VERSION_2,
            "Cache-Control": "no-store, no-cache",
        },
    )
