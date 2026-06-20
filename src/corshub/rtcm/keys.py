"""
Ed25519 signing-key management for the RTCM UDP egress.

Wraps a single Ed25519 keypair and exposes:
  * `sign` for the correction stream,
  * a JWK / JWKS view (kty=OKP, crv=Ed25519) for the public-key endpoint,
  * a stable `kid` derived as the RFC 7638 JWK thumbprint.

`resolve_signing_key` centralises provisioning: an inline key takes precedence
over a file path, the public key is derived from the private key, and a dev
environment may fall back to an ephemeral key (with a loud warning). Production
must supply a key explicitly.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from corshub.crypto import sign
from corshub.logging import logger


def _b64url(raw: bytes) -> str:
    """base64url without padding, per JOSE conventions."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _raw_public_bytes(public_key: Ed25519PublicKey) -> bytes:
    return public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def _jwk_thumbprint(public_key: Ed25519PublicKey) -> str:
    """RFC 7638 JWK thumbprint for an OKP/Ed25519 key.

    The canonical form contains the required members (crv, kty, x) in
    lexicographic order with no whitespace.
    """
    x = _b64url(_raw_public_bytes(public_key))
    canonical = f'{{"crv":"Ed25519","kty":"OKP","x":"{x}"}}'
    return _b64url(hashlib.sha256(canonical.encode("ascii")).digest())


class SigningKey:
    """An Ed25519 keypair plus its JOSE/JWK projections."""

    def __init__(self, private_key: Ed25519PrivateKey) -> None:
        self._private = private_key
        self._public = private_key.public_key()
        self._kid = _jwk_thumbprint(self._public)

    @property
    def kid(self) -> str:
        """RFC 7638 thumbprint identifying this key in the JWKS."""
        return self._kid

    @property
    def public_key(self) -> Ed25519PublicKey:
        return self._public

    def sign(self, payload: bytes) -> bytes:
        return sign.ed25519_sign(payload, self._private)

    def jwk(self) -> dict[str, str]:
        """Public key as a JWK (kty=OKP, crv=Ed25519)."""
        return {
            "kty": "OKP",
            "crv": "Ed25519",
            "x": _b64url(_raw_public_bytes(self._public)),
            "kid": self._kid,
            "use": "sig",
            "alg": "EdDSA",
        }

    def jwks(self) -> dict[str, list[dict[str, str]]]:
        """JWK Set containing this key."""
        return {"keys": [self.jwk()]}

    @classmethod
    def generate(cls) -> SigningKey:
        return cls(Ed25519PrivateKey.generate())

    @classmethod
    def from_private_pem(cls, pem: bytes) -> SigningKey:
        key = serialization.load_pem_private_key(pem, password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError(f"Expected an Ed25519 private key, got {type(key).__name__}.")
        return cls(key)

    @classmethod
    def from_seed(cls, seed: bytes) -> SigningKey:
        """Build from a raw 32-byte private seed."""
        return cls(Ed25519PrivateKey.from_private_bytes(seed))


def resolve_signing_key(
    *,
    key_path: str | None,
    key_inline: str | None,
    allow_ephemeral: bool,
) -> SigningKey:
    """Resolve the signing key from config.

    Precedence: inline value, then file path, then (dev only) an ephemeral key.
    Inline values may be a PEM block or a base64-encoded 32-byte seed.

    Raises ValueError if no key is configured and *allow_ephemeral* is False.
    """
    if key_inline:
        stripped = key_inline.strip()
        if stripped.startswith("-----BEGIN"):
            return SigningKey.from_private_pem(stripped.encode())
        return SigningKey.from_seed(base64.b64decode(stripped))

    if key_path:
        with open(key_path, "rb") as handle:
            return SigningKey.from_private_pem(handle.read())

    if not allow_ephemeral:
        raise ValueError(
            "No RTCM signing key configured. Set RTCM_SIGNING_KEY_PATH or "
            "RTCM_SIGNING_PRIVATE_KEY; ephemeral keys are only allowed in development."
        )

    key = SigningKey.generate()
    logger.warning(
        "RTCM signing: no key configured, generated an EPHEMERAL Ed25519 key (kid=%s). "
        "It is non-persistent and unpinnable across restarts. Do NOT use in production.",
        key.kid,
    )
    return key
