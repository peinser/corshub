r"""
Generic utilities for bootstrapping applications.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sanic import Sanic

from charge import http
from charge import json


if TYPE_CHECKING:
    from argparse import Namespace


def create_app(name: str, arguments: Namespace | None = None) -> Sanic:
    r"""
    Factory method to create a Sanic application with our custom defaults.
    """
    app = Sanic(name, dumps=json.dumps, loads=json.loads)
    app.config.FALLBACK_ERROR_FORMAT = "json"
    # Check if special arguments have been specified.
    if arguments and arguments.reverse_proxy_count > 0:
            app.config.PROXIES_COUNT = arguments.reverse_proxy_count
            app.config.REAL_IP_HEADER = arguments.header_real_ip

    # Initialize the HTTP context.
    http.initialize_http_sessions(app)

    return app
