"""
Tests for rover GGA position streaming (NTRIP v2 §4.3.3).

Coverage:
  _read_rover_gga   : unit tests with a mock request stream
  read route        : integration tests via the ASGI client verifying that the
                      header-seeded position is tracked and cleared on disconnect
"""

from __future__ import annotations

import asyncio
import base64

import pytest

from sanic import Sanic
from unittest.mock import AsyncMock, MagicMock, patch

from corshub import http
from corshub.ntrip.v2.caster import NTRIPCaster
from corshub.services.v1.ntrip import service as ntrip_service
from corshub.services.v1.ntrip.read import _read_rover_gga


NTRIP_HEADERS = {"Ntrip-Version": "Ntrip/2.0"}

_VALID_GGA = "$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47"


def _gga(lat: float, lon: float) -> str:
    """Build a minimal GPGGA sentence with a correct NMEA checksum."""
    lat_deg = int(abs(lat))
    lat_min = (abs(lat) - lat_deg) * 60
    lon_deg = int(abs(lon))
    lon_min = (abs(lon) - lon_deg) * 60
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    body = f"GPGGA,120000.00,{lat_deg:02d}{lat_min:08.5f},{ns},{lon_deg:03d}{lon_min:08.5f},{ew},1,08,1.0,0.0,M,0.0,M,,"
    checksum = 0
    for ch in body:
        checksum ^= ord(ch)
    return f"${body}*{checksum:02X}"


_VALID_GGA_2 = _gga(51.0, 3.72)  # distinct position from _VALID_GGA (Munich → Ghent)

_METADATA = {
    "name": "BASE1",
    "mountpoint": "BASE1",
    "identifier": "Test Station",
    "format": "RTCM 3.3",
    "country": "BEL",
    "latitude": 50.8503,
    "longitude": 4.3517,
}

_TEST_TIMEOUT_S = 0.25


def _basic_auth(username: str, password: str = "secret") -> str:
    return "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()


