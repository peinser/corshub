"""
Integration tests for NTRIP v2 HTTP routes.

Routes under test (registered via the ntrip blueprint):

    GET  /              → NTRIP source table  (Content-Type: gnss/sourcetable)
    PUT  /<mountpoint>  → base-station stream (authenticated with Basic auth)
    GET  /<mountpoint>  → rover stream        (authenticated with Basic auth)

Auth convention tested here:
    Basic auth where username=mountpoint_name, password=mountpoint_password.
    Both base-station (PUT) and rover (GET) connections use the same credential
    pair in these tests; the implementation may later add a separate user table.

NTRIP v2 requires the header:
    Ntrip-Version: Ntrip/2.0

The caster must reject connections that omit it with 400 Bad Request.

Streaming responses (PUT / GET /<mountpoint>) keep the TCP connection open
indefinitely.  The tests verify the initial handshake only: status code,
response headers, without waiting for the stream to end.
"""

from __future__ import annotations

import base64

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

from sanic import Sanic


if TYPE_CHECKING:
    from corshub.ntrip.v2.caster import NTRIPCaster


def _basic_auth(username: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()


NTRIP_HEADERS = {
    "Ntrip-Version": "Ntrip/2.0",
}

VALID_AUTH = _basic_auth("BASE1", "s3cr3t")
WRONG_AUTH = _basic_auth("BASE1", "wrong")

VALID_NTRIP_STR = "STR;RTCM3EPH;RTCM 3.x Ephemeris;RTCM 3.2;1004(1),1012(1),1021(1),1022(1),1023(1),1024(1),1033(1);GNSS;GPS+GLO+BDS+GAL;QZSS;0.00;0.00;0.00;0.00;IGS;0;RTCM;none;B;N;9600;none"

# Note: the name in the NTRIP-STR header will be ignored, the mountpoint name will always be taken.
INVALID_NTRIP_STR = "STR;Quelfes, PT;RTCM 3.x Ephemeris;RTCM 3.2;1004(1),1012(1);GNSS;GPS+GLO;0.00;0.00;0.00;0.00;IGS;0;RTCM;none;B;N;9600;none;EXTRA_FIELD"


class TestSourceTableRoute:
    async def test_returns_200(self, app: Sanic) -> None:
        _, response = await app.asgi_client.get("/", headers=NTRIP_HEADERS)
        assert response.status_code == 200

    async def test_content_type_is_gnss_sourcetable(self, app: Sanic) -> None:
        _, response = await app.asgi_client.get("/", headers=NTRIP_HEADERS)
        assert "gnss/sourcetable" in response.headers.get("content-type", "")

    async def test_body_ends_with_endsourcetable(self, app: Sanic) -> None:
        _, response = await app.asgi_client.get("/", headers=NTRIP_HEADERS)
        assert response.text.endswith("ENDSOURCETABLE\r\n")

    async def test_registered_mountpoint_appears_in_body(self, app: Sanic) -> None:
        _, response = await app.asgi_client.get("/", headers=NTRIP_HEADERS)
        assert "BASE1" in response.text


class TestSourceRoute:
    async def test_missing_ntrip_version_header_returns_400(self, app: Sanic) -> None:
        _, response = await app.asgi_client.put(
            "/BASE1",
            headers={"Authorization": VALID_AUTH, "Content-Type": "gnss/data"},
            content=b"",
        )
        assert response.status_code == 400

    async def test_wrong_content_type_returns_400(self, app: Sanic) -> None:
        _, response = await app.asgi_client.put(
            "/BASE1",
            headers={**NTRIP_HEADERS, "Authorization": VALID_AUTH, "Content-Type": "application/json"},
            content=b"",
        )
        assert response.status_code == 400

    async def test_incorrect_gga(self, app: Sanic) -> None:
        _, response = await app.asgi_client.put(
            "/this is not correct",
            headers={
                **NTRIP_HEADERS,
                "Authorization": VALID_AUTH,
                "Content-Type": "gnss/data",
                "Ntrip-STR": INVALID_NTRIP_STR,
            },
            content=b"",
        )
        assert response.status_code == 400

    async def test_correct_gga(self, app: Sanic, caster: NTRIPCaster) -> None:
        await caster.close("BASE1")  # A base station can only publish to an offline mountpoint.
        _, response = await app.asgi_client.put(
            "/BASE1",
            headers={
                **NTRIP_HEADERS,
                "Authorization": VALID_AUTH,
                "Content-Type": "gnss/data",
                "Ntrip-STR": VALID_NTRIP_STR,
            },
            content=b"",
        )

        assert response.status_code == 200

    async def test_second_publisher_on_live_mountpoint_returns_409(self, app: Sanic, caster: NTRIPCaster) -> None:
        # BASE1 already has a live transport: another base station is publishing.
        _, response = await app.asgi_client.put(
            "/BASE1",
            headers={**NTRIP_HEADERS, "Authorization": VALID_AUTH, "Content-Type": "gnss/data"},
            content=b"",
        )
        assert response.status_code == 409
        assert await caster.available("BASE1") is True  # The live publisher is untouched.


class TestQualityRoute:
    async def test_does_not_expose_rover_identities_or_positions(self, app: Sanic, caster: NTRIPCaster) -> None:
        caster.set_rover_position("BASE1", "rover-secret-name", 51.0543, 3.7174)
        _, response = await app.asgi_client.get("/quality/BASE1")
        assert response.status_code == 200
        assert "rover_positions" not in response.json
        assert "rover-secret-name" not in response.text
        assert "51.0543" not in response.text

    async def test_unknown_mountpoint_returns_404(self, app: Sanic) -> None:
        _, response = await app.asgi_client.get("/quality/UNKNOWN")
        assert response.status_code == 404


class TestVerificationFromAuth:
    async def test_put_takes_verified_from_auth_not_the_station(self, app: Sanic, caster: NTRIPCaster) -> None:
        await caster.close("BASE1")
        caster.authenticate_base_station = AsyncMock(return_value=(True, False))
        _, response = await app.asgi_client.put(
            "/BASE1",
            headers={**NTRIP_HEADERS, "Authorization": VALID_AUTH, "Content-Type": "gnss/data"},
            content=b"",
        )
        assert response.status_code == 200
        assert caster.mountpoints["BASE1"].verified is False

    async def test_quality_reports_verification(self, app: Sanic) -> None:
        _, response = await app.asgi_client.get("/quality/BASE1")
        assert response.json["verified"] is True
