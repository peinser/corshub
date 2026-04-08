"""
NTRIP v2 base-station endpoint PUT /<mountpoint>

A base station opens a long-lived PUT request and streams raw RTCM correction
frames for the duration of its session.  The caster reads each chunk and
fans it out to all subscribed rovers via the transport layer.

On disconnect the mountpoint is unregistered, making it disappear from the
source table until the base reconnects.

Request requirements (RTCM 10410.1 §4.2):
    Ntrip-Version: Ntrip/2.0          mandatory
    Authorization: Basic <b64>         mandatory — username:password
    Content-Type: gnss/data            mandatory
    Ntrip-STR: <str-fields>            optional  — self-description
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sanic import response
from sanic.exceptions import Unauthorized

from corshub.exceptions.http import BadRequestError
from corshub.ntrip.v2.headers import CONTENT_TYPE_GNSS
from corshub.ntrip.v2.headers import NTRIP_STR
from corshub.ntrip.v2.headers import NTRIP_VERSION
from corshub.ntrip.v2.headers import NTRIP_VERSION_2
from corshub.ntrip.v2.headers import parse_ntrip_str

from .base import bp


if TYPE_CHECKING:
    from sanic import HTTPResponse
    from sanic import Request


@bp.put("/<mountpoint_id:str>")
async def put(request: Request, mountpoint_id: str) -> HTTPResponse:
    """Accept a continuous RTCM stream from a base station.

    Registers the mountpoint on first connect (dynamic registration), then
    reads the request body in chunks and publishes each to the transport.
    Unregisters the mountpoint on disconnect.
    """
    if request.headers.get(NTRIP_VERSION).lower() != NTRIP_VERSION_2.lower():
        raise BadRequestError(f"{NTRIP_VERSION}: {NTRIP_VERSION_2} header is required.")

    if request.headers.get("Content-Type").lower() != CONTENT_TYPE_GNSS.lower():
        raise BadRequestError(f"Content-Type: {CONTENT_TYPE_GNSS} header is required.")

    if not request.credentials:
        raise Unauthorized("Basic credentials required.", scheme="Basic")

    caster = request.app.ctx.ntrip_caster
    if not caster.authenticate(request.credentials.username, request.credentials.password):
        raise Unauthorized("Invalid mountpoint credentials.", scheme="Basic")

    try:
        meta = parse_ntrip_str(request.headers.get(NTRIP_STR), mountpoint_id)
    except Exception as ex:
        raise BadRequestError("Malformed Ntrip-STR header.") from ex

    mountpoint = await caster.register(
        identifier=mountpoint_id,
        username=request.credentials.username,
        password=request.credentials.password,
        **meta
    )

    try:
        await caster.publish(mountpoint, request.body)
    finally:
        await caster.unregister(mountpoint_id)

    return response.empty(200)
