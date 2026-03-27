r"""
HTTP specific exceptions.
"""

from __future__ import annotations

from uuid import UUID

from sanic.exceptions import BadRequest as BadRequestError  # noqa: F401
from sanic.exceptions import Forbidden as ForbiddenError  # noqa: F401
from sanic.exceptions import InternalServerError  # noqa: F401
from sanic.exceptions import NotFound as NotFoundError  # noqa: F401
from sanic.exceptions import RequestTimeout as TimeoutError  # noqa: F401
from sanic.exceptions import SanicException
from sanic.exceptions import ServerError  # noqa: F401
from sanic.exceptions import ServiceUnavailable  # noqa: F401
from sanic.exceptions import Unauthorized as UnauthorizedError  # noqa: F401


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
