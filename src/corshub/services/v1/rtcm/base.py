"""
Blueprint, configuration, and lifecycle for the RTCM UDP egress service.

This service is opt-in and inert by default. When ``RTCM_UDP_ENABLED`` is false
no UDP socket is bound and the endpoints return 404. Signing is independently
optional via ``RTCM_UDP_SIGNING_ENABLED``.

The egress depends on the NTRIP caster (``app.ctx.ntrip_caster``) created by the
NTRIP blueprint; this blueprint must be registered after it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sanic import Blueprint
from sanic import HTTPResponse
from sanic import Request
from sanic import response
from sanic.exceptions import SanicException

from corshub import env
from corshub.logging import logger
from corshub.rtcm.keys import resolve_signing_key
from corshub.rtcm.udp import RTCMDatagramServer


if TYPE_CHECKING:
    from sanic import Sanic

    from corshub.rtcm.keys import SigningKey


bp: Blueprint = Blueprint(name="rtcm-v1", url_prefix="/api/v1/rtcm")


def _flag(key: str, default: str = "false") -> bool:
    return env.extract(key, default=default).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class RTCMConfig:
    enabled: bool
    signing_enabled: bool
    udp_host: str
    udp_port: int
    udp_endpoint: str
    token_secret: str
    token_ttl: int
    session_ttl: float
    keepalive_interval: int
    allow_ephemeral_key: bool


def load_config() -> RTCMConfig:
    enabled = _flag("RTCM_UDP_ENABLED")
    host = env.extract("RTCM_UDP_HOST", default="0.0.0.0")
    port = env.extract("RTCM_UDP_PORT", default="5009", dtype=int)
    token_secret = env.extract("RTCM_SESSION_TOKEN_SECRET", default="") or ""

    if enabled and not token_secret:
        raise ValueError("RTCM_UDP_ENABLED is set but RTCM_SESSION_TOKEN_SECRET is missing.")

    return RTCMConfig(
        enabled=enabled,
        signing_enabled=_flag("RTCM_UDP_SIGNING_ENABLED"),
        udp_host=host,
        udp_port=port,
        udp_endpoint=env.extract("RTCM_UDP_ENDPOINT", default=f"{host}:{port}"),
        token_secret=token_secret,
        token_ttl=env.extract("RTCM_SESSION_TOKEN_TTL", default="60", dtype=int),
        session_ttl=env.extract("RTCM_UDP_SESSION_TTL", default="30", dtype=float),
        keepalive_interval=env.extract("RTCM_UDP_KEEPALIVE_INTERVAL", default="10", dtype=int),
        allow_ephemeral_key=_flag("RTCM_SIGNING_ALLOW_EPHEMERAL"),
    )


def _build_signing_key(config: RTCMConfig) -> SigningKey | None:
    if not config.signing_enabled:
        return None
    return resolve_signing_key(
        key_path=env.extract("RTCM_SIGNING_KEY_PATH", default=None),
        key_inline=env.extract("RTCM_SIGNING_PRIVATE_KEY", default=None),
        allow_ephemeral=config.allow_ephemeral_key,
    )


@bp.exception(SanicException)
async def rtcm_error(_: Request, exc: SanicException) -> HTTPResponse:
    return response.json({"error": str(exc.message or exc)}, status=exc.status_code)


@bp.before_server_start
async def setup(app: Sanic) -> None:
    config = load_config()
    app.ctx.rtcm_config = config

    if not config.enabled:
        logger.info("RTCM UDP egress disabled (RTCM_UDP_ENABLED is false).")
        app.ctx.rtcm_signing_key = None
        app.ctx.rtcm_server = None
        return

    signing_key = _build_signing_key(config)
    app.ctx.rtcm_signing_key = signing_key

    caster = getattr(app.ctx, "ntrip_caster", None)
    if caster is None:
        logger.error("RTCM UDP enabled but no NTRIP caster is present; the egress will not start.")
        app.ctx.rtcm_server = None
        return

    server = RTCMDatagramServer(
        caster,
        host=config.udp_host,
        port=config.udp_port,
        token_secret=config.token_secret,
        signing_key=signing_key,
        signing_enabled=config.signing_enabled,
        session_ttl=config.session_ttl,
        keepalive_interval=config.keepalive_interval,
    )
    await server.start()
    app.ctx.rtcm_server = server


@bp.after_server_stop
async def finalize(app: Sanic) -> None:
    server = getattr(app.ctx, "rtcm_server", None)
    if server is not None:
        await server.stop()
        app.ctx.rtcm_server = None
