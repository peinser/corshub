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

Timing accuracy
---------------
``ntrip_frame_interval_seconds`` relies on ``time.monotonic()``, which
measures wall-clock elapsed time on the host.  For accurate inter-frame
delay measurements the host should synchronise its system clock with an
NTP server (e.g. ``chronyd`` or ``systemd-timesyncd``).  Without NTP,
clock drift will silently bias all interval observations.
"""

from __future__ import annotations

import time

from typing import TYPE_CHECKING

from prometheus_client import Counter
from prometheus_client import Gauge
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

frame_interval_seconds: Histogram = Histogram(
    "ntrip_frame_interval_seconds",
    "Elapsed time between consecutive frames from the same base station, in seconds.",
    ["mountpoint"],
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0],
)

rtcm_messages_total: Counter = Counter(
    "ntrip_rtcm_messages_total",
    "RTCM messages observed from base stations, by message type.",
    ["mountpoint", "message_type"],
)

rtcm_parse_errors_total: Counter = Counter(
    "ntrip_rtcm_parse_errors_total",
    "RTCM frames that could not be fully parsed (CRC failure, truncation, or unknown format).",
    ["mountpoint"],
)

rtcm_signal_cnr_dbhz: Histogram = Histogram(
    "ntrip_rtcm_signal_cnr_dbhz",
    "GNSS signal carrier-to-noise density (CNR) per satellite-signal cell from MSM4-7 messages, in dBHz. "
    "Anomalous uniformity or out-of-range values may indicate spoofing or hardware issues.",
    ["mountpoint", "constellation"],
    buckets=[10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70],
)

base_station_arp_ecef_meters: Gauge = Gauge(
    "ntrip_base_station_arp_ecef_meters",
    "Base station Antenna Reference Point (ARP) ECEF coordinate in metres from RTCM 1005/1006. "
    "Should remain constant for a fixed installation. Any change may indicate spoofing or physical tampering.",
    ["mountpoint", "axis"],
)

base_station_arp_changes_total: Counter = Counter(
    "ntrip_base_station_arp_changes_total",
    "Number of times the ARP position in RTCM 1005/1006 deviated from the first observed value by more "
    "than 1 cm. Any non-zero value on a fixed installation is a potential spoofing or tampering indicator.",
    ["mountpoint"],
)

rtcm_high_cnr_total: Counter = Counter(
    "ntrip_rtcm_high_cnr_total",
    "Satellite-signal CNR observations above 55 dBHz from MSM4-7 messages. Spoofed signals are often "
    "abnormally strong; a sustained high fraction relative to total CNR observations may indicate spoofing.",
    ["mountpoint", "constellation"],
)

rtcm_satellites_tracked: Histogram = Histogram(
    "ntrip_rtcm_satellites_tracked",
    "Number of GNSS satellites present in each MSM frame, by constellation.",
    ["mountpoint", "constellation"],
    buckets=[
        1,
        2,
        3,
        4,
        5,
        6,
        7,
        8,
        9,
        10,
        11,
        12,
        13,
        14,
        15,
        16,
        17,
        18,
        19,
        20,
        21,
        22,
        23,
        24,
        25,
        26,
        27,
        28,
        29,
        30,
        31,
        32,
        33,
        34,
        35,
        36,
        37,
        38,
        39,
        40,
    ],
)


class NTRIPCasterCollector(Collector):
    """Yields gauge metrics by inspecting a live NTRIPCaster instance.

    Registered once per caster lifetime so that Prometheus always gets a
    fresh snapshot.  Only emits per-mountpoint queue/subscriber metrics for
    QueueTransport instances; other transport backends are skipped silently.

    Note, since we run `generate_latest` through Sanic, the Prometheus Client
    runs in the Sanic event loop. Hence, we don't need locks in the event loop
    as long as we are running a single process. Given QueueTransport is the
    only transport that is currently supported, this needs to be done anyway.
    """

    def __init__(self, caster: NTRIPCaster) -> None:
        self._caster = caster

    def collect(self) -> Iterator[Metric]:
        caster = self._caster

        mountpoints = caster.mountpoints
        transports = caster.transports

        yield GaugeMetricFamily(
            "ntrip_mountpoints_registered",
            "Total mountpoints in the registry (online and offline).",
            value=float(len(mountpoints)),
        )

        yield GaugeMetricFamily(
            "ntrip_mountpoints_online",
            "Mountpoints with an active base station transport.",
            value=float(len(transports)),
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

        for name, transport in transports.items():
            if isinstance(transport, QueueTransport):
                rovers_gauge.add_metric([name], float(transport.subscriber_count))
                queue_gauge.add_metric([name], float(transport.queue_depth))

        yield rovers_gauge
        yield queue_gauge

        last_seen_gauge = GaugeMetricFamily(
            "ntrip_mountpoint_last_seen_seconds",
            "Seconds elapsed since the last frame was received from this mountpoint.",
            labels=["mountpoint"],
        )

        now = time.monotonic()
        for name, mp in mountpoints.items():
            last_seen_gauge.add_metric([name], now - mp.last_seen)

        yield last_seen_gauge

        info_gauge = GaugeMetricFamily(
            "ntrip_mountpoint_info",
            "Mountpoint metadata. Value is always 1; use labels for location and identification. "
            "mask is the configured maximum rover distance in km (0 = unlimited).",
            labels=["mountpoint", "latitude", "longitude", "identifier", "country", "mask"],
        )

        for name, mp in mountpoints.items():
            if mp.latitude is not None and mp.longitude is not None:
                info_gauge.add_metric(
                    [name, str(mp.latitude), str(mp.longitude), mp.identifier or "", mp.country or "", str(mp.mask)],
                    1.0,
                )

        yield info_gauge

    def describe(self) -> list[Metric]:
        return []
