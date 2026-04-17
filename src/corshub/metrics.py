"""
Prometheus metrics for the NTRIP caster.

Module-level counters and histograms are registered on import.
NTRIPCasterCollector samples live caster state at each scrape so that
gauges (connected rovers, queue depths, online mountpoints) always reflect
the current snapshot rather than a stale last-written value.

Register the collector in the Sanic before_server_start listener and
unregister it in after_server_stop to avoid double-registration across
test runs or hot reloads::

    from prometheus_client import REGISTRY
    from corshub.metrics import NTRIPCasterCollector

    collector = NTRIPCasterCollector(caster)
    REGISTRY.register(collector)
    ...
    REGISTRY.unregister(collector)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from prometheus_client import Counter
from prometheus_client import Histogram
from prometheus_client.metrics_core import GaugeMetricFamily
from prometheus_client.registry import Collector

from corshub.ntrip.v2.transport import QueueTransport


if TYPE_CHECKING:
    from collections.abc import Iterator

    from prometheus_client.metrics_core import Metric

    from corshub.ntrip.v2.caster import NTRIPCaster


frames_published_total: Counter = Counter(
    "ntrip_frames_published_total",
    "RTCM frames received from base stations.",
    ["mountpoint"],
)

bytes_published_total: Counter = Counter(
    "ntrip_bytes_published_total",
    "Bytes received from base stations.",
    ["mountpoint"],
)

frames_delivered_total: Counter = Counter(
    "ntrip_frames_delivered_total",
    "Successful per-rover frame deliveries (summed across all subscribers).",
    ["mountpoint"],
)

rover_sessions_total: Counter = Counter(
    "ntrip_rover_sessions_total",
    "Total rover subscription sessions opened.",
    ["mountpoint"],
)

mountpoints_reaped_total: Counter = Counter(
    "ntrip_mountpoints_reaped_total",
    "Stale mountpoints removed by the reaper task.",
)

auth_requests_total: Counter = Counter(
    "ntrip_auth_requests_total",
    "Authentication attempts by role (base_station|rover) and result (success|failure).",
    ["role", "result"],
)

frame_size_bytes: Histogram = Histogram(
    "ntrip_frame_size_bytes",
    "Size of each RTCM frame received from a base station.",
    ["mountpoint"],
    buckets=[64, 128, 256, 512, 1024, 2048, 4096, 8192],
)


class NTRIPCasterCollector(Collector):
    """Yields gauge metrics by inspecting a live NTRIPCaster instance.

    Registered once per caster lifetime so that Prometheus always gets a
    fresh snapshot.  Only emits per-mountpoint queue/subscriber metrics for
    QueueTransport instances; other transport backends are skipped silently.
    """

    def __init__(self, caster: NTRIPCaster) -> None:
        self._caster = caster

    def collect(self) -> Iterator[Metric]:
        caster = self._caster

        yield GaugeMetricFamily(
            "ntrip_mountpoints_registered",
            "Total mountpoints in the registry (online and offline).",
            value=float(len(caster.mountpoints)),
        )

        yield GaugeMetricFamily(
            "ntrip_mountpoints_online",
            "Mountpoints with an active base station transport.",
            value=float(len(caster._transports)),  # noqa: SLF001
        )

        rovers_gauge = GaugeMetricFamily(
            "ntrip_rovers_connected",
            "Active rover connections per mountpoint (QueueTransport only).",
            labels=["mountpoint"],
        )
        queue_gauge = GaugeMetricFamily(
            "ntrip_queue_depth",
            "Total pending frames across all rover queues per mountpoint (QueueTransport only).",
            labels=["mountpoint"],
        )

        for name, transport in caster._transports.items():  # noqa: SLF001
            if isinstance(transport, QueueTransport):
                rovers_gauge.add_metric([name], float(transport.subscriber_count))
                queue_gauge.add_metric([name], float(transport.queue_depth))

        yield rovers_gauge
        yield queue_gauge

    def describe(self) -> list[Metric]:
        return []
