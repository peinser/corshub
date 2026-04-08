"""
Unit tests for NTRIP v2 header parsing utilities.

Functions under test:
    parse_ntrip_gga(header)  →  (lat, lon) | None
    haversine(lat1, lon1, lat2, lon2)  →  float (km)
"""

from __future__ import annotations

import pytest

from corshub.ntrip.v2.headers import haversine
from corshub.ntrip.v2.headers import parse_ntrip_gga


def _gga(lat: float, lon: float) -> str:
    """Build a minimal valid GPGGA sentence for the given WGS-84 position."""
    lat_deg = int(abs(lat))
    lat_min = (abs(lat) - lat_deg) * 60
    lon_deg = int(abs(lon))
    lon_min = (abs(lon) - lon_deg) * 60
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    body = (
        f"GPGGA,120000.00,"
        f"{lat_deg:02d}{lat_min:08.5f},{ns},"
        f"{lon_deg:03d}{lon_min:08.5f},{ew},"
        f"1,08,1.0,0.0,M,0.0,M,,"
    )
    checksum = 0
    for ch in body:
        checksum ^= ord(ch)
    return f"${body}*{checksum:02X}"


class TestParseNtripGga:

    def test_none_returns_none(self) -> None:
        assert parse_ntrip_gga(None) is None

    def test_empty_string_returns_none(self) -> None:
        assert parse_ntrip_gga("") is None

    def test_whitespace_only_returns_none(self) -> None:
        assert parse_ntrip_gga("   ") is None

    def test_garbage_string_returns_none(self) -> None:
        assert parse_ntrip_gga("not a sentence") is None

    def test_known_valid_gga_parses_correctly(self) -> None:
        # $GPGGA,123519,4807.038,N,01131.000,E — lat=48.1173°, lon=11.5167°
        result = parse_ntrip_gga("$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47")
        assert result is not None
        lat, lon = result
        assert abs(lat - 48.1173) < 1e-4
        assert abs(lon - 11.5167) < 1e-4

    def test_generated_gga_round_trips(self) -> None:
        orig_lat, orig_lon = 50.8503, 4.3517
        result = parse_ntrip_gga(_gga(orig_lat, orig_lon))
        assert result is not None
        lat, lon = result
        assert abs(lat - orig_lat) < 1e-4
        assert abs(lon - orig_lon) < 1e-4

    def test_south_latitude_returns_negative(self) -> None:
        result = parse_ntrip_gga(_gga(-33.8688, 151.2093))
        assert result is not None
        lat, _ = result
        assert lat < 0
        assert abs(lat - (-33.8688)) < 1e-3

    def test_west_longitude_returns_negative(self) -> None:
        result = parse_ntrip_gga(_gga(40.7128, -74.0060))
        assert result is not None
        _, lon = result
        assert lon < 0
        assert abs(lon - (-74.0060)) < 1e-3

    def test_south_west_both_negative(self) -> None:
        result = parse_ntrip_gga(_gga(-34.6037, -58.3816))  # Buenos Aires
        assert result is not None
        lat, lon = result
        assert lat < 0
        assert lon < 0

    def test_leading_whitespace_is_stripped(self) -> None:
        gga = "  $GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47"
        assert parse_ntrip_gga(gga) is not None

    def test_trailing_whitespace_is_stripped(self) -> None:
        gga = "$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47  "
        assert parse_ntrip_gga(gga) is not None

    def test_non_gga_sentence_returns_none(self) -> None:
        # GPGLL is not GGA
        assert parse_ntrip_gga("$GPGLL,4807.038,N,01131.000,E,123519,A*26") is None

    def test_invalid_checksum_returns_none(self) -> None:
        assert parse_ntrip_gga("$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*00") is None

    def test_result_is_float_tuple(self) -> None:
        result = parse_ntrip_gga("$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47")
        assert result is not None
        lat, lon = result
        assert isinstance(lat, float)
        assert isinstance(lon, float)


class TestHaversine:

    def test_same_point_is_zero(self) -> None:
        assert haversine(50.85, 4.35, 50.85, 4.35) == 0.0

    def test_returns_float(self) -> None:
        assert isinstance(haversine(0.0, 0.0, 1.0, 0.0), float)

    def test_is_symmetric(self) -> None:
        d1 = haversine(50.85, 4.35, 52.37, 4.90)
        d2 = haversine(52.37, 4.90, 50.85, 4.35)
        assert abs(d1 - d2) < 1e-6

    def test_one_degree_latitude_is_approx_111_km(self) -> None:
        # 1° of latitude ≈ 111.195 km
        dist = haversine(0.0, 0.0, 1.0, 0.0)
        assert 110.0 < dist < 112.0

    def test_brussels_to_amsterdam_approx_173_km(self) -> None:
        dist = haversine(50.8503, 4.3517, 52.3676, 4.9041)
        assert 170.0 < dist < 177.0

    def test_distance_is_positive(self) -> None:
        dist = haversine(50.0, 4.0, 52.0, 5.0)
        assert dist > 0

    def test_antipodal_points_roughly_half_earth_circumference(self) -> None:
        # Antipode of (0, 0) is (0, 180); half Earth circumference ≈ 20015 km
        dist = haversine(0.0, 0.0, 0.0, 180.0)
        assert 20000.0 < dist < 20030.0
