"""
Find the nearest NTRIP caster to the rover.

NTRIP v2 specification defines a "nearest" endpoint that returns the single mountpoint closest to the rover's approximate position, as provided in the Ntrip-GGA header. This is a convenience for rovers that want to connect to the nearest mountpoint without first fetching the entire source table and parsing coordinates client-side.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sanic.exceptions import NotFound

from corshub.exceptions.http import BadRequestError
from corshub.ntrip.v2.headers import NTRIP_GGA
from corshub.ntrip.v2.headers import haversine
from corshub.ntrip.v2.headers import parse_ntrip_gga

from .base import bp
from .read import read


if TYPE_CHECKING:
    from sanic import HTTPResponse
    from sanic import Request


async def find_and_read(request: Request) -> HTTPResponse:
    """Stream RTCM correction frames to a rover from the nearest mountpoint.

    Parses the rover's approximate position from the ``Ntrip-GGA`` header,
    finds the registered mountpoint closest to that position (respecting each
    mountpoint's mask), then delegates to the normal rover stream handler.

    Raises ``BadRequestError`` (400) if the ``Ntrip-GGA`` header is absent or
    invalid.  Raises ``NotFound`` (404) if no mountpoint lies within range of
    the rover's reported position.

    For now, this implementation simply iterates through all mountpoints and
    finds the closest one. Sufficiently efficient for small number of mountpoints,
    but may need a spatial index / datastructure if the number of mountpoints grows large.
    """
    position = parse_ntrip_gga(request.headers.get(NTRIP_GGA))
    if position is None:
        raise BadRequestError("Ntrip-GGA header is absent or not a valid GGA sentence.")

    rover_lat, rover_lon = position
    caster = request.app.ctx.ntrip_caster

    best_id: str | None = None
    best_dist = float("inf")

    for mp_id, mp in caster.mountpoints.items():
        dist = haversine(mp.latitude, mp.longitude, rover_lat, rover_lon)

        if mp.mask > 0.0 and dist > mp.mask:
            continue

        if dist < best_dist:
            best_dist = dist
            best_id = mp_id

    if best_id is None:
        raise NotFound("No mountpoint found within range of the rover's reported position.")

    return await read(request, best_id)


@bp.get("/NEAR")
async def near(request: Request) -> HTTPResponse:
    return await find_and_read(request)


@bp.get("/NEAREST")
async def nearest(request: Request) -> HTTPResponse:
    return await find_and_read(request)


@bp.get("/NSB")
async def nsb(request: Request) -> HTTPResponse:
    return await find_and_read(request)
