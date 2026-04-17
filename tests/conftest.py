from __future__ import annotations

import pytest

from sanic import Sanic

from unittest.mock import AsyncMock

from corshub import http
from corshub.ntrip.v2.caster import Mountpoint
from corshub.ntrip.v2.caster import NTRIPCaster
from corshub.services.v1.ntrip import service as ntrip_service

ntrip_blueprint = ntrip_service.blueprint()


@pytest.fixture(autouse=True, scope="session")
def enable_test_mode() -> None:
    Sanic.test_mode = True


_metadata = {
    "name": "BASE1",
    "identifier": "BASE1",
    "format": "RTCM 3.3",
    "country": "BEL",
    "latitude": 50.8503,
    "longitude": 4.3517,
}


@pytest.fixture
def mountpoint_metadata() -> dict:
    return _metadata


@pytest.fixture
async def caster() -> NTRIPCaster:
    c = NTRIPCaster()
    # Note: Don't start the caster because this will initiate the reap thread and you'll lose the base stations.

    await c.register(**_metadata)

    return c


@pytest.fixture
def app(caster: NTRIPCaster) -> Sanic:
    _app = Sanic("test_ntrip")

    # Ensure an HTTP session is registered like we do in the application startup.
    # This needs to happen first!
    http.initialize_http_sessions(_app)

    _app.blueprint(ntrip_blueprint)

    caster.authenticate_base_station = AsyncMock(return_value=True)
    caster.authenticate_rover = AsyncMock(return_value=True)

    @_app.before_server_start
    async def setup_caster(app: Sanic, _: object) -> None:
        app.ctx.ntrip_caster = caster

    return _app
