r"""
HTTP specific exceptions.
"""

from __future__ import annotations

from sanic.exceptions import BadRequest as BadRequestError
from sanic.exceptions import Forbidden as ForbiddenError
from sanic.exceptions import InternalServerError
from sanic.exceptions import NotFound as NotFoundError
from sanic.exceptions import RequestTimeout as TimeoutError
from sanic.exceptions import SanicException
from sanic.exceptions import ServerError
from sanic.exceptions import ServiceUnavailable
from sanic.exceptions import Unauthorized as UnauthorizedError


# Explicit re-export so these are part of the module's public API for both
# `from ... import X` consumers and strict type checkers (PEP 484 re-export).
__all__ = [
    "BadRequestError",
    "ConflictError",
    "ForbiddenError",
    "InternalServerError",
    "NotFoundError",
    "RateLimitedError",
    "SanicException",
    "ServerError",
    "ServiceUnavailable",
    "TimeoutError",
    "UnauthorizedError",
]


class ConflictError(SanicException):
    def __init__(
        self,
        message: str | bytes | None = None,
    ):
        super().__init__(
            message=message,
            status_code=409,  # HTTP Conflict
        )


class RateLimitedError(SanicException):
    def __init__(
        self,
        message: str | bytes | None = None,
    ):
        super().__init__(
            message=message,
            status_code=429,  # HTTP Too Many Requests
        )
