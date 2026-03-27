r"""
Cryptographic hashing utilities for secure data processing, authentication, and integrity verification.

Functions:
---------

- sha256(payload: bytes, serialize: bool = False) -> bytes | tuple[bytes, str]
    Computes the SHA-256 hash of the payload. If `serialize` is True, returns both the raw digest and hex string.

- sha512(payload: bytes, serialize: bool = False) -> bytes | tuple[bytes, str]
    Computes the SHA-512 hash of the payload. If `serialize` is True, returns both the raw digest and hex string.

- hex_sha256(payload: bytes) -> str
    Computes the SHA-256 hash and returns it as a hex string.

- hex_sha512(payload: bytes) -> str
    Computes the SHA-512 hash and returns it as a hex string.

- sha3_256(payload: bytes) -> bytes
    Computes the SHA3-256 hash of the payload.

- sha3_512(payload: bytes) -> bytes
    Computes the SHA3-512 hash of the payload.

- hash(payload: bytes) -> bytes
    Computes a generic hash by salting the payload with its SHA-512 hash and hashing with SHA3-256.

Usage:
-----

    digest = sha256(b"data")
    hex_digest = hex_sha256(b"data")
    salted = hash(b"data")

This module is intended for use in password hashing, digital signatures, and other security-sensitive contexts.
"""

from __future__ import annotations

import hashlib


def _hash(hasher: hashlib._Hash, payload: bytes) -> bytes:
    """
    Compute the hash digest for the given payload using the provided hasher.

    Args:
        hasher (hashlib._Hash): Hashlib hasher instance.
        payload (bytes): Data to hash.

    Returns:
        bytes: The hash digest.
    """
    hasher.update(payload)
    return hasher.digest()


def _hash_hex(hasher: hashlib._Hash, payload: bytes) -> str:
    """
    Compute the hash digest for the given payload and return as a hex string.

    Args:
        hasher (hashlib._Hash): Hashlib hasher instance.
        payload (bytes): Data to hash.

    Returns:
        str: The hex-encoded hash digest.
    """
    hasher.update(payload)
    return hasher.hexdigest()


def sha256(payload: bytes, serialize: bool = False) -> bytes | tuple[bytes, str]:
    """
    Compute the SHA-256 hash of the payload.

    Args:
        payload (bytes): Data to hash.
        serialize (bool): If True, also return the hex string.

    Returns:
        bytes: The hash digest.
        tuple[bytes, str]: The digest and hex string if serialize is True.
    """
    hasher = hashlib.sha256()
    hasher.update(payload)
    if serialize:
        return hasher.digest(), hasher.hexdigest()
    return hasher.digest()


def sha512(payload: bytes, serialize: bool = False) -> bytes | tuple[bytes, str]:
    """
    Compute the SHA-512 hash of the payload.

    Args:
        payload (bytes): Data to hash.
        serialize (bool): If True, also return the hex string.

    Returns:
        bytes: The hash digest.
        tuple[bytes, str]: The digest and hex string if serialize is True.
    """
    hasher = hashlib.sha512()
    hasher.update(payload)
    if serialize:
        return hasher.digest(), hasher.hexdigest()
    return hasher.digest()


def hex_sha256(payload: bytes) -> str:
    """
    Compute the SHA-256 hash and return as a hex string.

    Args:
        payload (bytes): Data to hash.

    Returns:
        str: The hex-encoded hash digest.
    """
    return _hash_hex(hasher=hashlib.sha256(), payload=payload)


def hex_sha512(payload: bytes) -> str:
    """
    Compute the SHA-512 hash and return as a hex string.

    Args:
        payload (bytes): Data to hash.

    Returns:
        str: The hex-encoded hash digest.
    """
    return _hash_hex(hasher=hashlib.sha512(), payload=payload)


def sha3_256(payload: bytes) -> bytes:
    """
    Compute the SHA3-256 hash of the payload.

    Args:
        payload (bytes): Data to hash.

    Returns:
        bytes: The hash digest.
    """
    return _hash(hashlib.sha3_256(), payload=payload)


def sha3_512(payload: bytes) -> bytes:
    """
    Compute the SHA3-512 hash of the payload.

    Args:
        payload (bytes): Data to hash.

    Returns:
        bytes: The hash digest.
    """
    return _hash(hashlib.sha3_512(), payload=payload)


def hash(payload: bytes) -> bytes:
    """
    Compute a generic hash by salting the payload with its SHA-512 hash and hashing with SHA3-256.

    Args:
        payload (bytes): Data to hash.

    Returns:
        bytes: The salted hash digest.
    """
    salt = sha512(payload=payload)
    return sha3_256(salt + payload)
