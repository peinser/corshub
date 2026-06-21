"""Tests for JWK key loading (RSA + OKP/Ed25519) in corshub.jwt."""

from __future__ import annotations

import json

import jwt
import pytest

from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from corshub.jwt import _public_key_from_jwk
from corshub.rtcm.keys import SigningKey


class TestPublicKeyFromJwk:
    def test_okp_ed25519_roundtrip(self) -> None:
        priv = Ed25519PrivateKey.generate()
        jwk = SigningKey(priv).jwk()  # public OKP JWK (kty=OKP, crv=Ed25519)

        public_key = _public_key_from_jwk(jwk)
        token = jwt.encode({"sub": "rover"}, priv, algorithm="EdDSA")
        assert jwt.decode(token, public_key, algorithms=["EdDSA"])["sub"] == "rover"

    def test_rsa_still_supported(self) -> None:
        priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(priv.public_key()))

        public_key = _public_key_from_jwk(jwk)
        token = jwt.encode({"sub": "svc"}, priv, algorithm="RS256")
        assert jwt.decode(token, public_key, algorithms=["RS256"])["sub"] == "svc"

    def test_unsupported_kty_raises(self) -> None:
        with pytest.raises(jwt.InvalidTokenError, match="Unsupported JWK key type"):
            _public_key_from_jwk({"kty": "oct", "k": "c2VjcmV0"})
