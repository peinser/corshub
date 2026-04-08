"""
NTRIP v2 base-station endpoint PUT /<mountpoint>

A base station opens a long-lived PUT request and streams raw RTCM correction
frames for the duration of its session.  The caster reads each chunk and
fans it out to all subscribed rovers via the transport layer.

Mountpoints are pre-provisioned by the operator.  The base station must supply
valid Basic credentials matching the mountpoint's username/password.  Attempting
to push to an unknown mountpoint returns 404.

Request requirements (RTCM 10410.1 §4.2):
    Ntrip-Version: Ntrip/2.0          mandatory
    Authorization: Basic <b64>         mandatory — username:password
    Content-Type: gnss/data            mandatory
    Ntrip-STR: <str-fields>            optional  — self-description
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sanic import response
from sanic.exceptions import NotFound
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

    The mountpoint must already be provisioned.  Validates Basic credentials,
    then reads the request body in chunks and publishes each to the transport.
    """
    if request.headers.get(NTRIP_VERSION, "").lower() != NTRIP_VERSION_2.lower():
        raise BadRequestError(f"{NTRIP_VERSION}: {NTRIP_VERSION_2} header is required.")

    if request.headers.get("Content-Type", "").lower() != CONTENT_TYPE_GNSS.lower():
        raise BadRequestError(f"Content-Type: {CONTENT_TYPE_GNSS} header is required.")

    if not request.credentials:
        raise Unauthorized("Basic credentials required.", scheme="Basic")

    caster = request.app.ctx.ntrip_caster

    if mountpoint_id not in caster.mountpoints:
        raise NotFound(f"Mountpoint {mountpoint_id!r} does not exist.")

    if not caster.authenticate_source(mountpoint_id, request.credentials.password):
        raise Unauthorized("Invalid mountpoint credentials.", scheme="Basic")

    # Parse optional self-description header; ignore errors — it is advisory only.
    meta = {}
    ntrip_str_header = request.headers.get(NTRIP_STR)
    if ntrip_str_header:
        try:
            meta = parse_ntrip_str(ntrip_str_header, mountpoint_id)
        except Exception:
            pass  # Malformed Ntrip-STR is not fatal; we already have a registered mountpoint.

    # Update metadata fields from Ntrip-STR if provided.
    if meta:
        from corshub.ntrip.v2.caster import NTRIPCaster
        mp = caster.mountpoints[mountpoint_id]
        for key, value in meta.items():
            if key in NTRIPCaster._METADATA_FIELDS and value is not None:
                setattr(mp, key, value)

    # Stream RTCM frames from the request body and publish to subscribers.
    async for chunk in request.stream:
        if chunk:
            await caster.publish(mountpoint_id, chunk)

    return response.empty(200)
