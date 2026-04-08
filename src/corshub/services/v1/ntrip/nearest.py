"""
Find the nearest NTRIP caster to the rover.

NTRIP v2 specification defines a "nearest" endpoint that returns the single mountpoint closest to the rover's approximate position, as provided in the Ntrip-GGA header. This is a convenience for rovers that want to connect to the nearest mountpoint without first fetching the entire source table and parsing coordinates client-side.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import bp
from .read import read


if TYPE_CHECKING:
    from sanic import HTTPResponse
    from sanic import Request


async def find_and_read(request: Request)
    """Stream RTCM correction frames to a rover from the nearest mountpoint.

    Validates the Ntrip-Version header and Basic credentials, then opens a
    chunked streaming response that forwards every frame published on
    *mountpoint_id* until the rover disconnects.
    """
    # TODO Exctract the rover's approximate position from the Ntrip-GGA header and find the nearest mountpoint with a configured position within the specified mask.  If none are found, return 404.
    mountpoint_id = "TODO"  # TODO This is a placeholder until the nearest-mountpoint logic is implemented.

    return await read(request, mountpoint_id)


@bp.get("/NEAR")
async def near(request: Request) -> HTTPResponse:
    return await find_and_read(request)


@bp.get("/NEAREST")
async def nearest(request: Request) -> HTTPResponse:
    return await find_and_read(request)


@bp.get("/NSB")
async def nsb(request: Request) -> HTTPResponse:
    return await find_and_read(request)
