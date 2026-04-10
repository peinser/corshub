r"""
A script simulating an RTK base station and pushing RTCM corrections to the local development server.

Generates synthetic RTCM 1005 (Reference Station ARP) frames at a configurable rate and streams
them to a CORSHub NTRIP v2 caster over a long-lived HTTP PUT connection.

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


def _build_ntrip_str(mountpoint: str, label: str, lat: float, lon: float, country: str, network: str) -> str:
    """Build an ``Ntrip-STR`` header value describing this simulated base station.

    Field order matches RTCM 10410.1 §4.1 (without the leading ``STR;`` prefix).
    """
    fields = [
        mountpoint,        # [0]  mountpoint (redundant with URL path)
        label,             # [1]  human-readable label
        "RTCM 3.3",        # [2]  message format
        "1005(1)",         # [3]  message IDs and rates
        "0",               # [4]  carrier: 0=none
        "GPS+GLO+GAL",     # [5]  nav systems
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
    """Yield synthetic RTCM 1005 frames at *rate_hz* until *stop* is set."""
    interval = 1.0 / rate_hz
    frame = _build_rtcm1005(station_id, x_m, y_m, z_m)
    log.debug("Pre-built RTCM 1005 frame: %d bytes", len(frame))
    while not stop.is_set():
        yield frame
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

            log.info("Connected — streaming RTCM 1005 at %.1f Hz to %s", rate_hz, url)

            frames_sent = 0
            bytes_sent = 0
            frame_size = len(_build_rtcm1005(station_id, x_m, y_m, z_m))

            # Consume any server acknowledgements to keep the connection alive.
            async for chunk in resp.content:
                if not chunk:
                    break
                frames_sent += 1
                bytes_sent += frame_size
                if frames_sent % 10 == 0:
                    log.info("Sent %d frames (%d bytes total)", frames_sent, bytes_sent)

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
