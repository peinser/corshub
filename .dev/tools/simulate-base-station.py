r"""
A script simulating an RTK base station and pushing RTCM corrections to the local development server.

Generates synthetic RTCM frames at a configurable rate and streams them to a CORSHub NTRIP v2
caster over a long-lived HTTP PUT connection.  Each epoch burst contains:

  • 1005  — Reference Station ARP (position)
  • 1077  — GPS MSM7       (carrier phase + pseudorange + CNR)
  • 1087  — GLONASS MSM7
  • 1097  — Galileo MSM7
  • 1127  — BeiDou MSM7

Usage
-----
    python simulate-base-station.py
    python simulate-base-station.py --mountpoint HERE4 --username HERE4 --password secret
    python simulate-base-station.py --lat 51.5074 --lon -0.1278 --rate 0.5

The script reconnects automatically with exponential back-off if the server drops the connection.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import time
from typing import TYPE_CHECKING, AsyncIterator

import aiohttp

from pyrtcm import calc_crc24q, llh2ecef


if TYPE_CHECKING:
    pass


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def _build_rtcm1005(station_id: int, x_m: float, y_m: float, z_m: float) -> bytes:
    """Build a minimal RTCM 1005 (Reference Station ARP) frame.

    The frame is 25 bytes total:
        3 bytes preamble + length header
        19 bytes payload  (152 bits)
        3 bytes CRC-24Q

    Args:
        station_id: Reference station ID (0–4095).
        x_m, y_m, z_m: ECEF coordinates in metres.

    Returns:
        Raw RTCM 1005 frame bytes, ready to stream.
    """
    # ECEF coordinates are encoded in 0.0001 m units as signed 38-bit integers.
    xi = int(round(x_m * 10_000))
    yi = int(round(y_m * 10_000))
    zi = int(round(z_m * 10_000))

    # Mask to 38-bit signed two's-complement (wrap negatives).
    mask38 = (1 << 38) - 1
    xi &= mask38
    yi &= mask38
    zi &= mask38

    # Assemble 152 payload bits in a Python int, MSB first.
    bits = 0

    def push(value: int, width: int) -> None:
        nonlocal bits
        bits = (bits << width) | (value & ((1 << width) - 1))

    push(1005, 12)       # Message type
    push(station_id, 12) # Reference station ID
    push(0, 6)           # ITRF realization year (0 = not specified)
    push(1, 1)           # GPS indicator
    push(1, 1)           # GLONASS indicator
    push(1, 1)           # Galileo indicator
    push(0, 1)           # Reference station indicator (0 = physical)
    push(xi, 38)         # ECEF-X
    push(0, 1)           # Single receiver oscillator indicator
    push(0, 1)           # Reserved
    push(yi, 38)         # ECEF-Y
    push(0, 2)           # Quarter cycle indicator
    push(zi, 38)         # ECEF-Z

    # 152 bits → 19 bytes, no padding needed (152 % 8 == 0).
    payload = bits.to_bytes(19, "big")

    # Frame = preamble D3 + 2-byte (6 reserved + 10-bit length) + payload + CRC-24Q.
    length = len(payload)
    header = bytes([0xD3, (length >> 8) & 0x03, length & 0xFF])
    frame_no_crc = header + payload
    crc = calc_crc24q(frame_no_crc)
    return frame_no_crc + bytes([(crc >> 16) & 0xFF, (crc >> 8) & 0xFF, crc & 0xFF])


_GPS_EPOCH_UNIX  = 315_964_800  # 1980-01-06T00:00:00Z as Unix timestamp
_GPS_LEAP_SECONDS = 18          # GPS–UTC offset (current as of 2017)


def _gps_tow_ms() -> int:
    """Return the current GPS time-of-week in milliseconds (0..604 799 999)."""
    t = time.time() - _GPS_EPOCH_UNIX + _GPS_LEAP_SECONDS
    return int(t * 1000) % 604_800_000


# Synthetic satellite data
#
# Each tuple: (prn, rough_range_int_ms, rough_range_frac_1024ms, cnr_dbhz)
#   rough_range_int_ms      — integer milliseconds part of pseudorange (DF397)
#   rough_range_frac_1024ms — fractional ms in units of 2^-10 ms (DF399)
#   cnr_dbhz                — carrier-to-noise density in dB-Hz (DF420, 6 bits)

_MSM7_SATS: dict[int, list[tuple[int, int, int, int]]] = {
    1077: [  # GPS
        ( 2, 68, 512, 42),
        ( 5, 72, 256, 38),
        (12, 75, 768, 44),
        (15, 70, 128, 40),
        (18, 80, 384, 36),
        (25, 73, 640, 41),
    ],
    1087: [  # GLONASS
        ( 1, 69, 200, 39),
        ( 2, 74, 800, 37),
        ( 8, 71, 450, 43),
        ( 9, 78, 600, 38),
    ],
    1097: [  # Galileo
        ( 1, 70, 300, 41),
        ( 3, 73, 500, 39),
        ( 7, 76, 250, 42),
        (14, 69, 700, 40),
    ],
    1127: [  # BeiDou
        ( 1, 71, 400, 38),
        ( 5, 75, 150, 40),
        (12, 68, 600, 42),
        (22, 77, 350, 37),
    ],
}

# Bit width of the GNSS epoch-time field in the MSM header (varies by constellation).
_MSM7_EPOCH_BITS: dict[int, int] = {
    1077: 30,  # GPS TOW in ms
    1087: 27,  # GLONASS time-of-day in ms (3-hour window)
    1097: 30,  # Galileo TOW in ms
    1127: 30,  # BeiDou TOW in ms
}


def _build_rtcm_msm7(
    msg_type: int,
    station_id: int,
    epoch_ms: int,
    sats: list[tuple[int, int, int, int]],
    signal_mask: int = 1 << 30,  # signal code 2: GPS L1C / GLO L1C / GAL E1C / BDS B1I
) -> bytes:
    """Build a minimal but valid RTCM MSM7 observation frame.

    The frame contains carrier phase, pseudorange, CNR, and Doppler for a set
    of synthetic satellites — everything a rover needs to compute an RTK fix.

    Structure (RTCM 3.3 §3.5.11):
      Common MSM header → per-satellite DF397/398/399/400 → per-cell DF405/406/407/408/420/404

    One signal type per satellite (nSig=1 → nCell=nSat).

    Args:
        msg_type:    1077=GPS, 1087=GLONASS, 1097=Galileo, 1127=BeiDou.
        station_id:  Reference station ID (0–4095).
        epoch_ms:    GNSS epoch time in milliseconds (GPS TOW or equivalent).
        sats:        Satellite list — (prn, rough_int_ms, rough_frac, cnr_dbhz).
        signal_mask: 32-bit signal presence mask (MSB = signal code 1).

    Returns:
        Raw RTCM frame bytes ready to stream.
    """
    n_sat  = len(sats)
    n_cell = n_sat  # n_sig = 1

    # 64-bit satellite mask: PRN n → bit (64 – n).
    sat_mask = 0
    for prn, _, _, _ in sats:
        sat_mask |= 1 << (64 - prn)

    # All n_cell cells present.
    cell_mask = (1 << n_cell) - 1

    # Mask epoch to the field width (important for GLONASS 27-bit field).
    epoch_bits = _MSM7_EPOCH_BITS[msg_type]
    epoch_val  = epoch_ms & ((1 << epoch_bits) - 1)

    buf = 0   # accumulated bits
    n   = 0   # bit count

    def push(value: int, width: int) -> None:
        nonlocal buf, n
        buf = (buf << width) | (value & ((1 << width) - 1))
        n  += width

    def push_s(value: int, width: int) -> None:
        """Push a signed integer as two's-complement."""
        if value < 0:
            value += 1 << width
        push(value, width)

    push(msg_type, 12)   # message type
    push(station_id, 12) # reference station ID
    push(epoch_val, epoch_bits)  # GNSS epoch time
    push(0, 1)   # multiple message flag
    push(0, 3)   # IODS
    push(0, 7)   # reserved
    push(0, 2)   # clock steering indicator
    push(0, 2)   # external clock indicator
    push(0, 1)   # GNSS divergence-free smoothing
    push(0, 3)   # smoothing interval
    push(sat_mask,  64)    # satellite mask
    push(signal_mask, 32)  # signal mask
    push(cell_mask, n_cell)  # cell mask

    # Fields are stored in separate passes over all nSat satellites.
    for _, rough_int, _, _ in sats:
        push(rough_int, 8)   # DF397: integer ms
    for _ in sats:
        push(0, 4)           # DF398: extended satellite info
    for _, _, rough_frac, _ in sats:
        push(rough_frac, 10) # DF399: fractional ms (2^-10 ms units)

    for _ in sats:
        push_s(0, 14)        # DF399: rough phase range rate (m/s)

    # With nSig=1 the cell order matches the satellite order.
    # Field order and widths per pyrtcm MSM_SIG_7 / RTCM 10403.3:
    #   DF405 (INT 20) → DF406 (INT 24) → DF407 (UINT 10) →
    #   DF420 (BIT 1)  → DF408 (UINT 10) → DF404 (INT 15)
    # Note: MSM7 uses DF408 (10-bit extended CNR, 0.0625 dB-Hz/lsb), NOT
    # DF403 (6-bit, 1 dB-Hz/lsb) used by MSM4.
    for _ in range(n_cell):
        push_s(0, 20)        # DF405: fine pseudorange (2^-29 ms)
    for _ in range(n_cell):
        push_s(0, 24)        # DF406: fine phase range  (2^-31 ms)
    for _ in range(n_cell):
        push(1000, 10)       # DF407: phase range lock time indicator
    for _ in range(n_cell):
        push(0, 1)           # DF420: half-cycle ambiguity indicator
    for _, _, _, cnr in sats:
        push(cnr * 16, 10)   # DF408: CNR, extended (0.0625 dB-Hz/lsb → ×16)
    for _ in range(n_cell):
        push_s(0, 15)        # DF404: fine phase range rate (0.0001 m/s)

    # Pad to byte boundary.
    rem = n % 8
    if rem:
        push(0, 8 - rem)

    payload = buf.to_bytes(n // 8, "big")
    length  = len(payload)
    header  = bytes([0xD3, (length >> 8) & 0x03, length & 0xFF])
    raw     = header + payload
    crc     = calc_crc24q(raw)
    return raw + bytes([(crc >> 16) & 0xFF, (crc >> 8) & 0xFF, crc & 0xFF])


def _build_ntrip_str(mountpoint: str, label: str, lat: float, lon: float, country: str, network: str) -> str:
    """Build an ``Ntrip-STR`` header value describing this simulated base station.

    Field order matches RTCM 10410.1 §4.1 (without the leading ``STR;`` prefix).
    """
    fields = [
        mountpoint,        # [0]  mountpoint (redundant with URL path)
        label,             # [1]  human-readable label
        "RTCM 3.3",        # [2]  message format
        "1005(1),1077(1),1087(1),1097(1),1127(1)",  # [3]  message IDs and rates
        "2",               # [4]  carrier: 2=L1+L2 (phase observations present)
        "GPS+GLO+GAL+BDS", # [5]  nav systems
        network,           # [6]  network / agency
        country,           # [7]  ISO 3166-1 alpha-3
        str(lat),          # [8]  latitude
        str(lon),          # [9]  longitude
        "0",               # [10] NMEA: 0=does not accept
        "0",               # [11] solution: 0=single base
        "simulate-base-station/1.0",  # [12] generator
        "none",            # [13] compression
        "B",               # [14] auth: Basic
        "N",               # [15] fee: no
        "9600",            # [16] bitrate (approx)
    ]
    return ";".join(fields)


async def _frame_stream(
    station_id: int,
    x_m: float,
    y_m: float,
    z_m: float,
    rate_hz: float,
    stop: asyncio.Event,
) -> AsyncIterator[bytes]:
    """Yield one RTCM epoch burst per interval until *stop* is set.

    Each burst contains 1005 (position) followed by MSM7 observation messages
    for GPS (1077), GLONASS (1087), Galileo (1097), and BeiDou (1127).
    """
    interval = 1.0 / rate_hz
    frame1005 = _build_rtcm1005(station_id, x_m, y_m, z_m)
    while not stop.is_set():
        epoch_ms = _gps_tow_ms()
        burst = frame1005
        for msg_type, sats in _MSM7_SATS.items():
            burst += _build_rtcm_msm7(msg_type, station_id, epoch_ms, sats)
        log.debug("Epoch burst: %d bytes (1005 + MSM7 ×4)", len(burst))
        yield burst
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


async def stream_to_caster(
    *,
    url: str,
    username: str,
    password: str,
    ntrip_str: str,
    station_id: int,
    x_m: float,
    y_m: float,
    z_m: float,
    rate_hz: float,
) -> None:
    """Open a single long-lived NTRIP v2 PUT connection and stream frames.

    Raises on any HTTP or connection error so the caller can apply back-off.
    """
    stop = asyncio.Event()

    async def _generate() -> AsyncIterator[bytes]:
        async for frame in _frame_stream(station_id, x_m, y_m, z_m, rate_hz, stop):
            yield frame

    async with aiohttp.ClientSession() as session:
        async with session.put(
            url,
            data=_generate(),
            auth=aiohttp.BasicAuth(username, password),
            headers={
                "Ntrip-Version": "Ntrip/2.0",
                "Ntrip-STR": ntrip_str,
                "Content-Type": "gnss/data",
                "User-Agent": "simulate-base-station/1.0",
            },
            chunked=True,
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise aiohttp.ClientResponseError(
                    resp.request_info,
                    resp.history,
                    status=resp.status,
                    message=body.strip(),
                )

            log.info(
                "Connected — streaming 1005+MSM7(GPS/GLO/GAL/BDS) at %.1f Hz to %s",
                rate_hz, url,
            )

            bursts_sent = 0

            # Consume any server acknowledgements to keep the connection alive.
            async for chunk in resp.content:
                if not chunk:
                    break
                bursts_sent += 1
                if bursts_sent % 10 == 0:
                    log.info("Sent %d epoch bursts", bursts_sent)

    stop.set()


async def run(args: argparse.Namespace) -> None:
    x_m, y_m, z_m = llh2ecef(args.lat, args.lon, args.alt)
    log.info(
        "Station position: lat=%.6f lon=%.6f alt=%.1f m  →  ECEF (%.2f, %.2f, %.2f)",
        args.lat, args.lon, args.alt, x_m, y_m, z_m,
    )

    url = f"{args.server.rstrip('/')}/{args.mountpoint}"
    ntrip_str = _build_ntrip_str(
        args.mountpoint,
        args.label or args.mountpoint,
        args.lat,
        args.lon,
        args.country,
        args.network,
    )
    log.info("Target URL : %s", url)
    log.info("Ntrip-STR  : %s", ntrip_str)

    backoff = 1.0

    while True:
        try:
            await stream_to_caster(
                url=url,
                username=args.username,
                password=args.password,
                ntrip_str=ntrip_str,
                station_id=args.station_id,
                x_m=x_m,
                y_m=y_m,
                z_m=z_m,
                rate_hz=args.rate,
            )
            # Server closed the connection cleanly — reconnect immediately.
            log.warning("Server closed connection, reconnecting…")
            backoff = 1.0

        except asyncio.CancelledError:
            log.info("Interrupted, exiting.")
            return

        except Exception as exc:
            log.error("Connection error (retry in %.0f s): %s", backoff, exc)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Simulate an RTK base station pushing RTCM corrections to a CORSHub NTRIP caster.",
    )

    server_group = parser.add_argument_group("Server")
    server_group.add_argument(
        "--server",
        default="http://localhost:8000",
        metavar="URL",
        help="Caster base URL (default: http://localhost:8000)",
    )
    server_group.add_argument(
        "--mountpoint",
        default="HERE4",
        metavar="NAME",
        help="Mountpoint name (default: HERE4)",
    )
    server_group.add_argument(
        "--username",
        default="HERE4",
        metavar="USER",
        help="Basic auth username (default: HERE4)",
    )
    server_group.add_argument(
        "--password",
        default="secret",
        metavar="PASS",
        help="Basic auth password (default: secret)",
    )

    position_group = parser.add_argument_group("Position")
    position_group.add_argument(
        "--lat",
        type=float,
        default=50.8503,
        metavar="DEG",
        help="WGS-84 latitude in decimal degrees (default: 50.8503 — Brussels)",
    )
    position_group.add_argument(
        "--lon",
        type=float,
        default=4.3517,
        metavar="DEG",
        help="WGS-84 longitude in decimal degrees (default: 4.3517 — Brussels)",
    )
    position_group.add_argument(
        "--alt",
        type=float,
        default=50.0,
        metavar="M",
        help="Ellipsoidal altitude in metres (default: 50.0)",
    )

    meta_group = parser.add_argument_group("Metadata (optional Ntrip-STR)")
    meta_group.add_argument(
        "--label",
        default="",
        metavar="TEXT",
        help="Human-readable label for the mountpoint (defaults to mountpoint name)",
    )
    meta_group.add_argument(
        "--country",
        default="BEL",
        metavar="ISO3",
        help="ISO 3166-1 alpha-3 country code (default: BEL)",
    )
    meta_group.add_argument(
        "--network",
        default="",
        metavar="NAME",
        help="Network or agency name",
    )
    meta_group.add_argument(
        "--station-id",
        type=int,
        default=1,
        dest="station_id",
        metavar="ID",
        help="RTCM reference station ID 0–4095 (default: 1)",
    )

    stream_group = parser.add_argument_group("Stream")
    stream_group.add_argument(
        "--rate",
        type=float,
        default=1.0,
        metavar="HZ",
        help="Frame transmission rate in Hz (default: 1.0)",
    )
    stream_group.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )

    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass
