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
    Authorization: Basic <b64>         mandatory - username:password
    Content-Type: gnss/data            mandatory
    Ntrip-STR: <str-fields>            optional  - self-description
"""

from __future__ import annotations

import time

from typing import TYPE_CHECKING

from sanic import response
from sanic.exceptions import Unauthorized

from corshub.exceptions.http import BadRequestError
from corshub.exceptions.http import ConflictError
from corshub.http.ratelimit import enforce_auth_rate_limit
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


@bp.put("/<mountpoint:str>", stream=True)
async def put(request: Request, mountpoint: str) -> HTTPResponse:
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

    # Throttle auth attempts per client before the bcrypt path.
    enforce_auth_rate_limit(request)

    # Note, this implicitly checks wheter a transport is NOT available.
    caster = request.app.ctx.ntrip_caster
    allowed, verified = await caster.authenticate_base_station(
        username=request.credentials.username,
        password=request.credentials.password,
        mountpoint=mountpoint,
    )
    if not allowed:
        raise Unauthorized("Invalid mountpoint credentials.", scheme="Basic")

    # Parse optional self-description header; ignore errors: it is advisory only.
    meta = {}
    ntrip_str_header = request.headers.get(NTRIP_STR)
    if ntrip_str_header:
        try:
            meta = parse_ntrip_str(ntrip_str_header, mountpoint)
        except ValueError as ex:
            logger.warning("Base station rejected: mountpoint=%s reason=invalid Ntrip-STR (%s)", mountpoint, ex)
            raise BadRequestError(f"Invalid Ntrip-STR: {ex}") from ex
        except Exception:  # noqa: BLE001 - advisory header only; we already have a registered mountpoint
            logger.debug("Ignoring malformed Ntrip-STR: mountpoint=%s", mountpoint, exc_info=True)

    # OPA only denies a live mountpoint at query time; a second publisher may have
    # connected while this request awaited OPA and bcrypt.  Nothing suspends between
    # this check and register(), so it is atomic on the event loop.
    if await caster.available(mountpoint):
        logger.warning("Base station rejected: mountpoint=%s reason=already live", mountpoint)
        raise ConflictError(f"Mountpoint {mountpoint!r} already has an active base station.")

    # Register the mountpoint with the available metadata.
    try:
        # verified comes from the registry (via OPA) on every connect, never from the station.
        await caster.register(mountpoint=mountpoint, **meta, verified=verified)
    except ValueError as ex:
        logger.warning("Base station rejected: mountpoint=%s reason=%s", mountpoint, ex)
        raise BadRequestError(f"Invalid mountpoint name: {ex}") from ex

    client_ip = request.remote_addr or request.ip
    logger.info(
        "Base station connected: mountpoint=%s user=%s ip=%s verified=%s",
        mountpoint,
        request.credentials.username,
        client_ip,
        verified,
    )
    logger.debug("Base station metadata: mountpoint=%s meta=%s", mountpoint, meta)

    # Stream RTCM frames from the request body and publish to subscribers.
    if request.stream:
        # Send 200 immediately so the client knows we're ready to receive (inform proxy).
        resp = await request.respond(
            status=200,
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

        started = time.monotonic()
        chunks = 0
        received = 0
        reason = "end of stream"
        try:
            # Read the received chunks in the stream.
            async for chunk in request.stream:
                if chunk:
                    chunks += 1
                    received += len(chunk)
                    acks = await caster.publish(mountpoint, chunk)
                    logger.debug("Chunk: mountpoint=%s bytes=%d rovers=%d", mountpoint, len(chunk), acks)
                    await resp.send(data=str(acks))  # Send back the number of ACKs from the rovers for now.

        except BaseException as ex:
            reason = type(ex).__name__  # e.g. CancelledError when the connection drops
            raise

        finally:
            logger.info(
                "Base station disconnected: mountpoint=%s ip=%s duration=%.1fs chunks=%d bytes=%d reason=%s",
                mountpoint,
                client_ip,
                time.monotonic() - started,
                chunks,
                received,
                reason,
            )
            await caster.close(mountpoint)

        return await resp.eof()

    # Read the request body to completion, even if the client doesn't stream, to avoid leaving a hanging request.
    logger.info("Base station sent a non-streaming body: mountpoint=%s bytes=%d", mountpoint, len(request.body))

    acks = await caster.publish(mountpoint, request.body) if request.body else 0
    await caster.close(mountpoint)

    return response.text(status=200, body=str(acks))
