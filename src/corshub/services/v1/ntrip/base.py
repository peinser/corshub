"""
Blueprint definition for NTRIP services, including shared utilities and base classes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sanic import Blueprint
from sanic import HTTPResponse
from sanic import Request
from sanic import response
from sanic.exceptions import SanicException

from corshub import env
from corshub.ntrip.v2.caster import NTRIPCaster
from corshub.opa import OPAClient


if TYPE_CHECKING:
    from sanic import Sanic


bp: Blueprint = Blueprint(
    name="ntrip-v1",
    url_prefix="/api/v1/ntrip",
)


env.verify(
    blueprint=bp,
    required={"OPA_URL"},
)


@bp.exception(SanicException)
async def ntrip_error(_: Request, exc: SanicException) -> HTTPResponse:
    """Return plain-text error responses as required by RTCM 10410.1.

    Overrides the app-wide FALLBACK_ERROR_FORMAT (JSON) for all routes on
    this blueprint so that NTRIP clients — which are often embedded firmware
    that cannot parse JSON — receive a human-readable message instead.
    """
    return response.text(str(exc.message or exc), status=exc.status_code)


@bp.before_server_start
async def setup(app: Sanic) -> None:
    """Initialize the shared NTRIP caster before the server starts accepting requests."""
    opa_url: str = env.extract("OPA_URL", default="http://opa:8181")
    opa = OPAClient(base_url=opa_url, session=app.ctx.http_client_session)

    app.ctx.ntrip_caster = NTRIPCaster(
        opa=opa,
        expiry=3600.0,
        reap_interval=30.0,
    )

    # Start the caster and its reaper loop.
    await app.ctx.ntrip_caster.start()


@bp.after_server_stop
async def finalize(app: Sanic) -> None:
    """Stop the shared NTRIP caster after the server has been stopped."""
    await app.ctx.ntrip_caster.stop()
    del app.ctx.ntrip_caster
