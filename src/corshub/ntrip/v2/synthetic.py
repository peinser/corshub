"""
Synthetic mountpoint for debugging rover and GCS integrations without a base station.

When ``NTRIP_SYNTHETIC_MOUNTPOINT`` is enabled, the caster serves a mountpoint named
``SYNTHETIC`` that publishes one RTCM 1005 frame (station antenna position) per second
through the normal transport, so the NTRIP and RTCM UDP paths, auth, logging and
metrics all behave exactly as for a real station.

It is safe by construction: 1005 carries no satellite observations, so no receiver
can compute an RTK solution from it, and a vehicle can never be steered by fake
corrections.  The frame flags a non-physical reference station, the source table
labels it "not for navigation", and it is never verified, so NEAREST never routes to
it: a rover has to ask for it by name.
"""

from __future__ import annotations

import asyncio

from typing import TYPE_CHECKING

from corshub.logging import logger


if TYPE_CHECKING:
    from corshub.ntrip.v2.caster import NTRIPCaster


NAME = "SYNTHETIC"

# On the WGS-84 ellipsoid at latitude 0, longitude 0: obviously not a real site.
_ARP_ECEF_METERS = (6378137.0, 0.0, 0.0)
_STATION_ID = 4095  # largest 12-bit station id


def _crc24q(data: bytes) -> int:
    """CRC-24Q, the RTCM 3 frame checksum (polynomial 0x1864CFB)."""
    crc = 0
    for byte in data:
        crc ^= byte << 16
        for _ in range(8):
            crc <<= 1
            if crc & 0x1000000:
                crc ^= 0x1864CFB
    return crc & 0xFFFFFF


def rtcm_1005(x: float, y: float, z: float, station_id: int) -> bytes:
    """Encode a complete RTCM 3 frame holding a 1005 message (ECEF antenna position, metres)."""
    fields = (  # (bits, value), in RTCM 10403 order
        (12, 1005),  # DF002 message number
        (12, station_id),  # DF003 reference station id
        (6, 0),  # DF021 ITRF realization year (0: not specified)
        (1, 1),  # DF022 GPS
        (1, 0),  # DF023 GLONASS
        (1, 0),  # DF024 Galileo
        (1, 1),  # DF141 reference station indicator: non-physical
        (38, round(x * 10_000)),  # DF025 ECEF X, 0.1 mm
        (1, 0),  # DF142 single receiver oscillator
        (1, 0),  # DF001 reserved
        (38, round(y * 10_000)),  # DF026 ECEF Y
        (2, 0),  # DF364 quarter cycle indicator
        (38, round(z * 10_000)),  # DF027 ECEF Z
    )
    bits = 0
    width_total = 0
    for width, value in fields:
        bits = (bits << width) | (value & ((1 << width) - 1))  # two's complement for negatives
        width_total += width
    payload = bits.to_bytes(width_total // 8, "big")  # 152 bits = 19 bytes

    header = bytes((0xD3, len(payload) >> 8, len(payload) & 0xFF))
    return header + payload + _crc24q(header + payload).to_bytes(3, "big")


async def run(caster: NTRIPCaster, interval: float = 1.0) -> None:
    """Publish a 1005 frame on the SYNTHETIC mountpoint every *interval* seconds until cancelled."""
    await caster.register(
        mountpoint=NAME,
        identifier="Synthetic test data - not for navigation",
        format="RTCM 3.3",
        format_detail="1005(1)",
        latitude=0.0,
        longitude=0.0,
        generator="corshub",
        verified=False,
    )
    frame = rtcm_1005(*_ARP_ECEF_METERS, station_id=_STATION_ID)
    logger.warning("Synthetic mountpoint enabled: mountpoint=%s (RTCM 1005 only, not for navigation)", NAME)
    try:
        while True:
            await caster.publish(NAME, frame)
            await asyncio.sleep(interval)
    finally:
        await caster.close(NAME)
