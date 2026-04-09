r"""
Base OIDC configuration utilities.
"""

from __future__ import annotations

import asyncio

from typing import TYPE_CHECKING

import aiofiles
import orjson

from corshub import env
from corshub import jwt


if TYPE_CHECKING:
    from typing import Final

    from sanic import Sanic


OIDC_CONFIG_PATH: Final[str] = env.extract(
    key="OIDC_CONFIG_PATH",
    default="config/oidc.json",
    dtype=str,
)


async def _register_jwks(app: Sanic, providers: list[dict]) -> None:
    r"""
    Initializes the OIDC configuration according to the specificiation
    outlined in the OIDC configuration file.
    """
    # Register the various providers with JWKs endpoints.
    await jwt.JWKSManager.register(app, providers=[provider for provider in providers if "jwks" in provider])


async def _register_token_cache(app: Sanic, issuer: dict) -> None:
    await jwt.ServiceAccountTokenManager.register(app, issuer)


async def register(app: Sanic) -> None:
    # Load the providers from the OIDC configuration file.
    async with aiofiles.open(OIDC_CONFIG_PATH) as f:
        configuration = orjson.loads(await f.read())

    await asyncio.gather(
        _register_jwks(app=app, providers=configuration["providers"]),
        _register_token_cache(app=app, issuer=configuration["service_account"]),
    )
