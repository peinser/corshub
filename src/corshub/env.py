r"""
Utilities related to managing environment variables.
"""

from __future__ import annotations

import os

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any

    from sanic import Blueprint
    from sanic import Sanic


def extract(
    key: str,
    optional: bool = True,
    default: str | None = None,
    verify: Callable | None = None,
    dtype: type = str,
) -> Any:
    r"""
    Utility method to extract environment variables with ease.
    This method adds the ability to extract a specific key and
    an optional default value. Whenever the specified key is not
    specified in the program environment, the method will raise
    an AssertionError.
    """
    if default is None and not optional:
        assert key in os.environ, f"{key} not in environment."

    value = os.getenv(key=key, default=default)

    if not optional:
        assert value, f"Value `{value}` for key `{key}` is undefined."

    # An optional verification function can be specified to check
    # the integrity of the provided value.
    if value and verify is not None:
        assert verify(value), f"Value `{value}` for key `{key}` failed verification."

    return dtype(value) if value else None


def verify(blueprint: Blueprint, required: set[str]) -> None:
    r"""
    A method which verifies the scope of the program environment.
    """

    @blueprint.listener("before_server_start")
    async def _verify(_: Sanic) -> None:
        missing = required - os.environ.keys()
        assert len(missing) == 0, missing
