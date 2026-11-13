r"""
Application logging.

All CORSHub modules log through the ``corshub`` logger.  It shares Sanic's console
handler (so lines look like Sanic's own) but has its own level, ``LOG_LEVEL``
(default ``INFO``), so caster diagnostics can be turned up to ``DEBUG`` without
also enabling Sanic's internal debug output.

Conventions, so incidents can be grepped and correlated:
  * one line per lifecycle event (connect, disconnect, close, expiry), written
    as ``<Event>: key=value ...``; disconnect lines carry duration, volume and
    the reason the session ended;
  * nothing is logged per frame above ``DEBUG``;
  * passwords, tokens and UDP session ids are never logged (a session id
    authenticates its datagrams; only a short ``ref`` prefix is logged).
"""

from __future__ import annotations

import logging

from typing import Any

from sanic.log import LOGGING_CONFIG_DEFAULTS

from corshub import env


logger = logging.getLogger("corshub")


def log_config() -> dict[str, Any]:
    """Sanic's default logging config plus the ``corshub`` application logger.

    Only the dicts that change are copied: the defaults hold ``sys.stdout``,
    which cannot be deep-copied.
    """
    corshub = {"level": env.extract("LOG_LEVEL", default="INFO").upper(), "handlers": ["console"]}
    return {**LOGGING_CONFIG_DEFAULTS, "loggers": {**LOGGING_CONFIG_DEFAULTS["loggers"], "corshub": corshub}}


__all__ = ["log_config", "logger"]
