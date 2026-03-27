r"""
A module for managing JSON Web Tokens (JWTs) and JSON Web Key Sets (JWKS).
"""

from __future__ import annotations

import asyncio
import time

from dataclasses import dataclass
from typing import TYPE_CHECKING

import jwt

from cachetools import TTLCache
from jwt import InvalidTokenError

from corshub.http.sessions import HTTPRequestManager
from corshub.oidc.constants import AUD
from corshub.oidc.constants import CLIENT_ID
from corshub.oidc.constants import ISS
from corshub.oidc.constants import JWKS


if TYPE_CHECKING:
    from typing import Final

    from sanic import Sanic


@dataclass(frozen=True)
class JWKProvider:
    client_id: str
    iss: str
    aud: str
    token: str


class ServiceAccountTokenManager:
    """Manages Access Token management for the backend services."""

    TOKEN_CACHE_KEY: Final[str] = "access_token"

    def __init__(self, configuration: dict):
        self._aud = configuration["aud"]
        self._token_endpoint = configuration["token_endpoint"]
        self._client_id = configuration["client_id"]
        self._username = configuration["credentials"]["username"]
        self._password = configuration["credentials"]["password"]
        self._cache = TTLCache(maxsize=1, ttl=3600)  # default TTL, updated after initial token fetch

    async def _fetch_token(self) -> str:
        """
        Fetches a new access token from the OIDC provider using the client credentials.
        """
        async with HTTPRequestManager.__session__.post(
            self._token_endpoint,
            data={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "username": self._username,
                "password": self._password,
                "scope": "email",  # Necessary for retrieving the subject.
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            raise_for_status=True,
        ) as response:
            payload = await response.json()

        access_token = payload.get("access_token")
        expires_in = payload.get("expires_in", 3600)

        # Cache token with correct TTL
        self._cache[self.TOKEN_CACHE_KEY] = (access_token, time.time() + expires_in - 60)  # 60s early expiration

        return access_token

    @property
    def aud(self) -> str:
        return self._aud

    @property
    async def token(self) -> str:
        """
        Retrieves a valid access token, either from cache or by requesting a new one.
        """
        token_data = self._cache.get(self.TOKEN_CACHE_KEY)

        if token_data:
            token, expiry = token_data
            if expiry > time.time():
                return token

        return await self._fetch_token()

    @staticmethod
    async def register(app: Sanic, configuration: dict):
        """
        Registers the TokenManager instance in the Sanic app context.
        """
        if not hasattr(app.ctx, "token_manager"):
            app.ctx.sa_token_manager = ServiceAccountTokenManager(configuration)


class JWKSManager:
    """Manages JWT verification using JWKS from external issuers."""

    TOKEN: Final[str] = "token"

    def __init__(self, providers: list[dict], cache_ttl: int = 3600):
        """
        Initializes the JWT verification manager.

        :param providers: List of provider dictionaries containing JWKS URLs.
        :param cache_ttl: Time-to-live (TTL) for JWKS cache in seconds (default: 3600 seconds).
        """
        self._providers = providers
        self._jwks_metadata = {}
        self._jwks_cache = TTLCache(maxsize=32, ttl=cache_ttl)

    async def _fetch_issuer_jwks(self, provider: dict) -> list:
        """Fetches JWKS from the specified provider."""
        async with HTTPRequestManager.__session__.get(
            provider[JWKS],
            raise_for_status=True,
            timeout=5.0,
        ) as response:
            return await response.json()

    async def populate(self) -> None:
        """
        Populates the JWKS cache by fetching keys from all providers.

        Note, JWK providers are searchable by kid and aud key.
        """
        jwks_per_provider = await asyncio.gather(*(self._fetch_issuer_jwks(provider) for provider in self._providers))
        cache = self._jwks_cache  # A little bit easier to read.
        metadata = self._jwks_metadata

        for provider, provider_jwks in zip(self._providers, jwks_per_provider, strict=False):
            # Allocate the provider and register the provider by audience.
            aud = provider[AUD]
            _provider = JWKProvider(
                client_id=provider[CLIENT_ID],
                iss=provider[ISS],
                aud=aud,
                token=provider["token"],
            )

            metadata[aud] = _provider

            for key in provider_jwks.get("keys", []):
                kid = key.get("kid")
                if kid:
                    # A provider and it's public key are searchable by the `kid` as well. This of course
                    # imposes the constraint that a kid should be unique among the set of configured providers.
                    # Typically, this is the case.
                    cache[kid] = jwt.algorithms.RSAAlgorithm.from_jwk(key)
                    metadata[kid] = _provider

    def provider(self, aud: str):
        r"""
        Returns the JWKProvider based on the configured aud. Note, this method doesn't require population since
        the metadata and caches are loaded on startup.
        """
        return self._jwks_metadata.get(aud, None)

    async def public_key(self, key: str):
        """
        Retrieves the public key for the given Key ID (kid) from JWKS or `aud` (Audience) claim,
        and the associated JWK provider.
        """
        if key:
            if key not in self._jwks_cache:
                await self.populate()
                if key not in self._jwks_cache:
                    raise InvalidTokenError("Signing Key ID not found in issuer JWKS.")

            return self._jwks_metadata[key], self._jwks_cache[key]

    async def validate(self, token: str) -> dict:
        """Validates a JWT asynchronously using the issuer's public key."""
        unverified_header = jwt.get_unverified_header(token)
        kid = unverified_header.get("kid")

        if not kid:
            raise InvalidTokenError("Invalid JWT: Missing 'kid' in header")

        provider, public_key = await self.public_key(kid)

        return jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            audience=provider.aud,
            issuer=provider.iss,
            options={
                "verify_signature": True,
                "verify_aud": True,
                "verify_iss": True,
                "verify_exp": True,
            },
        )

    @staticmethod
    async def register(app: Sanic, providers: list[dict]):
        """Registers the JWKSManager instance in the Sanic app context."""
        if not hasattr(app.ctx, "jwks_manager"):
            app.ctx.jwks_manager = JWKSManager(providers)
            await app.ctx.jwks_manager.populate()


def unverified_claims(token: str) -> dict:
    """
    Extracts unverified claims from a JWT without signature verification.

    :param token: The JWT to decode.
    :return: A dictionary containing the unverified claims.
    :raises InvalidTokenError: If the token is malformed or cannot be decoded.
    """
    try:
        return jwt.decode(
            token,
            options={
                "verify_signature": False,
                "verify_aud": False,
                "verify_iss": False,
                "verify_exp": True,
            },
        )

    except jwt.InvalidTokenError as ex:
        raise InvalidTokenError(f"Failed to decode token: {ex!s}") from ex
