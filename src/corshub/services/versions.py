from __future__ import annotations

import importlib

from .base import Service


def load(service: str) -> Service:
    r"""
    Attemts to load the dependencies and versions of the specified service.
    """
    module = importlib.import_module(f"corshub.services.v1.{service}")
    return module.service
