"""
Find the nearest NTRIP caster to the rover.

NTRIP v2 specification defines a "nearest" endpoint that returns the single mountpoint closest to the rover's approximate position, as provided in the Ntrip-GGA header. This is a convenience for rovers that want to connect to the nearest mountpoint without first fetching the entire source table and parsing coordinates client-side.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sanic.exceptions import NotFound

from corshub.exceptions.http import BadRequestError
from corshub.logging import logger
from corshub.ntrip.v2.headers import NTRIP_GGA
from corshub.ntrip.v2.headers import gga_problem
from corshub.ntrip.v2.headers import parse_ntrip_gga

from .base import bp
from .read import stream_mountpoint


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
    """
    header = request.headers.get(NTRIP_GGA)
    position = parse_ntrip_gga(header)
    if position is None:
        problem = gga_problem(header)
        logger.info("NEAREST rejected: reason=%s", problem)
        raise BadRequestError(f"{problem}; NEAREST needs the rover position in the Ntrip-GGA request header.")

    best_id = request.app.ctx.ntrip_caster.nearest(*position)
    # Positions are personal data: only at DEBUG.
    logger.debug("NEAREST: lat=%.4f lon=%.4f -> mountpoint=%s", position[0], position[1], best_id)
    if best_id is None:
        logger.info("NEAREST rejected: reason=no online mountpoint in range")
        raise NotFound("No mountpoint found within range of the rover's reported position.")

    return await stream_mountpoint(request, best_id)


@bp.get("/NEAR")
async def near(request: Request) -> HTTPResponse:
    return await find_and_read(request)


@bp.get("/NEAREST")
async def nearest(request: Request) -> HTTPResponse:
    return await find_and_read(request)


@bp.get("/NSB")
async def nsb(request: Request) -> HTTPResponse:
    return await find_and_read(request)
