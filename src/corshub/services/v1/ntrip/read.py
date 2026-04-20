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

Position updates
----------------
After the response starts, the rover may send NMEA GGA sentences in the
HTTP request body (NTRIP v2 spec §4.3.3 — sent as HTTP chunked data).  A
background task reads these and updates the rover's position in the caster
so the quality endpoint can expose last-known rover coordinates.
"""

from __future__ import annotations

import asyncio

from typing import TYPE_CHECKING

from sanic.exceptions import NotFound
from sanic.exceptions import Unauthorized
from sanic.response import ResponseStream

import corshub.metrics as metrics

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

    from corshub.ntrip.v2.caster import NTRIPCaster


async def _read_rover_gga(
    request: Request,
    mountpoint: str,
    connection_id: str,
    caster: NTRIPCaster,
) -> None:
    """Read NMEA GGA sentences sent by the rover in the HTTP request body.

    NTRIP v2 rovers may stream periodic GGA updates as HTTP chunked data on the
    same GET connection while receiving RTCM frames.  This coroutine runs as a
    background task alongside the frame-delivery loop; it silently exits if the
    rover sends no body or the stream ends.
    """
    buf = b""

    try:
        async for chunk in request.stream:
            if not chunk:
                continue
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                gga = parse_ntrip_gga(line.decode("ascii", errors="ignore").strip())
                if gga is not None:
                    lat, lon = gga
                    caster.set_rover_position(mountpoint, connection_id, lat, lon)
                    metrics.rover_gga_updates_total.labels(mountpoint=mountpoint).inc()

    except Exception:
        pass  # Rover sent no body, stream ended, or Sanic doesn't expose GET body.


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

    # Use the rover's username as the stable per-connection position key.
    # A username uniquely identifies a rover in the OPA policy; using it here
    # means the quality endpoint can surface a meaningful rover identifier.
    connection_id = request.credentials.username

    async def stream_frames(stream: HTTPResponse) -> None:
        # The transport may disappear between the availability check above and this
        # point if the base station disconnects concurrently.  Treat that as a
        # normal end-of-stream rather than a server error.
        #
        # asyncio.timeout(None) sets no deadline and is a no-op, so registered
        # users (max_session_seconds=None) pass through without any timeout.
        # Users with a policy-imposed limit (e.g. anonymous) are disconnected
        # cleanly once the deadline expires.

        # Seed position from the connection-time GGA header (if present).
        initial_gga = parse_ntrip_gga(request.headers.get(NTRIP_GGA))
        if initial_gga is not None:
            caster.set_rover_position(mountpoint, connection_id, *initial_gga)

        gga_task = asyncio.create_task(
            _read_rover_gga(request, mountpoint, connection_id, caster)
        )

        try:
            async with asyncio.timeout(max_session_seconds):
                async with caster.subscribe(mountpoint) as sub:
                    while (frame := await sub.get()) is not None:
                        await stream.write(frame)

        except (KeyError, TimeoutError):
            pass  # Mountpoint closed, or session limit reached.
        finally:
            gga_task.cancel()
            try:
                await gga_task
            except (asyncio.CancelledError, Exception):
                pass
            caster.clear_rover_position(mountpoint, connection_id)

    return ResponseStream(
        stream_frames,
        status=200,
        content_type=CONTENT_TYPE_GNSS,
        headers={
            NTRIP_VERSION: NTRIP_VERSION_2,
            "Cache-Control": "no-store, no-cache",
        },
    )