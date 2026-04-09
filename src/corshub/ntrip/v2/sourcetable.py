"""
NTRIP v2 source table formatting (RTCM 10410.1 §4.1).

The source table is the directory rovers fetch from ``GET /`` to discover
available mountpoints.  Each mountpoint produces one STR record line.

STR record field order (19 fields, semicolon-separated):
    STR;<name>;<identifier>;<format>;<format-detail>;<carrier>;<nav-system>;
        <network>;<country>;<latitude>;<longitude>;<nmea>;<solution>;
        <generator>;<compression>;<auth>;<fee>;<bitrate>;<misc>

The table is terminated by the mandatory ``ENDSOURCETABLE`` sentinel.
All lines use CRLF (\\r\\n) as required by the spec.
"""

from __future__ import annotations

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from corshub.ntrip.v2.caster import Caster
    from corshub.ntrip.v2.caster import Mountpoint


def _str_record(mp: Mountpoint) -> str:
    """Render a single mountpoint as a source table STR record line.

    Field order matches RTCM 10410.1 §4.1 Table 4 exactly.  The trailing
    ``<misc>`` field is left empty as it is implementation-defined.
    """
    return ";".join(
        [
            "STR",
            mp.name,  # [0]  mountpoint name
            mp.identifier,  # [1]  human-readable label
            mp.format,  # [2]  message format
            mp.format_detail,  # [3]  message IDs and rates
            str(mp.carrier),  # [4]  carrier: 0/1/2
            mp.nav_system,  # [5]  nav systems e.g. GPS+GLO
            mp.network,  # [6]  network / agency
            mp.country,  # [7]  ISO 3166-1 alpha-3
            f"{mp.latitude:.2f}" if mp.latitude is not None else "",  # [8]  latitude  (2 d.p. per spec)
            f"{mp.longitude:.2f}" if mp.longitude is not None else "",  # [9]  longitude (2 d.p. per spec)
            "1" if mp.nmea else "0",  # [10] NMEA accepted
            str(mp.solution),  # [11] 0=single base, 1=network
            mp.generator,  # [12] generator software
            mp.compression,  # [13] compression/encryption
            mp.auth,  # [14] N/B/D
            mp.fee,  # [15] N/Y
            str(mp.bitrate),  # [16] bit rate (0=unknown)
            "",  # [17] misc (implementation-defined)
        ]
    )


def format_sourcetable(caster: Caster) -> str:
    """Render the full NTRIP source table for all registered mountpoints.

    Returns a string with CRLF line endings, terminated by
    ``ENDSOURCETABLE\\r\\n``, as required by RTCM 10410.1 §4.1.
    """
    lines = [_str_record(mp) for mp in caster.mountpoints.values()]
    lines.append("ENDSOURCETABLE")
    return "\r\n".join(lines) + "\r\n"
