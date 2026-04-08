r"""
A command line utility for managing and booting services.
"""

from __future__ import annotations

import argparse

from typing import TYPE_CHECKING

from sanic import response

from corshub.services.versions import load

from .utils import create_app


if TYPE_CHECKING:
    from sanic import HTTPResponse
    from sanic import Request


parser = argparse.ArgumentParser("Service Bootstrap Manager")

parser.add_argument("--run", type=str, default=[], action="append", help="Lists the service to execute.")
parser.add_argument("--host", type=str, default=None, help="IP address to run the host on (default: none).")
parser.add_argument("--port", type=int, default=8000, help="The port to run the Sanic service on (default: 8000).")
parser.add_argument("--debug", action="store_true", default=False, help="Run in debug mode (default: false).")
parser.add_argument("--fast", action="store_true", default=False, help="Fast-mode, disables logging (default: false).")
parser.add_argument("--reload", action="store_true", default=False, help="Enable hot-reloading (default: false).")
parser.add_argument("--workers", type=int, default=1, help="Workers to process incoming requests (default: 1).")
parser.add_argument("--access-logs", action="store_true", help="Enable access logs (default: false).")
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


if not len(arguments.run):
    raise ValueError("No services have been specified! Add arguments to the `--run` flag.")


app = create_app("Service", arguments=arguments)

_services = []
for proposal in arguments.run:
    service, version = proposal.split("=")
    group = load(service)

    app.blueprint(group.blueprint(version=version))
    _services.append(
        {
            "service": service,
            "version": version,
        }
    )


if not _services:
    raise ValueError("No valid service has been added to the runtime! Check the `--run` argument.")


@app.route("/.info/healthz", methods=["GET"])
async def healthz(_: Request) -> HTTPResponse:
    return response.empty()


if __name__ == "__main__":
    app.run(
        workers=arguments.workers,
        fast=arguments.fast,
        host=arguments.host,
        port=arguments.port,
        debug=arguments.debug,
        auto_reload=arguments.reload,
        access_log=arguments.access_logs,
        single_process=True,
    )
