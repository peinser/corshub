"""Pre-flight signal quality endpoint — GET /api/v1/ntrip/quality/<mountpoint>

Returns a JSON summary of the current signal quality for a mountpoint, drawn
from the rolling window maintained by the caster's RTCM parser.  Intended for
GCS software and autopilot scripts that need a machine-readable quality check
before arming.

Single-mountpoint response shape::

    {
      "mountpoint": "BASE1",
      "online": true,
      "last_seen_seconds": 1.24,
      "arp": {"x": 3975478.0, "y": 302283.0, "z": 4986670.0, "changes": 0},
      "constellations": {
        "GPS":     {"cnr_p50_dbhz": 45.2, "cnr_p95_dbhz": 52.1, "median_satellites": 8.0},
        "GLONASS": {"cnr_p50_dbhz": 42.0, "cnr_p95_dbhz": 49.3, "median_satellites": 5.0}
      },
      "parse_errors": 0,
      "rover_positions": [{"rover_id": "rover1", "lat": 51.05, "lon": 3.72}]
    }

``arp`` is omitted when no RTCM 1005/1006 message has been received yet.
``rover_positions`` is omitted when no rover with a known GGA position is connected.
``constellations`` is empty when no MSM4-7 message has been received yet.
``last_seen_seconds`` reflects time since the last *any* frame, not just MSM.
``parse_errors`` and ``arp.changes`` are cumulative since the process started.
"""

from __future__ import annotations

import time

from typing import TYPE_CHECKING

from sanic.exceptions import NotFound
from sanic.response import json as json_response

import corshub.metrics as metrics

from .base import bp


if TYPE_CHECKING:
    from sanic import HTTPResponse
    from sanic import Request


def _mountpoint_quality_dict(request: Request, mountpoint: str) -> dict:
    caster = request.app.ctx.ntrip_caster

    mp = caster.mountpoints.get(mountpoint)
    if mp is None:
        raise NotFound(f"Mountpoint {mountpoint!r} is not registered.")

    online = mountpoint in caster.transports
    last_seen_seconds = round(time.monotonic() - mp.last_seen, 3)

    result: dict = {
        "mountpoint": mountpoint,
        "online": online,
        "last_seen_seconds": last_seen_seconds,
    }

    arp = caster._arp_reference.get(mountpoint)
    if arp is not None:
        x, y, z = arp
        arp_changes_counter = metrics.base_station_arp_changes_total.labels(mountpoint=mountpoint)
        result["arp"] = {
            "x": x,
            "y": y,
            "z": z,
            "changes": int(arp_changes_counter._value.get()),
        }

    quality = caster._quality.get(mountpoint)
    result["constellations"] = quality.to_dict() if quality is not None else {}

    parse_errors_counter = metrics.rtcm_parse_errors_total.labels(mountpoint=mountpoint)
    result["parse_errors"] = int(parse_errors_counter._value.get())

    rover_positions = caster.get_rover_positions(mountpoint)
    if rover_positions:
        result["rover_positions"] = [
            {"rover_id": rover_id, "lat": lat, "lon": lon} for rover_id, (lat, lon) in rover_positions.items()
        ]

    return result


@bp.get("/quality/<mountpoint:str>")
async def quality_single(request: Request, mountpoint: str) -> HTTPResponse:
    """Signal quality summary for a single mountpoint."""
    return json_response(_mountpoint_quality_dict(request, mountpoint))
