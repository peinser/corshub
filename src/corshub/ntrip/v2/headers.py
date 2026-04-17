"""
NTRIP v2 HTTP header constants and parsers.

NTRIP v2 defines several custom HTTP headers (RTCM 10410.1 §4):
    Ntrip-Version    Required on all requests.
    Ntrip-STR        Optional — base station self-description (same fields as
                     a source table STR line, without the leading "STR;" token).
    Ntrip-GGA        Optional — rover approximate position as a NMEA GGA sentence.

This module owns the field indices, defaults, and parsing logic for these headers
so that route handlers stay focused on HTTP flow control.
"""

from __future__ import annotations

import math

from typing import TYPE_CHECKING

from pynmeagps import NMEAReader


if TYPE_CHECKING:
    from typing import Final


NTRIP_VERSION = "Ntrip-Version"
NTRIP_VERSION_2 = "Ntrip/2.0"
NTRIP_STR = "Ntrip-STR"
NTRIP_GGA = "Ntrip-GGA"

CONTENT_TYPE_GNSS = "gnss/data"

# Semicolon-separated fields in the same order as a source table STR record,
# but without the leading "STR;" prefix.  See RTCM 10410.1 §4.1 for the full
# source table specification.

STR_MOUNTPOINT = 0  # mountpoint name (redundant with URL path)
STR_IDENTIFIER = 1  # human-readable label / location description (I hate this name, should have been NAME)
STR_FORMAT = 2  # message format, e.g. "RTCM 3.3"
STR_FORMAT_DETAIL = 3  # message IDs and rates, e.g. "1004(1),1005(5)"
STR_CARRIER = 4  # 0=none, 1=L1, 2=L1+L2
STR_NAV_SYSTEM = 5  # e.g. "GPS+GLO+GAL+BDS"
STR_NETWORK = 6  # network / agency name
STR_COUNTRY = 7  # ISO 3166-1 alpha-3, e.g. "BEL"
STR_LATITUDE = 8  # WGS-84 latitude in decimal degrees
STR_LONGITUDE = 9  # WGS-84 longitude in decimal degrees
STR_NMEA = 10  # 0=no NMEA, 1=accepts NMEA GGA from rover
STR_SOLUTION = 11  # 0=single base, 1=network solution
STR_GENERATOR = 12  # software generating the stream
STR_COMPRESSION = 13  # compression/encryption identifier
STR_AUTH = 14  # N=none, B=basic, D=digest
STR_FEE = 15  # N=no fee, Y=fee required
STR_BITRATE = 16  # approximate bit rate in bits/s

STR_MIN_FIELDS = STR_LONGITUDE + 1  # minimum to extract coordinates


def parse_ntrip_str(
    header: str | None,
    mountpoint_id: str,
) -> dict[str, str | float | int | bool]:
    """Extract all known fields from an ``Ntrip-STR`` header value.

    Returns a dict with keys matching the metadata fields of ``Mountpoint``.
    Any field that is absent, empty, or unparseable falls back to a safe
    default so that base stations that omit ``Ntrip-STR`` can still connect.

    Args:
        header:        Raw value of the ``Ntrip-STR`` header, or ``None``.
        mountpoint_id: URL path segment used as the fallback name.

    Returns:
        Dict of parsed metadata ready to be unpacked into ``Caster.register``.
    """
    defaults: dict[str, str | float | int | bool] = {
        "name": mountpoint_id,
        "format": "RTCM 3.3",
        "format_detail": "",
        "carrier": 0,
        "nav_system": "",
        "network": "",
        "country": "UNK",
        "latitude": 0.0,
        "longitude": 0.0,
        "nmea": False,
        "solution": 0,
        "generator": "",
        "compression": "",
        "auth": "B",
        "fee": "N",
        "bitrate": 0,
    }

    if not header:
        return defaults

    fields = header.split(";")

    # Remove leading 'STR'
    del fields[0]

    def _str(idx: int) -> str | None:
        v = fields[idx].strip() if idx < len(fields) else None
        return v or None

    def _int(idx: int) -> int | None:
        try:
            return int(fields[idx]) if idx < len(fields) else None
        except ValueError, IndexError:
            return None

    def _float(idx: int) -> float | None:
        try:
            return float(fields[idx]) if idx < len(fields) else None
        except ValueError, IndexError:
            return None

    if (v := _str(STR_MOUNTPOINT)) is not None:
        defaults["identifier"] = v
    if (v := _str(STR_FORMAT)) is not None:
        defaults["format"] = v
    if (v := _str(STR_FORMAT_DETAIL)) is not None:
        defaults["format_detail"] = v
    if (i := _int(STR_CARRIER)) is not None and i in (0, 1, 2):
        defaults["carrier"] = i
    if (v := _str(STR_NAV_SYSTEM)) is not None:
        defaults["nav_system"] = v
    if (v := _str(STR_NETWORK)) is not None:
        defaults["network"] = v
    if len(fields) >= STR_MIN_FIELDS:
        if (v := _str(STR_COUNTRY)) is not None and len(v) == 3:
            defaults["country"] = v.upper()
        if (f := _float(STR_LATITUDE)) is not None and -90.0 <= f <= 90.0:
            defaults["latitude"] = f
        if (f := _float(STR_LONGITUDE)) is not None and -180.0 <= f <= 180.0:
            defaults["longitude"] = f
    if (i := _int(STR_NMEA)) is not None and i in (0, 1):
        defaults["nmea"] = bool(i)
    if (i := _int(STR_SOLUTION)) is not None and i in (0, 1):
        defaults["solution"] = i
    if (v := _str(STR_GENERATOR)) is not None:
        defaults["generator"] = v
    if (v := _str(STR_COMPRESSION)) is not None:
        defaults["compression"] = v
    if (v := _str(STR_AUTH)) is not None and v in ("N", "B", "D"):
        defaults["auth"] = v
    if (v := _str(STR_FEE)) is not None and v in ("N", "Y"):
        defaults["fee"] = v
    if (i := _int(STR_BITRATE)) is not None and i >= 0:
        defaults["bitrate"] = i

    return defaults


def parse_ntrip_gga(header: str | None) -> tuple[float, float] | None:
    """Parse an ``Ntrip-GGA`` header value as a NMEA GGA sentence.

    Returns ``(latitude, longitude)`` in decimal degrees (WGS-84), or ``None``
    if the header is absent, empty, or cannot be parsed as a valid GGA sentence.

    Args:
        header: Raw value of the ``Ntrip-GGA`` header, or ``None``.

    Returns:
        ``(latitude, longitude)`` tuple, or ``None`` on any parse failure.
    """
    if not header:
        return None
    try:
        msg = NMEAReader.parse(header.strip())
        if msg is None or msg.msgID != "GGA":
            return None

        lat = msg.lat
        lon = msg.lon

        if lat is None or lon is None:
            return None

        return (float(lat), float(lon))

    except Exception:
        return None


R_EARTH_KM: Final[float] = 6371.0  # mean Earth radius in km


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return the great-circle distance in kilometres between two WGS-84 points.

    Args:
        lat1, lon1: First point in decimal degrees.
        lat2, lon2: Second point in decimal degrees.

    Returns:
        Distance in km.
    """
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R_EARTH_KM * 2 * math.asin(math.sqrt(a))
