"""Tests for the RTCM HTTP endpoints (session bootstrap, JWKS) and config."""

from __future__ import annotations

import json

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from sanic.exceptions import NotFound
from sanic.exceptions import Unauthorized

from corshub.exceptions.http import BadRequestError
from corshub.rtcm.keys import SigningKey
from corshub.rtcm.tokens import verify_session_token
from corshub.services.v1.rtcm.base import RTCMConfig
from corshub.services.v1.rtcm.base import load_config
from corshub.services.v1.rtcm.jwks import jwks
from corshub.services.v1.rtcm.session import create_session


_SECRET = "rtcm-token-secret-at-least-32-bytes-long"


def _config(*, enabled: bool = True, signing: bool = True) -> RTCMConfig:
    return RTCMConfig(
        enabled=enabled,
        signing_enabled=signing,
        udp_host="127.0.0.1",
        udp_port=5009,
        udp_endpoint="caster.example:5009",
        token_secret=_SECRET,
        token_ttl=60,
        session_ttl=30.0,
        keepalive_interval=10,
        allow_ephemeral_key=True,
    )


def _request(*, config, credentials, json_body, signing_key=None, authenticate=(True, None)):
    caster = SimpleNamespace(authenticate_rover=AsyncMock(return_value=authenticate))
    ctx = SimpleNamespace(rtcm_config=config, ntrip_caster=caster, rtcm_signing_key=signing_key)
    return SimpleNamespace(app=SimpleNamespace(ctx=ctx), credentials=credentials, json=json_body)


def _creds(username: str = "rover1", password: str = "secret"):
    return SimpleNamespace(username=username, password=password)


class TestSessionEndpoint:
    async def test_valid_returns_signed_token(self) -> None:
        key = SigningKey.generate()
        req = _request(config=_config(), credentials=_creds(), json_body={"mountpoint": "BASE1"}, signing_key=key)
        resp = await create_session(req)
        body = json.loads(resp.body)

        claims = verify_session_token(body["token"], _SECRET)
        assert claims["mountpoint"] == "BASE1"
        assert claims["sub"] == "rover1"
        assert body["udp_endpoint"] == "caster.example:5009"
        assert body["signing_kid"] == key.kid

    async def test_signing_disabled_kid_is_null(self) -> None:
        req = _request(config=_config(signing=False), credentials=_creds(), json_body={"mountpoint": "BASE1"})
        body = json.loads((await create_session(req)).body)
        assert body["signing_kid"] is None

    async def test_disabled_feature_404(self) -> None:
        req = _request(config=_config(enabled=False), credentials=_creds(), json_body={"mountpoint": "BASE1"})
        with pytest.raises(NotFound):
            await create_session(req)

    async def test_missing_credentials_401(self) -> None:
        req = _request(config=_config(), credentials=None, json_body={"mountpoint": "BASE1"})
        with pytest.raises(Unauthorized):
            await create_session(req)

    async def test_missing_mountpoint_400(self) -> None:
        req = _request(config=_config(), credentials=_creds(), json_body={})
        with pytest.raises(BadRequestError):
            await create_session(req)

    async def test_invalid_credentials_401(self) -> None:
        req = _request(
            config=_config(),
            credentials=_creds(),
            json_body={"mountpoint": "BASE1"},
            authenticate=(False, None),
        )
        with pytest.raises(Unauthorized):
            await create_session(req)


class TestJwksEndpoint:
    async def test_returns_jwks_when_enabled(self) -> None:
        key = SigningKey.generate()
        ctx = SimpleNamespace(rtcm_signing_key=key)
        req = SimpleNamespace(app=SimpleNamespace(ctx=ctx))
        resp = await jwks(req)
        body = json.loads(resp.body)
        assert body["keys"][0]["kid"] == key.kid
        assert body["keys"][0]["kty"] == "OKP"
        assert resp.content_type == "application/jwk-set+json"

    async def test_404_when_signing_disabled(self) -> None:
        ctx = SimpleNamespace(rtcm_signing_key=None)
        req = SimpleNamespace(app=SimpleNamespace(ctx=ctx))
        with pytest.raises(NotFound):
            await jwks(req)


class TestLoadConfig:
    def test_disabled_by_default(self, monkeypatch) -> None:
        for key in ("RTCM_UDP_ENABLED", "RTCM_UDP_SIGNING_ENABLED", "RTCM_SESSION_TOKEN_SECRET"):
            monkeypatch.delenv(key, raising=False)
        config = load_config()
        assert config.enabled is False
        assert config.signing_enabled is False

    def test_enabled_requires_token_secret(self, monkeypatch) -> None:
        monkeypatch.setenv("RTCM_UDP_ENABLED", "true")
        monkeypatch.delenv("RTCM_SESSION_TOKEN_SECRET", raising=False)
        with pytest.raises(ValueError, match="RTCM_SESSION_TOKEN_SECRET"):
            load_config()

    def test_enabled_with_secret(self, monkeypatch) -> None:
        monkeypatch.setenv("RTCM_UDP_ENABLED", "on")
        monkeypatch.setenv("RTCM_UDP_SIGNING_ENABLED", "1")
        monkeypatch.setenv("RTCM_SESSION_TOKEN_SECRET", _SECRET)
        monkeypatch.setenv("RTCM_UDP_PORT", "6000")
        config = load_config()
        assert config.enabled is True
        assert config.signing_enabled is True
        assert config.udp_port == 6000
        assert config.token_secret == _SECRET

    def test_flag_parsing_false_values(self, monkeypatch) -> None:
        monkeypatch.setenv("RTCM_UDP_ENABLED", "false")
        assert load_config().enabled is False
        monkeypatch.setenv("RTCM_UDP_ENABLED", "0")
        assert load_config().enabled is False
        monkeypatch.setenv("RTCM_UDP_ENABLED", "nonsense")
        assert load_config().enabled is False
