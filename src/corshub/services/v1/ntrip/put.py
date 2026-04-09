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

import socket

from typing import TYPE_CHECKING

from sanic import response
from sanic.exceptions import Unauthorized

from corshub.exceptions.http import BadRequestError
from corshub.exceptions.http import ConflictError
from corshub.logging import logger
from corshub.ntrip.v2.headers import CONTENT_TYPE_GNSS
from corshub.ntrip.v2.headers import NTRIP_STR
from corshub.ntrip.v2.headers import NTRIP_VERSION
from corshub.ntrip.v2.headers import NTRIP_VERSION_2
from corshub.ntrip.v2.headers import parse_ntrip_str

from .base import bp


if TYPE_CHECKING:
    from sanic import HTTPResponse
    from sanic import Request


@bp.put("/<mountpoint_id:str>", stream=True)
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
    if not caster.authenticate(request.credentials.username, request.credentials.password):
        raise Unauthorized("Invalid mountpoint credentials.", scheme="Basic")

    # Check if the mountpoint is already connected.
    if mountpoint_id in caster.mountpoints:
        raise ConflictError("Caster already connected to the exchange.")

    # Parse optional self-description header; ignore errors — it is advisory only.
    meta = {}
    ntrip_str_header = request.headers.get(NTRIP_STR)
    if ntrip_str_header:
        try:
            meta = parse_ntrip_str(ntrip_str_header, mountpoint_id)
        except Exception:
            pass  # Malformed Ntrip-STR is not fatal; we already have a registered mountpoint.

    # Register the mountpoint with the available metadata.
    await caster.register(identifier=mountpoint_id, **meta)
    logger.info("Registered mountpoint %r with metadata: %s", mountpoint_id, meta)

    # Stream RTCM frames from the request body and publish to subscribers.
    if request.stream:
        logger.info("Starting RTCM stream for mountpoint %r", mountpoint_id)

        # Disable Nagle's algorithm during a stream, disable buffering.
        sock = request.transport.get_extra_info("socket")
        if sock is not None:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

        # Send 200 immediately so the client knows we're ready to receive (inform proxy).
        resp = await request.respond(
            status=200,
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            }
        )

        try:
            # Read the received chunks in the stream.
            async for chunk in request.stream:
                if chunk:
                    logger.info(
                        "Received chunk of %d bytes for mountpoint %r from IP %s",
                        len(chunk),
                        mountpoint_id,
                        request.remote_addr or request.ip,
                    )

                    acks = await caster.publish(mountpoint_id, chunk)
                    logger.info("Dispatched to %d rovers for mountpoint %r.", acks, mountpoint_id)
                    await resp.send(data=str(acks))  # Send back the number of ACKs from the rovers for now.

        finally:
            await caster.unregister(mountpoint_id)

        return await resp.eof()

    # Read the request body to completion, even if the client doesn't stream, to avoid leaving a hanging request.
    logger.info(
        "Received non-streaming request with body of %d bytes for mountpoint %r from IP %s",
        len(request.body),
        mountpoint_id,
        request.remote_addr or request.ip,
    )

    acks = await caster.publish(mountpoint_id, request.body) if request.body else 0
    await caster.unregister(mountpoint_id)

    return response.text(status=200, body=str(acks))
