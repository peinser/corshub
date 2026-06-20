r"""
Cryptographic signing utilities for generating and verifying HMAC signatures.

Functions:
---------

- hmac_sha256(payload: bytes, secret: bytes) -> str
    Generates a HMAC SHA256 signature for the given payload and secret.

- verify_hmac_sha256(payload: bytes, signature: str, secret: bytes) -> bool
    Verifies a HMAC SHA256 signature against the payload and secret.

Security:
---------
- Always use strong, random secrets for HMAC.
- Use constant-time comparison for signature verification to prevent timing attacks.

Usage:
-----

    sig = hmac_sha256(b"data", b"key")
    valid = verify_hmac_sha256(b"data", sig, b"key")
"""

from __future__ import annotations

import hashlib
import hmac

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


def hmac_sha256(payload: bytes, secret: bytes) -> str:
    """
    Generate a HMAC SHA256 signature for the given payload and secret.

    Args:
        payload (bytes): Data to sign.
        secret (bytes): Secret key for HMAC.

    Returns:
        str: Hex-encoded HMAC SHA256 signature.

    Security:
        Use strong, random secrets for HMAC keys.
    """
    return hmac.new(key=secret, msg=payload, digestmod=hashlib.sha256).hexdigest()


def verify_hmac_sha256(payload: bytes, signature: str, secret: bytes) -> bool:
    """
    Verify a HMAC SHA256 signature against the payload and secret.

    Args:
        payload (bytes): Data to verify.
        signature (str): Hex-encoded HMAC SHA256 signature to check.
        secret (bytes): Secret key for HMAC.

    Returns:
        bool: True if the signature is valid, False otherwise.

    Security:
        Uses constant-time comparison to prevent timing attacks.
    """
    expected = hmac.new(key=secret, msg=payload, digestmod=hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def ed25519_sign(payload: bytes, private_key: Ed25519PrivateKey) -> bytes:
    """Sign *payload* with an Ed25519 *private_key*, returning the 64-byte signature.

    Used to authenticate outgoing correction frames so any holder of the matching
    public key can verify provenance without a shared secret.
    """
    return private_key.sign(payload)


def ed25519_verify(payload: bytes, signature: bytes, public_key: Ed25519PublicKey) -> bool:
    """Verify an Ed25519 *signature* over *payload* against *public_key*.

    Returns ``True`` on a valid signature, ``False`` on any verification failure
    (bad signature, wrong key, malformed input). Never raises.
    """
    try:
        public_key.verify(signature, payload)
        return True
    except InvalidSignature, ValueError, TypeError:
        return False
