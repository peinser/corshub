r"""
This module provides utilities for securely generating random secrets and hashing them using bcrypt.

Functions:
---------

- generate(num_bytes: int = DEFAULT_SECRET_BYTES) -> str
    Generates a cryptographically secure random secret string using URL-safe base64 encoding.
    The length of the secret is determined by `num_bytes` (default: 20).

- generate_and_hash(num_bytes: int = 16) -> tuple[str, bytes]
    Generates a random secret and returns a tuple containing the secret and its bcrypt hash.
    Raises ValueError if `num_bytes` is less than or equal to zero.

Constants:
---------

- DEFAULT_SECRET_BYTES: int
    Default number of bytes for secret generation (20).

Usage:
-----

    secret = generate()
    secret, hashed = generate_and_hash()

This module is intended for use in authentication, API key generation, and other security-sensitive contexts.
"""

from __future__ import annotations

import secrets

from typing import TYPE_CHECKING

import bcrypt


if TYPE_CHECKING:
    from typing import Final


DEFAULT_SECRET_BYTES: Final[int] = 20


def generate(num_bytes: int = DEFAULT_SECRET_BYTES) -> str:
    r"""
    Returns `num_bytes` of URL-safe secret characters. This method ensures the output length is
    constrained to `num_bytes`, in contrast to `b64generate`.

    Args:
        num_bytes (int): Number of bytes (characters) for the secret (default: DEFAULT_SECRET_BYTES).

    Returns:
        str: The url-safe token sequence of length `num_bytes`.
    """
    return b64generate(num_bytes=num_bytes)[:num_bytes]


def b64generate(num_bytes: int = DEFAULT_SECRET_BYTES) -> str:
    """
    Generate a cryptographically secure random secret byte sequence using URL-safe base64 encoding.
    It is important to note this method will generally output more then `num_bytes` of characters
    due to the base64 encoding.

    Args:
        num_bytes (int): Number of bytes for the secret (default: DEFAULT_SECRET_BYTES).

    Returns:
        str: The base64-encoded generated secret byte sequence.
    """
    if num_bytes <= 0:
        raise ValueError("At least a single byte is required.")

    return secrets.token_urlsafe(nbytes=num_bytes)


def generate_and_hash(num_bytes: int = 16) -> tuple[str, bytes]:
    """
    Generate a random secret and its bcrypt hash.

    Args:
        num_bytes (int): Number of bytes for the secret (default: 16).

    Returns:
        tuple[str, bytes]: A tuple containing the secret and its bcrypt hash.

    Raises:
        ValueError: If num_bytes is less than or equal to zero.
    """
    if num_bytes <= 0:
        raise ValueError("At least a single byte is required.")

    secret = b64generate(num_bytes=num_bytes)
    hashed = bcrypt.hashpw(secret.encode(), bcrypt.gensalt())

    return secret, hashed
