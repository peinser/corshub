r"""
Generic utilities for bootstrapping applications.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sanic import Sanic

from corshub import http
from corshub import json


if TYPE_CHECKING:
    from argparse import Namespace


def create_app(name: str, arguments: Namespace | None = None) -> Sanic:
    r"""
    Factory method to create a Sanic application with our custom defaults.
    """
    app = Sanic(
        name,
        dumps=json.dumps,
        loads=json.loads,
    )

    app.config.FALLBACK_ERROR_FORMAT = "json"
    # Check if special arguments have been specified.
    if arguments and arguments.reverse_proxy_count > 0:
        app.config.PROXIES_COUNT = arguments.reverse_proxy_count
        app.config.REAL_IP_HEADER = arguments.header_real_ip

    app.config.ACCESS_LOG = arguments.access_logs if arguments else False
    app.config.AUTO_RELOAD = arguments.reload if arguments else False

    # Initialize the HTTP context.
    http.initialize_http_sessions(app)

    return app