class _FakeStream:
    """Async iterator that yields pre-defined byte chunks."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = iter(chunks)

    def __aiter__(self) -> _FakeStream:
        return self

    async def __anext__(self) -> bytes:
        try:
            return next(self._chunks)
        except StopIteration:
            raise StopAsyncIteration


def _make_request(chunks: list[bytes], username: str = "rover1") -> MagicMock:
    req = MagicMock()
    req.stream = _FakeStream(chunks)
    creds = MagicMock()
    creds.username = username
    req.credentials = creds
    return req


@pytest.fixture
async def caster() -> NTRIPCaster:
    c = NTRIPCaster()
    await c.register(**_METADATA)
    return c


@pytest.fixture
def app(caster: NTRIPCaster) -> Sanic:
    _app = Sanic(f"test_gga_{id(caster)}")
    http.initialize_http_sessions(_app)
    _app.blueprint(ntrip_service.blueprint())
    caster.authenticate_base_station = AsyncMock(return_value=True)
    caster.authenticate_rover = AsyncMock(return_value=(True, None))

    @_app.before_server_start
    async def _set_caster(app: Sanic, _: object) -> None:
        app.ctx.ntrip_caster = caster

    return _app


class TestReadRoverGga:
    async def test_single_gga_line_sets_position(self, caster: NTRIPCaster) -> None:
        req = _make_request([(_VALID_GGA + "\n").encode()])
        await _read_rover_gga(req, "BASE1", "rover1", caster)
        positions = caster.get_rover_positions("BASE1")
        assert "rover1" in positions
        lat, lon = positions["rover1"]
        assert abs(lat - 48.117) < 0.01
        assert abs(lon - 11.517) < 0.01

    async def test_counter_incremented_per_valid_gga(self, caster: NTRIPCaster) -> None:
        lines = (_VALID_GGA + "\n" + _VALID_GGA_2 + "\n").encode()
        req = _make_request([lines])

        import corshub.metrics as m

        before = m.rover_gga_updates_total.labels(mountpoint="BASE1")._value.get()
        await _read_rover_gga(req, "BASE1", "rover1", caster)
        after = m.rover_gga_updates_total.labels(mountpoint="BASE1")._value.get()

        assert after - before == 2

    async def test_second_gga_overwrites_first_position(self, caster: NTRIPCaster) -> None:
        lines = (_VALID_GGA + "\n" + _VALID_GGA_2 + "\n").encode()
        req = _make_request([lines])
        await _read_rover_gga(req, "BASE1", "rover1", caster)
        positions = caster.get_rover_positions("BASE1")
        lat, _ = positions["rover1"]
        assert abs(lat - 51.0) < 0.01  # _VALID_GGA_2 = _gga(51.0, 3.72)

    async def test_gga_split_across_chunks(self, caster: NTRIPCaster) -> None:
        full = (_VALID_GGA + "\n").encode()
        mid = len(full) // 2
        req = _make_request([full[:mid], full[mid:]])
        await _read_rover_gga(req, "BASE1", "rover1", caster)
        assert "rover1" in caster.get_rover_positions("BASE1")

    async def test_non_gga_nmea_sentences_are_ignored(self, caster: NTRIPCaster) -> None:
        garbage = b"$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A\n"
        req = _make_request([garbage])
        await _read_rover_gga(req, "BASE1", "rover1", caster)
        assert "rover1" not in caster.get_rover_positions("BASE1")

    async def test_malformed_line_is_ignored(self, caster: NTRIPCaster) -> None:
        req = _make_request([b"not a valid sentence\n"])
        await _read_rover_gga(req, "BASE1", "rover1", caster)
        assert "rover1" not in caster.get_rover_positions("BASE1")

    async def test_empty_stream_is_harmless(self, caster: NTRIPCaster) -> None:
        req = _make_request([])
        await _read_rover_gga(req, "BASE1", "rover1", caster)
        assert caster.get_rover_positions("BASE1") == {}

    async def test_exception_in_stream_does_not_propagate(self, caster: NTRIPCaster) -> None:
        async def _bad_stream():
            yield b"some data\n"
            raise RuntimeError("transport error")

        req = MagicMock()
        req.stream = _bad_stream()
        req.credentials.username = "rover1"

        # Should not raise.
        await _read_rover_gga(req, "BASE1", "rover1", caster)

    async def test_multiple_rovers_tracked_independently(self, caster: NTRIPCaster) -> None:
        req1 = _make_request([(_VALID_GGA + "\n").encode()], username="rover1")
        req2 = _make_request([(_VALID_GGA_2 + "\n").encode()], username="rover2")
        await _read_rover_gga(req1, "BASE1", "rover1", caster)
        await _read_rover_gga(req2, "BASE1", "rover2", caster)
        positions = caster.get_rover_positions("BASE1")
        assert "rover1" in positions
        assert "rover2" in positions
        assert positions["rover1"] != positions["rover2"]

    async def test_clear_rover_position_removes_entry(self, caster: NTRIPCaster) -> None:
        req = _make_request([(_VALID_GGA + "\n").encode()])
        await _read_rover_gga(req, "BASE1", "rover1", caster)
        assert "rover1" in caster.get_rover_positions("BASE1")
        caster.clear_rover_position("BASE1", "rover1")
        assert "rover1" not in caster.get_rover_positions("BASE1")

    async def test_clear_nonexistent_rover_is_harmless(self, caster: NTRIPCaster) -> None:
        # Should not raise.
        caster.clear_rover_position("BASE1", "does-not-exist")
        caster.clear_rover_position("UNKNOWN", "rover1")


class TestReadRouteGgaHeader:
    async def _connect_and_close(
        self,
        app: Sanic,
        caster: NTRIPCaster,
        headers: dict,
        *,
        username: str = "rover1",
        delay: float = _TEST_TIMEOUT_S,
    ) -> None:
        """Issue a rover GET request, shut down the transport after *delay* seconds."""

        async def _shutdown() -> None:
            await asyncio.sleep(delay)
            for transport in list(caster._transports.values()):
                await transport.shutdown()

        asyncio.create_task(_shutdown())
        await app.asgi_client.get(
            "/BASE1",
            headers={**NTRIP_HEADERS, "Authorization": _basic_auth(username), **headers},
        )

    async def test_position_seeded_from_gga_header_during_connection(
        self, app: Sanic, caster: NTRIPCaster
    ) -> None:
        """While connected, position stored under rover's username."""
        positions_seen: list[dict] = []

        original_set = caster.set_rover_position

        def _capturing_set(mp: str, conn_id: str, lat: float, lon: float) -> None:
            positions_seen.append({"mountpoint": mp, "rover_id": conn_id, "lat": lat, "lon": lon})
            original_set(mp, conn_id, lat, lon)

        caster.set_rover_position = _capturing_set  # type: ignore[method-assign]

        await self._connect_and_close(
            app, caster, {"Ntrip-GGA": _VALID_GGA}, username="rover1"
        )

        assert len(positions_seen) >= 1
        assert positions_seen[0]["mountpoint"] == "BASE1"
        assert positions_seen[0]["rover_id"] == "rover1"
        assert abs(positions_seen[0]["lat"] - 48.117) < 0.01

    async def test_position_cleared_after_disconnect(
        self, app: Sanic, caster: NTRIPCaster
    ) -> None:
        """After the rover disconnects the entry is removed from the caster."""
        await self._connect_and_close(
            app, caster, {"Ntrip-GGA": _VALID_GGA}, username="rover1"
        )
        # Stream has ended; clear_rover_position should have been called.
        assert "rover1" not in caster.get_rover_positions("BASE1")

    async def test_no_gga_header_does_not_set_position(
        self, app: Sanic, caster: NTRIPCaster
    ) -> None:
        """Rover without any GGA leaves no position entry."""
        cleared_calls: list[tuple] = []
        original_clear = caster.clear_rover_position

        def _tracking_clear(mp: str, conn_id: str) -> None:
            cleared_calls.append((mp, conn_id))
            original_clear(mp, conn_id)

        caster.clear_rover_position = _tracking_clear  # type: ignore[method-assign]

        await self._connect_and_close(app, caster, {}, username="rover1")

        # clear was called but there was never anything to clear, which is fine.
        assert ("BASE1", "rover1") in cleared_calls
        assert "rover1" not in caster.get_rover_positions("BASE1")

    async def test_rover_username_used_as_position_key(
        self, app: Sanic, caster: NTRIPCaster
    ) -> None:
        """The position is stored under the rover's authenticated username."""
        set_calls: list[str] = []
        original_set = caster.set_rover_position

        def _track(mp: str, conn_id: str, lat: float, lon: float) -> None:
            set_calls.append(conn_id)
            original_set(mp, conn_id, lat, lon)

        caster.set_rover_position = _track  # type: ignore[method-assign]

        await self._connect_and_close(
            app, caster, {"Ntrip-GGA": _VALID_GGA}, username="myrobot"
        )

        assert "myrobot" in set_calls