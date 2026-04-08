from __future__ import annotations

import pytest

from sanic import Sanic

from corshub.ntrip.v2.caster import Mountpoint
from corshub.ntrip.v2.caster import NTRIPCaster
from corshub.services.v1.ntrip import service as ntrip_service

ntrip_blueprint = ntrip_service.blueprint("v1")


@pytest.fixture(autouse=True, scope="session")
def enable_test_mode() -> None:
    Sanic.test_mode = True


@pytest.fixture
def mountpoint() -> Mountpoint:
    return Mountpoint(
        name="BASE1",
        identifier="BASE1",
        username="BASE1",
        password="s3cr3t",
        format="RTCM 3.3",
        country="BEL",
        latitude=50.8503,
        longitude=4.3517,
    )


@pytest.fixture
def caster(mountpoint: Mountpoint) -> NTRIPCaster:
    c = NTRIPCaster()
    c.register(mountpoint)
    return c


@pytest.fixture
def app(caster: NTRIPCaster) -> Sanic:
    _app = Sanic("test_ntrip")
    _app.blueprint(ntrip_blueprint)

    @_app.before_server_start
    async def setup_caster(app: Sanic, _: object) -> None:
        app.ctx.ntrip_caster = caster

    return _app
