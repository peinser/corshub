"""
Blueprint definition for the metrics service, includes generation of the
Prometheus metrics endpoint.

Important!!! On Kubernetes all routes with `.info` prefixes are shielded from
the public ingresses / HTTPRoutes. If you do not use our Helm chart, be sure
to protect these endpoints behind your reverse proxy.
"""

from __future__ import annotations

from prometheus_client import REGISTRY
from sanic import Blueprint
from sanic import HTTPResponse
from sanic import Request
from sanic import response

from prometheus_client import REGISTRY
from prometheus_client import CONTENT_TYPE_LATEST
from prometheus_client import generate_latest

from corshub import env


bp: Blueprint = Blueprint(
    name="metrics-v1",
    url_prefix="/.info/v1/metrics",
)


env.verify(
    blueprint=bp,
    required={},
)


@bp.get("/")
async def metrics(_: Request) -> HTTPResponse:
    return response.raw(
        generate_latest(REGISTRY),
        content_type=CONTENT_TYPE_LATEST,
    )
