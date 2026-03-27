r"""
Cookie management utilities for HTTP requests.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sanic.exceptions import SanicException


if TYPE_CHECKING:
    from sanic import HTTPResponse


def set(
    response: HTTPResponse,
    key: str,
    value: str,
    max_age: int = 600,
    secure: bool = True,
    httponly: bool = True,
    samesite: str = "Lax",
    path: str = "/",
    domain: str | None = None,
    partitioned: bool = False,
) -> HTTPResponse:
    """
    Set a cookie in a Sanic HTTP response using secure defaults and validation.

    Args:
        response: The Sanic HTTP response object.
        key: The name of the cookie (must be a valid string).
        value: The value of the cookie (must be a valid string).
        max_age: The maximum age of the cookie in seconds (default: 600).
        secure: Whether the cookie should be marked as secure (default: True).
        httponly: Whether the cookie should be marked as HttpOnly to prevent JavaScript access (default: True).
        samesite: The SameSite attribute for CSRF protection ('Strict', 'Lax', or 'None') (default: 'Lax').
        path: The path for which the cookie is valid (default: '/').
        domain: The domain for which the cookie is valid (optional, must start with a dot if specified).
        partitioned: Whether to mark the cookie as partitioned (default: False).

    Raises:
        SanicException: If the cookie key, value, or other parameters are invalid.
    """
    # Validate inputs
    if not isinstance(key, str) or not key.strip():
        raise SanicException("Cookie key must be a non-empty string")
    if not isinstance(value, str):
        raise SanicException("Cookie value must be a string")
    if not isinstance(max_age, int) or max_age < 0:
        raise SanicException("max_age must be a non-negative integer")
    if samesite not in ("Strict", "Lax", "None"):
        raise SanicException("SameSite must be 'Strict', 'Lax', or 'None'")
    if domain is not None and not domain.startswith("."):
        raise SanicException("Domain must start with a dot if specified")

    # Set cookie using Sanic's add_cookie method
    response.add_cookie(
        key=key,
        value=value,
        max_age=max_age,
        secure=secure,
        httponly=httponly,
        samesite=samesite,
        path=path,
        domain=domain,
        partitioned=partitioned,
    )

    return response


def delete(
    response: HTTPResponse,
    key: str,
    path: str = "/",
    domain: str | None = None,
) -> HTTPResponse:
    """
    Delete a cookie in a Sanic HTTP response by setting it to expire immediately.

    Args:
        response: The Sanic HTTP response object.
        key: The name of the cookie to delete.
        path: The path for which the cookie was valid (default: '/').
        domain: The domain for which the cookie was valid (optional).

    Raises:
        SanicException: If the cookie key is invalid.
    """
    # Validate inputs
    if not isinstance(key, str) or not key.strip():
        raise SanicException("Cookie key must be a non-empty string")
    if domain is not None and not domain.startswith("."):
        raise SanicException("Domain must start with a dot if specified")

    # Delete cookie using Sanic's delete_cookie method
    response.delete_cookie(key=key, path=path, domain=domain)

    return response
