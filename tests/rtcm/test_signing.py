"""Tests for Ed25519 signing primitives and signing-key management."""

from __future__ import annotations

import base64

import pytest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from corshub.crypto import sign
from corshub.rtcm.keys import SigningKey
from corshub.rtcm.keys import resolve_signing_key


class TestEd25519Primitives:
    def test_sign_then_verify_succeeds(self) -> None:
        priv = Ed25519PrivateKey.generate()
        payload = b"correction-frame-bytes"
        signature = sign.ed25519_sign(payload, priv)
        assert sign.ed25519_verify(payload, signature, priv.public_key()) is True

    def test_tampered_payload_fails(self) -> None:
        priv = Ed25519PrivateKey.generate()
        signature = sign.ed25519_sign(b"original", priv)
        assert sign.ed25519_verify(b"tampered", signature, priv.public_key()) is False

    def test_wrong_key_fails(self) -> None:
        priv = Ed25519PrivateKey.generate()
        other = Ed25519PrivateKey.generate()
        signature = sign.ed25519_sign(b"data", priv)
        assert sign.ed25519_verify(b"data", signature, other.public_key()) is False

    def test_malformed_signature_returns_false_not_raise(self) -> None:
        priv = Ed25519PrivateKey.generate()
        assert sign.ed25519_verify(b"data", b"too-short", priv.public_key()) is False


class TestSigningKey:
    def test_jwk_shape(self) -> None:
        key = SigningKey.generate()
        jwk = key.jwk()
        assert jwk["kty"] == "OKP"
        assert jwk["crv"] == "Ed25519"
        assert jwk["alg"] == "EdDSA"
        assert jwk["use"] == "sig"
        assert jwk["kid"] == key.kid
        # x is base64url (no padding) of the 32-byte raw public key.
        assert "=" not in jwk["x"]
        assert len(base64.urlsafe_b64decode(jwk["x"] + "==")) == 32

    def test_jwks_wraps_single_key(self) -> None:
        key = SigningKey.generate()
        jwks = key.jwks()
        assert jwks["keys"] == [key.jwk()]

    def test_kid_is_stable_and_derived_from_public_key(self) -> None:
        priv = Ed25519PrivateKey.generate()
        a = SigningKey(priv)
        b = SigningKey(priv)
        assert a.kid == b.kid  # deterministic thumbprint
        assert SigningKey.generate().kid != a.kid  # different key, different kid

    def test_sign_is_verifiable_with_published_jwk(self) -> None:
        key = SigningKey.generate()
        payload = b"frame"
        signature = key.sign(payload)
        assert sign.ed25519_verify(payload, signature, key.public_key) is True

    def test_from_seed_roundtrip(self) -> None:
        priv = Ed25519PrivateKey.generate()
        seed = priv.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        assert SigningKey.from_seed(seed).kid == SigningKey(priv).kid

    def test_from_private_pem_roundtrip(self) -> None:
        priv = Ed25519PrivateKey.generate()
        pem = priv.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        assert SigningKey.from_private_pem(pem).kid == SigningKey(priv).kid

    def test_from_private_pem_rejects_non_ed25519(self) -> None:
        from cryptography.hazmat.primitives.asymmetric import rsa

        rsa_pem = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        with pytest.raises(ValueError, match="Ed25519"):
            SigningKey.from_private_pem(rsa_pem)


class TestResolveSigningKey:
    def _seed_b64(self, priv: Ed25519PrivateKey) -> str:
        seed = priv.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        return base64.b64encode(seed).decode()

    def test_inline_pem_takes_precedence(self, tmp_path) -> None:
        inline = Ed25519PrivateKey.generate()
        inline_pem = inline.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode()

        # A different key on disk must be ignored when an inline key is present.
        path = tmp_path / "other.pem"
        path.write_bytes(
            Ed25519PrivateKey.generate().private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )

        key = resolve_signing_key(key_path=str(path), key_inline=inline_pem, allow_ephemeral=False)
        assert key.kid == SigningKey(inline).kid

    def test_inline_base64_seed(self) -> None:
        priv = Ed25519PrivateKey.generate()
        key = resolve_signing_key(key_path=None, key_inline=self._seed_b64(priv), allow_ephemeral=False)
        assert key.kid == SigningKey(priv).kid

    def test_file_path(self, tmp_path) -> None:
        priv = Ed25519PrivateKey.generate()
        path = tmp_path / "key.pem"
        path.write_bytes(
            priv.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
        key = resolve_signing_key(key_path=str(path), key_inline=None, allow_ephemeral=False)
        assert key.kid == SigningKey(priv).kid

    def test_ephemeral_when_allowed(self) -> None:
        key = resolve_signing_key(key_path=None, key_inline=None, allow_ephemeral=True)
        assert key.kid  # generated

    def test_raises_when_no_key_and_ephemeral_disallowed(self) -> None:
        with pytest.raises(ValueError, match="No RTCM signing key"):
            resolve_signing_key(key_path=None, key_inline=None, allow_ephemeral=False)
