r"""
Entrypoint that spins up all independent services.
The binary is mainly intended for development purposes or
low-traffic environments.
"""

from __future__ import annotations

import argparse
import importlib
import pkgutil

from typing import TYPE_CHECKING

from prometheus_client import start_http_server
from sanic import response

import corshub.services.v1

from corshub.services.base import Service

from .utils import create_app


if TYPE_CHECKING:
    from sanic import HTTPResponse
    from sanic import Request


parser = argparse.ArgumentParser("Service Bootstrap Manager")

parser.add_argument("--host", type=str, default=None, help="IP address to run the host on (default: none).")
parser.add_argument("--port", type=int, default=8000, help="The port to run the Sanic service on (default: 8000).")
parser.add_argument("--debug", action="store_true", default=False, help="Run in debug mode (default: false).")
parser.add_argument("--reload", action="store_true", default=False, help="Enable hot-reloading (default: false).")
parser.add_argument("--access-logs", action="store_true", help="Enable access logs (default: false).")
parser.add_argument(
    "--metrics-port",
    type=int,
    default=None,
    help="The port to run the (Prometheus) metrics service on (default: none, which disables the service).",
)
parser.add_argument(
    "--reverse-proxy-count",
    type=int,
    default=0,
    help="Defines whether a reverse proxy is being used, the value represents the number of entries expected in x-forwarded-for (default: false).",
)
parser.add_argument(
    "--header-real-ip",
    type=str,
    default="x-real-ip",
    help="Defines the header in which the true IP of the client is defined. Only has effect whenever `--reverse-proxy` is defined, i.e., > 0 (default: x-real-ip).",
)

arguments, _ = parser.parse_known_args()

app = create_app("Standalone", arguments=arguments)

path = corshub.services.v1.__path__
for _, module_name, is_pkg in pkgutil.iter_modules(path):
    if not is_pkg:
        continue
    service_path = f"corshub.services.v1.{module_name}"
    module = importlib.import_module(service_path)
    service: Service = module.service
    # Add all possible versions to the application.
    for version in service.versions:
        app.blueprint(service.blueprint(version=version))


@app.route("/.info/healthz", methods=["GET"])
async def healthz(_: Request) -> HTTPResponse:
    return response.empty()


if __name__ == "__main__":
    # Check if the Prometheus web service needs to be ran.
    if arguments.metrics_port:
        # Start the prometheus exposition server on a dedicated port so that it is
        # never reachable through the main Ingress / HTTPRoute (which only targets
        # the application port). METRICS_PORT absent or empty disables the server
        # (useful in tests and local dev without a Prometheus stack).
        start_http_server(port=arguments.metrics_port, addr="0.0.0.0")

    app.run(
        host=arguments.host,
        port=arguments.port,
        debug=arguments.debug,
        auto_reload=arguments.reload,
        access_log=arguments.access_logs,
        workers=1,
    )
