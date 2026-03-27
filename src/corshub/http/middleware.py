r"""
Common HTTP middleware for Sanic.
"""

from __future__ import annotations

from functools import wraps
from typing import TYPE_CHECKING

import aiohttp
import jwt

from corshub.exceptions.http import BadRequestError
from corshub.exceptions.http import ForbiddenError
from corshub.exceptions.http import UnauthorizedError
from corshub.oidc.constants import COOKIE_ACCESS_TOKEN


if TYPE_CHECKING:
    from sanic import HTTPResponse
    from sanic import Request


def _enrich_token(request: Request) -> None:
    r"""
    This method enriches the request object to obtain the (access) token
    from various sources such as secure HttpOnly cookies.
    """
    # Check if the (access) token is provided as a Cookie.
    token = request.cookies.get(COOKIE_ACCESS_TOKEN)
    if token:
        # Mock a bearer token based on a Cookie to assure compatibility.
        request.headers.add("Authorization", f"Bearer {token}")


def ensure_json(f: callable) -> callable:
    r"""
    Decorator to ensure the request body is JSON.
    Raises a BadRequestError if the body is not JSON.
    """

    async def _wrapped(request: Request, **kwargs) -> HTTPResponse:
        if not request.json:
            raise BadRequestError("Request body must be JSON.")

        return await f(request, **kwargs)

    return _wrapped


def protected(allow_service_account: bool = False) -> None:
    r"""
    A decorator which indicates a Sanic route is protected. As of this moment
    the protection mechanism relies on the JWTManager to validate the supplied
    access token cookie or bearer against the provided OIDC configuration.
    """

    def decorator(f: callable) -> callable:
        @wraps(f)
        async def _wrapped(request: Request, **kwargs) -> HTTPResponse:
            # An access token cookie might be present.
            _enrich_token(request=request)

            if not request.credentials:
                raise UnauthorizedError

            token = request.credentials.token
            claims: dict | None = None

            try:
                # Check if the provided bearer token is a JWT or an Opaque Access Token.
                if token.count(".") == 2:
                    # JWT: Use configured JWKS validation.
                    # Update the request context with the claims.
                    claims = await request.app.ctx.jwks_manager.validate(token)

                else:
                    # TODO Support token introspection when required.
                    raise NotImplementedError

                # Check if the service account is accessing a route it is not allowed to access.
                _service_account_aud = request.app.ctx.sa_token_manager.aud
                is_service_account = claims.get("aud") == _service_account_aud
                if not allow_service_account and is_service_account:
                    raise ForbiddenError("Service Account is not authorized to access this route.")

                # Verify whether an email property is defined.
                subject = claims.get("email")
                if not subject:  # Both service accounts and users should have the `email` scope.
                    raise jwt.InvalidTokenError("E-mail is missing in token claims.")

                # Set the request context.
                request.ctx.claims = claims
                request.ctx.service_account = is_service_account
                request.ctx.subject = subject

            except jwt.DecodeError as ex:
                raise UnauthorizedError("Malformed JWT supplied.") from ex

            except jwt.InvalidTokenError as ex:
                raise UnauthorizedError(ex) from ex

            except jwt.PyJWKError as ex:
                raise UnauthorizedError from ex

            except aiohttp.client_exceptions.ClientResponseError as ex:
                raise UnauthorizedError("Invalid access token.") from ex

            return await f(request, **kwargs)

        return _wrapped

    return decorator
