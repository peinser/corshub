from __future__ import annotations

from corshub.services.base import Service

from . import put
from . import read
from . import sourcetable


__all__ = ["put", "read", "sourcetable"]

from .base import bp


service = Service(
    name="ntrip",
    v1=bp,
    latest=bp.copy("ntrip-latest", url_prefix="/"),  # NTRIP v2 specification mandates no prefix.
)