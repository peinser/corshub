"""Per-mountpoint signal quality tracking for the NTRIP caster.

``MountpointQuality`` keeps a bounded rolling window of CNR and satellite-count
observations per GNSS constellation.  It is updated by ``_observe_rtcm_quality``
on every parsed MSM frame and read by the ``/api/v1/ntrip/quality`` endpoint to
produce a pre-flight quality summary without querying Prometheus.
"""

from __future__ import annotations

import statistics

from collections import deque
from dataclasses import dataclass
from dataclasses import field


# Per-signal CNR values (and satellite counts) retained per constellation.  At a
# typical 1 Hz base-station rate with ~8 tracked satellites per MSM message, 200
# entries cover roughly 25 seconds of readings: enough to reflect current sky
# conditions without consuming significant memory.
WINDOW = 200


@dataclass
class ConstellationQuality:
    """Rolling CNR and satellite-count observations for one GNSS constellation."""

    cnr: deque[float] = field(default_factory=lambda: deque(maxlen=WINDOW))
    sat_counts: deque[int] = field(default_factory=lambda: deque(maxlen=WINDOW))

    def cnr_p50(self) -> float | None:
        data = list(self.cnr)
        if not data:
            return None
        return statistics.median(data)

    def cnr_p95(self) -> float | None:
        data = list(self.cnr)
        if len(data) < 2:
            return data[0] if data else None
        # quantiles(n=20) returns 19 cut points at 5 %, 10 %, …, 95 %.
        return statistics.quantiles(data, n=20)[-1]

    def median_satellites(self) -> float | None:
        data = list(self.sat_counts)
        if not data:
            return None
        return statistics.median(data)


@dataclass
class MountpointQuality:
    """Rolling quality snapshot for a single mountpoint."""

    constellations: dict[str, ConstellationQuality] = field(default_factory=dict)

    def record_cnr(self, constellation: str, values: list[float]) -> None:
        self._get_or_create(constellation).cnr.extend(values)

    def record_sat_count(self, constellation: str, count: int) -> None:
        self._get_or_create(constellation).sat_counts.append(count)

    def to_dict(self) -> dict:
        result: dict[str, dict] = {}
        for name, cq in self.constellations.items():
            entry: dict = {}
            if (p50 := cq.cnr_p50()) is not None:
                entry["cnr_p50_dbhz"] = round(p50, 2)
            if (p95 := cq.cnr_p95()) is not None:
                entry["cnr_p95_dbhz"] = round(p95, 2)
            if (msat := cq.median_satellites()) is not None:
                entry["median_satellites"] = round(msat, 1)
            result[name] = entry
        return result

    def _get_or_create(self, constellation: str) -> ConstellationQuality:
        if constellation not in self.constellations:
            self.constellations[constellation] = ConstellationQuality()
        return self.constellations[constellation]
