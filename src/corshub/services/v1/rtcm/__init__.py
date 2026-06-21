from __future__ import annotations

from corshub.services.base import Service

from . import jwks
from . import session


__all__ = ["jwks", "session"]

from .base import bp


service = Service(
    name="rtcm",
    v1=bp,
)
