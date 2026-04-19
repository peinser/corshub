"""
Tests for policy-driven rover session timeouts.

The OPA rover policy may return ``max_session_seconds`` for certain users
(e.g. anonymous).  When present, the rover stream is wrapped with
``asyncio.timeout(max_session_seconds)`` and closed cleanly once the deadline
expires.  When absent (``None``), no deadline is set and the stream stays open
until the base station disconnects or the rover closes the connection.
"""

from __future__ import annotations

import asyncio
import base64
import time

import pytest

from sanic import Sanic
from unittest.mock import AsyncMock

from corshub import http
from corshub.ntrip.v2.caster import NTRIPCaster
from corshub.services.v1.ntrip import service as ntrip_service


NTRIP_HEADERS = {"Ntrip-Version": "Ntrip/2.0"}
AUTH = "Basic " + base64.b64encode(b"anonymous:anonymous").decode()

_METADATA = {
    "name": "BASE1",
    "mountpoint": "BASE1",
    "identifier": "Test Station",
    "format": "RTCM 3.3",
    "country": "BEL",
    "latitude": 50.8503,
    "longitude": 4.3517,
}

# Short timeout used in tests so the suite stays fast.
_TEST_TIMEOUT_S = 0.2


@pytest.fixture
async def caster() -> NTRIPCaster:
    c = NTRIPCaster()
    await c.register(**_METADATA)
    return c


def _make_app(caster: NTRIPCaster, max_session_seconds: float | None) -> Sanic:
    app = Sanic(f"test_timeout_{id(caster)}")
    http.initialize_http_sessions(app)
    app.blueprint(ntrip_service.blueprint())

    caster.authenticate_base_station = AsyncMock(return_value=True)
    caster.authenticate_rover = AsyncMock(return_value=(True, max_session_seconds))

    @app.before_server_start
    async def _set_caster(app: Sanic, _: object) -> None:
        app.ctx.ntrip_caster = caster

    return app


class TestSessionTimeout:
    async def test_stream_closes_after_session_limit(self, caster: NTRIPCaster) -> None:
        """A rover with a session limit is disconnected once the deadline expires."""
        app = _make_app(caster, _TEST_TIMEOUT_S)

        start = time.monotonic()
        _, response = await app.asgi_client.get(
            "/BASE1", headers={**NTRIP_HEADERS, "Authorization": AUTH}
        )
        elapsed = time.monotonic() - start

        assert response.status_code == 200
        # Stream should have closed shortly after the timeout fired, not hung.
        assert elapsed < _TEST_TIMEOUT_S + 2.0

    async def test_stream_closed_by_timeout_receives_no_frames(
        self, caster: NTRIPCaster
    ) -> None:
        """No RTCM frames are published during the timeout window, so the body is empty."""
        app = _make_app(caster, _TEST_TIMEOUT_S)

        _, response = await app.asgi_client.get(
            "/BASE1", headers={**NTRIP_HEADERS, "Authorization": AUTH}
        )

        assert response.body == b""

    async def test_unlimited_session_closes_when_mountpoint_shuts_down(
        self, caster: NTRIPCaster
    ) -> None:
        """A rover with no session limit (None) stays connected until the transport shuts down."""
        app = _make_app(caster, None)

        async def _shutdown_after_delay() -> None:
            await asyncio.sleep(_TEST_TIMEOUT_S)
            for transport in caster._transports.values():
                await transport.shutdown()

        asyncio.create_task(_shutdown_after_delay())

        start = time.monotonic()
        _, response = await app.asgi_client.get(
            "/BASE1", headers={**NTRIP_HEADERS, "Authorization": AUTH}
        )
        elapsed = time.monotonic() - start

        assert response.status_code == 200
        # Closed by the transport shutdown, not by a session timeout.
        assert elapsed >= _TEST_TIMEOUT_S
