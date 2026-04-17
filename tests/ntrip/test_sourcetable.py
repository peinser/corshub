"""
Unit tests for NTRIP v2 source table formatting.

The NTRIP source table is the directory that rovers fetch from GET /
to discover available mountpoints.  The format is defined in RTCM 10410.1:

    One STR line per mountpoint:
        STR;<mp>;<id>;<fmt>;<fmt-detail>;<carrier>;<nav-sys>;<network>;
            <country>;<lat>;<lon>;<nmea>;<solution>;<generator>;
            <compr-encr>;<auth>;<fee>;<bitrate>;<misc>\\r\\n

    Final line (mandatory):
        ENDSOURCETABLE\\r\\n

Expected interface:
    corshub.ntrip.sourcetable.format_sourcetable(caster: NTRIPCaster) -> str
"""

from __future__ import annotations

import asyncio

import pytest

from corshub.ntrip.v2.caster import Mountpoint
from corshub.ntrip.v2.caster import NTRIPCaster
from corshub.ntrip.v2.sourcetable import format_sourcetable  # noqa: E402


def _str_lines(table: str) -> list[str]:
    return [line for line in table.splitlines() if line.startswith("STR;")]


async def _make_caster(*mountpoints: dict, **kwargs) -> NTRIPCaster:
    c = NTRIPCaster(**kwargs)
    await c.start()

    for mp in mountpoints:
        await c.register(**mp)

    return c


class TestSourceTableStructure:
    def test_empty_caster_ends_with_endsourcetable(self) -> None:
        table = format_sourcetable(NTRIPCaster())
        assert table.endswith("ENDSOURCETABLE\r\n")

    def test_empty_caster_has_no_str_lines(self) -> None:
        table = format_sourcetable(NTRIPCaster())
        assert _str_lines(table) == []

    def test_lines_use_crlf_terminator(self, caster: NTRIPCaster) -> None:
        table = format_sourcetable(caster)
        for line in table.split("\r\n")[:-1]:  # skip trailing empty after final \r\n
            assert "\n" not in line, f"bare LF found in line: {line!r}"

    def test_single_mountpoint_produces_one_str_line(self, caster: NTRIPCaster) -> None:
        assert len(_str_lines(format_sourcetable(caster))) == 1

    async def test_multiple_mountpoints_produce_multiple_str_lines(self) -> None:
        c = await _make_caster(
            {
                "mountpoint": "A",
                "identifier": "A",
                "format": "RTCM 3.3",
                "country": "BEL",
                "latitude": 50.0,
                "longitude": 4.0
            },
            {
                "mountpoint": "B",
                "identifier": "B",
                "format": "RTCM 3.3",
                "country": "NLD",
                "latitude": 52.0,
                "longitude": 5.0
            },
            {
                "mountpoint": "C",
                "identifier": "C",
                "format": "RTCM 3.3",
                "country": "DEU",
                "latitude": 48.0,
                "longitude": 10.0
            },
        )

        assert len(_str_lines(format_sourcetable(c))) == 3

    def test_str_line_starts_with_str_prefix(self, caster: NTRIPCaster) -> None:
        line = _str_lines(format_sourcetable(caster))[0]
        assert line.startswith("STR;")

    def test_str_line_has_at_least_19_semicolon_separated_fields(self, caster: NTRIPCaster) -> None:
        # NTRIP v2 spec defines 19 fields for an STR line.
        line = _str_lines(format_sourcetable(caster))[0]
        assert len(line.split(";")) >= 19


class TestSourceTableContent:
    def test_mountpoint_name_present_in_str_line(self, caster: NTRIPCaster) -> None:
        table = format_sourcetable(caster)
        assert "BASE1" in table

    def test_format_string_present_in_str_line(self, caster: NTRIPCaster) -> None:
        table = format_sourcetable(caster)
        assert "RTCM 3.3" in table

    def test_country_code_present_in_str_line(self, caster: NTRIPCaster) -> None:
        table = format_sourcetable(caster)
        assert "BEL" in table

    def test_latitude_present_in_str_line(self, caster: NTRIPCaster) -> None:
        table = format_sourcetable(caster)
        assert "50" in table  # 50.8503

    def test_longitude_present_in_str_line(self, caster: NTRIPCaster) -> None:
        table = format_sourcetable(caster)
        assert "4" in table  # 4.3517

    def test_mountpoint_name_is_second_field(self, caster: NTRIPCaster) -> None:
        line = _str_lines(format_sourcetable(caster))[0]
        fields = line.split(";")
        assert fields[1] == "BASE1"

    async def test_each_mountpoint_name_appears_in_own_str_line(self) -> None:
        c = await _make_caster(
            {
                "mountpoint": "ALPHA",
                "identifier": "ALPHA",
                "format": "RTCM 3.3",
                "country": "BEL",
                "latitude": 50.0,
                "longitude": 4.0
            },
            {
                "mountpoint": "BETA",
                "identifier": "BETA",
                "format": "RTCM 3.3",
                "country": "NLD",
                "latitude": 52.0,
                "longitude": 5.0
            },
        )
        str_lines = _str_lines(format_sourcetable(c))
        names = {line.split(";")[1] for line in str_lines}
        assert names == {"ALPHA", "BETA"}

    async def test_mountpoint_removal(self) -> None:
        reap_interval = 2.5

        c = await _make_caster(
            {
                "mountpoint": "ALPHA",
                "identifier": "ALPHA",
                "format": "RTCM 3.3",
                "country": "BEL",
                "latitude": 50.0,
                "longitude": 4.0
            },
            expiry=1.0,
            reap_interval=reap_interval,
        )

        str_lines = _str_lines(format_sourcetable(c))
        names = {line.split(";")[1] for line in str_lines}
        assert names == {"ALPHA"}

        # At this point, the reap thread shouldn't have executed.
        await asyncio.sleep(0)  # Yield to other coroutines.
        assert "ALPHA" in c.mountpoints

        await asyncio.sleep(reap_interval * 2)

        # The caster should have been removed from the mountpoints.
        assert "ALPHA" not in c.mountpoints
