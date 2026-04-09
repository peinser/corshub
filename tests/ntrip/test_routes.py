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
indefinitely.  The tests verify the initial handshake only — status code,
response headers — without waiting for the stream to end.
"""

from __future__ import annotations

import base64

import pytest

from sanic import Sanic


def _basic_auth(username: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()


NTRIP_HEADERS = {"Ntrip-Version": "Ntrip/2.0"}
VALID_AUTH = _basic_auth("BASE1", "s3cr3t")
WRONG_AUTH = _basic_auth("BASE1", "wrong")


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
            data=b"",
        )
        assert response.status_code == 400

    async def test_wrong_content_type_returns_400(self, app: Sanic) -> None:
        _, response = await app.asgi_client.put(
            "/BASE1",
            headers={**NTRIP_HEADERS, "Authorization": VALID_AUTH, "Content-Type": "application/json"},
            data=b"",
        )
        assert response.status_code == 400
