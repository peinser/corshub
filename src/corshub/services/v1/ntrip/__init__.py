from __future__ import annotations

from corshub.services.base import Service

from . import put
from . import read


__all__ = ["put", "read"]

from .base import bp


service = Service(
    name="ntrip",
    v1=bp,
    latest=bp.copy("ntrip-latest", url_prefix="/"),  # NTRIP v2 specification mandates no prefix.
)