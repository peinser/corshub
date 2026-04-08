"""
Integration tests for NMEA GGA validation on rover and nearest routes.

Routes under test:
    GET /<mountpoint>   →  rover stream; validates Ntrip-GGA when mountpoint.nmea=True
    GET /NEAR           →  nearest mountpoint stream; always requires Ntrip-GGA
    GET /NEAREST        →  alias for /NEAR
    GET /NSB            →  alias for /NEAR

NMEA validation rules:
    - If mountpoint.nmea is False the Ntrip-GGA header is ignored entirely.
    - If mountpoint.nmea is True the header must be a valid GGA sentence (400 otherwise).
    - If mountpoint.mask > 0 the rover must be within that many km of the
      mountpoint's configured position (400 otherwise).

Nearest-mountpoint rules:
    - Ntrip-GGA is always required (400 if absent or invalid).
    - The nearest mountpoint within each mountpoint's mask is selected.
    - 404 when no mountpoint lies within range.
"""

from __future__ import annotations

import asyncio
import base64

import pytest

from sanic import Sanic

from corshub.ntrip.v2.caster import NTRIPCaster
from corshub.services.v1.ntrip import service as ntrip_service

ntrip_blueprint = ntrip_service.blueprint()


def _basic_auth(username: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()


def _gga(lat: float, lon: float) -> str:
    """Build a minimal valid GPGGA sentence for the given WGS-84 position."""
    lat_deg = int(abs(lat))
    lat_min = (abs(lat) - lat_deg) * 60
    lon_deg = int(abs(lon))
    lon_min = (abs(lon) - lon_deg) * 60
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    body = (
        f"GPGGA,120000.00,"
        f"{lat_deg:02d}{lat_min:08.5f},{ns},"
        f"{lon_deg:03d}{lon_min:08.5f},{ew},"
        f"1,08,1.0,0.0,M,0.0,M,,"
    )
    checksum = 0
    for ch in body:
        checksum ^= ord(ch)
    return f"${body}*{checksum:02X}"


async def _end_stream(caster: NTRIPCaster) -> None:
    """Terminate any active rover stream on *caster*.

    Polls all mountpoint transports until at least one subscriber has connected
    (indicating the server accepted the request with 200 and started streaming),
    then signals every subscriber to stop by putting the sentinel in its queue.
    This unblocks the streaming handler and lets asgi_client.get() return.

    Schedule this as a background task *before* awaiting the streaming request:

        asyncio.create_task(_end_stream(caster))
        _, response = await app.asgi_client.get(...)
    """
    while True:
        for transport in caster._transports.values():
            if transport._queues:
                for sub in list(transport._queues):
                    sub.shutdown()
                return
        await asyncio.sleep(0)


NTRIP_H = {"Ntrip-Version": "Ntrip/2.0"}
AUTH = _basic_auth("BASE1", "s3cr3t")

# Brussels — used as the reference mountpoint position throughout.
MP_LAT, MP_LON = 50.8503, 4.3517

# A point ~10 km north-east of Brussels, well within any 50 km mask.
NEAR_LAT, NEAR_LON = 50.9300, 4.5000

# A point ~260 km from Brussels (Paris), well outside a 50 km mask.
FAR_LAT, FAR_LON = 48.8566, 2.3522


def _make_app(caster: NTRIPCaster) -> Sanic:
    app = Sanic(f"test_{id(caster)}")
    app.blueprint(ntrip_blueprint)

    @app.before_server_start
    async def _set_caster(app: Sanic, _: object) -> None:
        # Runs after the blueprint's own before_server_start, overriding the
        # default NTRIPCaster that base.py creates with the test-controlled one.
        app.ctx.ntrip_caster = caster

    return app


@pytest.fixture
async def nmea_caster() -> NTRIPCaster:
    """Single mountpoint at Brussels with nmea=True and no mask."""
    c = NTRIPCaster()
    await c.register(
        name="BASE1", identifier="BASE1", username="BASE1", password="s3cr3t",
        format="RTCM 3.3", country="BEL", latitude=MP_LAT, longitude=MP_LON,
        nmea=True,
    )
    return c


@pytest.fixture
async def masked_caster() -> NTRIPCaster:
    """Single mountpoint at Brussels with nmea=True and a 50 km mask."""
    c = NTRIPCaster()
    await c.register(
        name="BASE1", identifier="BASE1", username="BASE1", password="s3cr3t",
        format="RTCM 3.3", country="BEL", latitude=MP_LAT, longitude=MP_LON,
        nmea=True, mask=50.0,
    )
    return c


@pytest.fixture
def nmea_app(nmea_caster: NTRIPCaster) -> Sanic:
    return _make_app(nmea_caster)


@pytest.fixture
def masked_app(masked_caster: NTRIPCaster) -> Sanic:
    return _make_app(masked_caster)


# ── Rover route — mountpoint.nmea = False ─────────────────────────────────────

class TestRoverRouteNmeaDisabled:
    """When nmea=False the Ntrip-GGA header is entirely optional."""

    async def test_no_gga_header_still_connects(self, app: Sanic, caster: NTRIPCaster) -> None:
        asyncio.create_task(_end_stream(caster))
        _, response = await app.asgi_client.get(
            "/BASE1",
            headers={**NTRIP_H, "Authorization": AUTH},
        )
        assert response.status_code == 200

    async def test_valid_gga_header_accepted_silently(self, app: Sanic, caster: NTRIPCaster) -> None:
        asyncio.create_task(_end_stream(caster))
        _, response = await app.asgi_client.get(
            "/BASE1",
            headers={**NTRIP_H, "Authorization": AUTH, "Ntrip-GGA": _gga(NEAR_LAT, NEAR_LON)},
        )
        assert response.status_code == 200

    async def test_invalid_gga_header_ignored(self, app: Sanic, caster: NTRIPCaster) -> None:
        asyncio.create_task(_end_stream(caster))
        _, response = await app.asgi_client.get(
            "/BASE1",
            headers={**NTRIP_H, "Authorization": AUTH, "Ntrip-GGA": "garbage"},
        )
        assert response.status_code == 200


# ── Rover route — mountpoint.nmea = True, no mask ────────────────────────────

class TestRoverRouteNmeaRequired:

    async def test_missing_gga_returns_400(self, nmea_app: Sanic) -> None:
        _, response = await nmea_app.asgi_client.get(
            "/BASE1",
            headers={**NTRIP_H, "Authorization": AUTH},
        )
        assert response.status_code == 400

    async def test_invalid_gga_returns_400(self, nmea_app: Sanic) -> None:
        _, response = await nmea_app.asgi_client.get(
            "/BASE1",
            headers={**NTRIP_H, "Authorization": AUTH, "Ntrip-GGA": "not a sentence"},
        )
        assert response.status_code == 400

    async def test_bad_checksum_gga_returns_400(self, nmea_app: Sanic) -> None:
        _, response = await nmea_app.asgi_client.get(
            "/BASE1",
            headers={**NTRIP_H, "Authorization": AUTH,
                     "Ntrip-GGA": "$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*00"},
        )
        assert response.status_code == 400

    async def test_non_gga_nmea_sentence_returns_400(self, nmea_app: Sanic) -> None:
        _, response = await nmea_app.asgi_client.get(
            "/BASE1",
            headers={**NTRIP_H, "Authorization": AUTH,
                     "Ntrip-GGA": "$GPGLL,4807.038,N,01131.000,E,123519,A*26"},
        )
        assert response.status_code == 400

    async def test_valid_gga_nearby_returns_200(self, nmea_app: Sanic, nmea_caster: NTRIPCaster) -> None:
        asyncio.create_task(_end_stream(nmea_caster))
        _, response = await nmea_app.asgi_client.get(
            "/BASE1",
            headers={**NTRIP_H, "Authorization": AUTH, "Ntrip-GGA": _gga(NEAR_LAT, NEAR_LON)},
        )
        assert response.status_code == 200

    async def test_valid_gga_far_away_returns_200_when_no_mask(
        self, nmea_app: Sanic, nmea_caster: NTRIPCaster
    ) -> None:
        # Without a mask, any valid GGA passes regardless of distance.
        asyncio.create_task(_end_stream(nmea_caster))
        _, response = await nmea_app.asgi_client.get(
            "/BASE1",
            headers={**NTRIP_H, "Authorization": AUTH, "Ntrip-GGA": _gga(FAR_LAT, FAR_LON)},
        )
        assert response.status_code == 200


# ── Rover route — mountpoint.nmea = True, mask = 50 km ───────────────────────

class TestRoverRouteNmeaMasked:

    async def test_rover_outside_mask_returns_400(self, masked_app: Sanic) -> None:
        _, response = await masked_app.asgi_client.get(
            "/BASE1",
            headers={**NTRIP_H, "Authorization": AUTH, "Ntrip-GGA": _gga(FAR_LAT, FAR_LON)},
        )
        assert response.status_code == 400

    async def test_missing_gga_returns_400_regardless_of_mask(self, masked_app: Sanic) -> None:
        _, response = await masked_app.asgi_client.get(
            "/BASE1",
            headers={**NTRIP_H, "Authorization": AUTH},
        )
        assert response.status_code == 400

    async def test_rover_within_mask_returns_200(
        self, masked_app: Sanic, masked_caster: NTRIPCaster
    ) -> None:
        asyncio.create_task(_end_stream(masked_caster))
        _, response = await masked_app.asgi_client.get(
            "/BASE1",
            headers={**NTRIP_H, "Authorization": AUTH, "Ntrip-GGA": _gga(NEAR_LAT, NEAR_LON)},
        )
        assert response.status_code == 200

    async def test_rover_at_mountpoint_position_returns_200(
        self, masked_app: Sanic, masked_caster: NTRIPCaster
    ) -> None:
        asyncio.create_task(_end_stream(masked_caster))
        _, response = await masked_app.asgi_client.get(
            "/BASE1",
            headers={**NTRIP_H, "Authorization": AUTH, "Ntrip-GGA": _gga(MP_LAT, MP_LON)},
        )
        assert response.status_code == 200


# ── Mountpoint.mask validation ────────────────────────────────────────────────

class TestMountpointMaskField:

    def test_default_mask_is_zero(self) -> None:
        from corshub.ntrip.v2.caster import Mountpoint
        mp = Mountpoint(
            name="X", identifier="X", username="u", password="p",
            format="RTCM 3.3", country="BEL", latitude=50.0, longitude=4.0,
        )
        assert mp.mask == 0.0

    def test_positive_mask_is_accepted(self) -> None:
        from corshub.ntrip.v2.caster import Mountpoint
        mp = Mountpoint(
            name="X", identifier="X", username="u", password="p",
            format="RTCM 3.3", country="BEL", latitude=50.0, longitude=4.0,
            mask=100.0,
        )
        assert mp.mask == 100.0

    def test_zero_mask_is_accepted(self) -> None:
        from corshub.ntrip.v2.caster import Mountpoint
        Mountpoint(
            name="X", identifier="X", username="u", password="p",
            format="RTCM 3.3", country="BEL", latitude=50.0, longitude=4.0,
            mask=0.0,
        )  # must not raise

    def test_negative_mask_raises(self) -> None:
        from corshub.ntrip.v2.caster import Mountpoint
        with pytest.raises(ValueError, match="[Mm]ask"):
            Mountpoint(
                name="X", identifier="X", username="u", password="p",
                format="RTCM 3.3", country="BEL", latitude=50.0, longitude=4.0,
                mask=-1.0,
            )


# ── Nearest routes ────────────────────────────────────────────────────────────

@pytest.fixture
async def two_mountpoint_caster() -> NTRIPCaster:
    """Two mountpoints: BASE1 near (50.0, 4.0), BASE2 far (52.0, 4.0)."""
    c = NTRIPCaster()
    await c.register(
        name="BASE1", identifier="BASE1", username="BASE1", password="s3cr3t",
        format="RTCM 3.3", country="BEL", latitude=50.0, longitude=4.0,
    )
    await c.register(
        name="BASE2", identifier="BASE2", username="BASE2", password="s3cr3t",
        format="RTCM 3.3", country="NLD", latitude=52.0, longitude=4.0,
    )
    return c


@pytest.fixture
async def masked_two_caster() -> NTRIPCaster:
    """Two mountpoints each with a 30 km mask; BASE1 south, BASE2 north."""
    c = NTRIPCaster()
    await c.register(
        name="BASE1", identifier="BASE1", username="BASE1", password="s3cr3t",
        format="RTCM 3.3", country="BEL", latitude=50.0, longitude=4.0, mask=30.0,
    )
    await c.register(
        name="BASE2", identifier="BASE2", username="BASE2", password="s3cr3t",
        format="RTCM 3.3", country="NLD", latitude=52.0, longitude=4.0, mask=30.0,
    )
    return c


@pytest.fixture
def two_app(two_mountpoint_caster: NTRIPCaster) -> Sanic:
    return _make_app(two_mountpoint_caster)


@pytest.fixture
def masked_two_app(masked_two_caster: NTRIPCaster) -> Sanic:
    return _make_app(masked_two_caster)


class TestNearestRoute:

    @pytest.mark.parametrize("path", ["/NEAR", "/NEAREST", "/NSB"])
    async def test_missing_gga_returns_400(self, path: str, two_app: Sanic) -> None:
        _, response = await two_app.asgi_client.get(
            path,
            headers={**NTRIP_H, "Authorization": AUTH},
        )
        assert response.status_code == 400

    @pytest.mark.parametrize("path", ["/NEAR", "/NEAREST", "/NSB"])
    async def test_invalid_gga_returns_400(self, path: str, two_app: Sanic) -> None:
        _, response = await two_app.asgi_client.get(
            path,
            headers={**NTRIP_H, "Authorization": AUTH, "Ntrip-GGA": "garbage"},
        )
        assert response.status_code == 400

    async def test_empty_caster_returns_404(self) -> None:
        c = NTRIPCaster()
        app = _make_app(c)
        _, response = await app.asgi_client.get(
            "/NEAR",
            headers={**NTRIP_H, "Authorization": AUTH, "Ntrip-GGA": _gga(50.0, 4.0)},
        )
        assert response.status_code == 404

    async def test_rover_outside_all_masks_returns_404(self, masked_two_app: Sanic) -> None:
        # Paris is >200 km from both mountpoints, far outside the 30 km masks.
        _, response = await masked_two_app.asgi_client.get(
            "/NEAR",
            headers={**NTRIP_H, "Authorization": AUTH, "Ntrip-GGA": _gga(FAR_LAT, FAR_LON)},
        )
        assert response.status_code == 404

    async def test_selects_nearest_mountpoint(
        self, two_app: Sanic, two_mountpoint_caster: NTRIPCaster
    ) -> None:
        # Rover at (50.1, 4.0) → ~11 km from BASE1, ~211 km from BASE2
        asyncio.create_task(_end_stream(two_mountpoint_caster))
        _, response = await two_app.asgi_client.get(
            "/NEAR",
            headers={**NTRIP_H, "Authorization": AUTH, "Ntrip-GGA": _gga(50.1, 4.0)},
        )
        assert response.status_code == 200

    async def test_rover_within_one_mask_returns_200(
        self, masked_two_app: Sanic, masked_two_caster: NTRIPCaster
    ) -> None:
        # (50.1, 4.0) is ~11 km from BASE1 (within 30 km mask) and ~211 km from BASE2.
        asyncio.create_task(_end_stream(masked_two_caster))
        _, response = await masked_two_app.asgi_client.get(
            "/NEAR",
            headers={**NTRIP_H, "Authorization": AUTH, "Ntrip-GGA": _gga(50.1, 4.0)},
        )
        assert response.status_code == 200

    async def test_nearest_aliases_return_same_status(
        self, two_app: Sanic, two_mountpoint_caster: NTRIPCaster
    ) -> None:
        gga = _gga(50.1, 4.0)
        results = []
        for path in ("/NEAR", "/NEAREST", "/NSB"):
            asyncio.create_task(_end_stream(two_mountpoint_caster))
            _, response = await two_app.asgi_client.get(
                path,
                headers={**NTRIP_H, "Authorization": AUTH, "Ntrip-GGA": gga},
            )
            results.append(response.status_code)
        assert results == [200, 200, 200]
