from __future__ import annotations

from corshub.services.base import Service


__all__ = []

from .base import bp


service = Service(
    name="metrics",
    latest=bp,
)
