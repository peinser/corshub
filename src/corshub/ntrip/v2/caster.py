"""
NTRIP v2 caster — mountpoint registry, credentials, and lifecycle management.

NTRIPCaster is the central authority for:
  - Registering and unregistering mountpoints
  - Validating base-station credentials
  - Delegating frame delivery to per-mountpoint Transport instances
  - Expiring stale mountpoints (base station went silent without disconnecting)

Responsibilities deliberately outside this module:
  - How bytes move between publishers and subscribers  → transport.py
  - How the source table is serialised                 → sourcetable.py
  - HTTP framing / Sanic route handling                → services/v1/ntrip/
"""

from __future__ import annotations

import asyncio
import re
import time

from abc import ABC
from abc import abstractmethod
from collections.abc import AsyncGenerator
from contextlib import AbstractAsyncContextManager
from contextlib import asynccontextmanager
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from io import BytesIO
from typing import TYPE_CHECKING

from pyrtcm import RTCMReader

import corshub.metrics as metrics

from corshub import env
from corshub.crypto import secrets
from corshub.logging import logger
from corshub.ntrip.v2.quality import MountpointQuality
from corshub.ntrip.v2.transport import QueueTransport


if TYPE_CHECKING:
    from typing import Final

    from corshub.ntrip.v2.transport import Transport
    from corshub.ntrip.v2.transport import TransportSubscriber
    from corshub.opa.client import OPAClient


_MOUNTPOINT_RE = re.compile(r"^[A-Za-z0-9_-]{1,100}$")

# Maps the hundreds digit of an MSM message type to its GNSS constellation name.
# MSM types run from <base>1 (MSM1) to <base>7 (MSM7); only MSM4-7 carry CNR (DF403).
_MSM_CONSTELLATION: dict[int, str] = {
    107: "GPS",
    108: "GLONASS",
    109: "Galileo",
    110: "SBAS",
    111: "QZSS",
    112: "BeiDou",
    113: "NavIC",
}

# ARP deviation above this threshold (metres) counts as a position change.
_ARP_CHANGE_THRESHOLD: float = env.extract("GNSS_ARP_CHANGE_THRESHOLD", default="0.01", dtype=float)

# CNR observations above this level are counted as anomalously high.
_HIGH_CNR_THRESHOLD_DBHZ: float = env.extract("GNSS_CNR_DBHZ_HIGH_THRESHOLD", default="55.0", dtype=float)


_RTCM3_PREAMBLE = 0xD3
_RTCM3_MAX_FRAME = 1029  # 3 header + 1023 max payload + 3 CRC


def _split_rtcm_frames(data: bytes) -> tuple[list[bytes], bytes]:
    """Extract complete RTCM3 frames from *data*.

    RTCM3 framing: 0xD3 preamble, 10-bit length in bytes 1-2, payload, 3-byte
    CRC.  Total frame = length + 6 bytes.  Returns a list of complete frames
    and any trailing bytes that form an incomplete frame, which should be
    prepended to the next incoming chunk.

    Discards bytes before a preamble and frames that exceed the RTCM3 maximum
    size, both of which indicate corruption or non-RTCM data.
    """
    frames: list[bytes] = []
    i = 0
    while i < len(data):
        if data[i] != _RTCM3_PREAMBLE:
            i += 1
            continue
        if i + 3 > len(data):
            break  # Need more bytes to read the length field.
        length = ((data[i + 1] & 0x03) << 8) | data[i + 2]
        total = length + 6
        if total > _RTCM3_MAX_FRAME:
            i += 1  # Spurious 0xD3 byte; keep scanning.
            continue
        if i + total > len(data):
            break  # Incomplete frame; wait for the next chunk.
        frames.append(data[i : i + total])
        i += total
    return frames, data[i:]


def _observe_rtcm_quality(
    mountpoint: str,
    chunk: bytes,
    arp_reference: dict[str, tuple[float, float, float]],
    frame_buffer: dict[str, bytes],
    quality_snapshots: dict[str, MountpointQuality] | None = None,
) -> None:
    """Accumulate *chunk* into a per-mountpoint buffer and parse complete RTCM3
    frames, recording signal-quality metrics for each.

    HTTP chunks are not RTCM frame-aligned. Incomplete frames at the end of a
    chunk are held in *frame_buffer* and prepended to the next chunk so that
    every frame is parsed exactly once regardless of chunk boundaries.

    For each complete frame the message type counter is incremented.  MSM4-7
    frames also record per-cell CNR and satellite count.  RTCM 1005/1006
    messages track the ARP position and increment the change counter on any
    deviation beyond the configured threshold.
    """
    data = frame_buffer.pop(mountpoint, b"") + chunk
    complete_frames, remainder = _split_rtcm_frames(data)

    # Discard buffers that are growing without yielding complete frames —
    # this guards against a base station that sends non-RTCM data indefinitely.
    if remainder and len(remainder) < _RTCM3_MAX_FRAME:
        frame_buffer[mountpoint] = remainder

    for frame in complete_frames:
        reader = RTCMReader(BytesIO(frame), quitonerror=2, parsed=True)
        try:
            _, msg = reader.read()
        except Exception as ex:
            logger.exception(ex)
            metrics.rtcm_parse_errors_total.labels(mountpoint=mountpoint).inc()
            continue

        if msg is None:
            continue  # Unknown message type — pyrtcm returns (raw, None) for unrecognised IDs.

        msg_type = int(msg.identity)

        metrics.rtcm_messages_total.labels(
            mountpoint=mountpoint,
            message_type=str(msg_type),
        ).inc()

        # ARP position tracking from RTCM 1005 (and 1006 which adds antenna height).
        if msg_type in (1005, 1006):
            try:
                x, y, z = float(msg.DF025), float(msg.DF026), float(msg.DF027)
            except AttributeError:
                continue

            metrics.base_station_arp_ecef_meters.labels(mountpoint=mountpoint, axis="x").set(x)
            metrics.base_station_arp_ecef_meters.labels(mountpoint=mountpoint, axis="y").set(y)
            metrics.base_station_arp_ecef_meters.labels(mountpoint=mountpoint, axis="z").set(z)
            ref = arp_reference.get(mountpoint)
            if ref is None:
                arp_reference[mountpoint] = (x, y, z)

            elif max(abs(x - ref[0]), abs(y - ref[1]), abs(z - ref[2])) > _ARP_CHANGE_THRESHOLD:
                metrics.base_station_arp_changes_total.labels(mountpoint=mountpoint).inc()
                arp_reference[mountpoint] = (x, y, z)

            continue

        constellation = _MSM_CONSTELLATION.get(msg_type // 10)
        if constellation is None:
            continue

        msm_variant = msg_type % 10
        msg_attrs = vars(msg)

        # Satellite count. PRN_XX attributes are present in all MSM types.
        nsat = sum(1 for k in msg_attrs if k.startswith("PRN_"))
        if nsat:
            metrics.rtcm_satellites_tracked.labels(mountpoint=mountpoint, constellation=constellation).observe(nsat)
            if quality_snapshots is not None:
                quality_snapshots.setdefault(mountpoint, MountpointQuality()).record_sat_count(constellation, nsat)

        # CNR is present in MSM4-7: DF403 (MSM4/5) or DF408 extended (MSM6/7).
        if msm_variant >= 4:
            cnr_prefix = "DF408_" if msm_variant >= 6 else "DF403_"
            cnr_batch: list[float] = []
            for attr, val in msg_attrs.items():
                if attr.startswith(cnr_prefix) and isinstance(val, (int, float)) and val > 0:
                    cnr = float(val)
                    metrics.rtcm_signal_cnr_dbhz.labels(mountpoint=mountpoint, constellation=constellation).observe(cnr)
                    if cnr > _HIGH_CNR_THRESHOLD_DBHZ:
                        metrics.rtcm_high_cnr_total.labels(mountpoint=mountpoint, constellation=constellation).inc()
                    cnr_batch.append(cnr)
            if cnr_batch and quality_snapshots is not None:
                quality_snapshots.setdefault(mountpoint, MountpointQuality()).record_cnr(constellation, cnr_batch)


_IDENTIFIER_RE = re.compile(r"[^;]+")


@dataclass
class Mountpoint:
    """Metadata for a single NTRIP mountpoint.

    Fields map directly to the STR record in the NTRIP source table
    (RTCM 10410.1 §4.1) plus the credential used to authenticate the
    base station that pushes corrections to this mountpoint.

    Attributes:
        name: Mountpoint identifier used in the URL path (e.g. ``BASE-1``).
            Alphanumeric, hyphens, and underscores, 1/100 characters (per spec).
        identifier: Human-readable label for the source table STR record,
            typically describing the physical location.
        format: RTCM message format, e.g. ``"RTCM 3.3"``.
        format_detail: Comma-separated list of message IDs and rates,
            e.g. ``"1004(1),1005(5),1012(1)"``.  Empty string if unknown.
        carrier: Phase information available.
            ``0`` = none, ``1`` = L1 only, ``2`` = L1 + L2.
        nav_system: GNSS constellation(s) tracked, e.g. ``"GPS+GLO+GAL+BDS"``.
        network: Network or agency name this mountpoint belongs to.
        country: ISO 3166-1 alpha-3 country code (exactly 3 uppercase letters),
            e.g. ``"BEL"``, ``"NLD"``, ``"DEU"``.
        latitude: WGS-84 geodetic latitude in decimal degrees [-90, 90].
            Source table renders this with 2 decimal places.
        longitude: WGS-84 geodetic longitude in decimal degrees [-180, 180].
            Source table renders this with 2 decimal places.
        nmea: Whether the caster accepts NMEA GGA sentences from rovers on
            this mountpoint.  ``False`` = no, ``True`` = yes.
        mask: Maximum allowed distance in km between the rover's reported
            position (from ``Ntrip-GGA``) and the mountpoint's configured
            position.  ``0.0`` disables the distance check (unlimited range).
        solution: ``0`` = single base station, ``1`` = network/VRS solution.
        generator: Software or firmware generating the RTCM stream.
        compression: Compression or encryption in use, or empty string.
        auth: Authentication required for rovers.
            ``"N"`` = none, ``"B"`` = Basic, ``"D"`` = Digest.
        fee: ``"N"`` = no fee, ``"Y"`` = fee required.
        bitrate: Approximate stream bit rate in bits/s.  ``0`` if unknown.
        last_seen: Monotonic timestamp of the last frame received.  Updated by
            the caster on every successful ``publish`` call.  Excluded from
            equality comparison.
    """

    name: str
    identifier: str | None = None
    format: str | None = None
    country: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    format_detail: str = ""
    carrier: int = 0
    nav_system: str = ""
    network: str = ""
    nmea: bool = False
    mask: float = 0.0
    solution: int = 0
    generator: str = ""
    compression: str = ""
    auth: str = "B"
    fee: str = "N"
    bitrate: int = 0
    last_seen: float = field(default_factory=time.monotonic, compare=False)

    def __post_init__(self) -> None:
        if not _MOUNTPOINT_RE.match(self.name):
            raise ValueError(
                f"Mountpoint name {self.name!r} is invalid: must be 1/100 characters, alphanumeric, underscore, or hyphen."
            )
        if self.identifier and not _IDENTIFIER_RE.match(self.identifier):
            raise ValueError(
                f"Mountpoint identifier {self.identifier!r} is invalid: must be 1/100 characters, alphanumeric, underscore, or hyphen."
            )
        if self.country and not re.match(r"^[A-Z]{3}$", self.country):
            raise ValueError(f"Country {self.country!r} is invalid: must be an ISO 3166-1 alpha-3 code (e.g. 'BEL').")
        if self.latitude is not None and not -90.0 <= self.latitude <= 90.0:
            raise ValueError(f"Latitude {self.latitude} is out of range [-90, 90].")
        if self.longitude is not None and not -180.0 <= self.longitude <= 180.0:
            raise ValueError(f"Longitude {self.longitude} is out of range [-180, 180].")
        if self.mask < 0.0:
            raise ValueError(f"Mask {self.mask} must be >= 0.")

    dict = asdict  # for easy serialisation to STR fields in the source table


class Caster(ABC):
    @abstractmethod
    def register(self, mountpoint: Mountpoint) -> None:
        """Add *mountpoint* to the registry and open its transport.

        Raises ValueError if a mountpoint with the same name already exists.
        """

    @abstractmethod
    def unregister(self, identifier: str) -> None:
        """Remove *identifier* from the registry and close its transport.

        Raises KeyError if *identifier* is not registered.
        """

    @abstractmethod
    def close(self, identifier: str) -> None:
        """Does NOT remove the mountpoint from the registry, but does close the transports."""

    @abstractmethod
    async def available(self, identifier: str) -> bool:
        """Return True if *identifier* is registered AND has an active transport."""

    @property
    @abstractmethod
    def mountpoints(self) -> dict[str, Mountpoint]:
        """Read-only view of currently registered mountpoints."""

    @abstractmethod
    async def authenticate_base_station(self, username: str, password: str) -> bool:
        """Return True if *username*/*password* identify a valid base station.

        The username maps directly to a mountpoint by convention, so no
        separate mountpoint argument is required.
        Raises no exception — returns False on any auth failure or error.
        """

    @abstractmethod
    async def authenticate_rover(self, username: str, password: str, mountpoint: str) -> tuple[bool, int | None]:
        """Return True if *username*/*password* may subscribe to *mountpoint* and returns
        an additional max session duration that needs to be imposed by the routes.

        Raises no exception — returns False on any auth failure or error.
        """

    @abstractmethod
    async def publish(self, name: str, frame: bytes) -> int:
        """Deliver *frame* to all rovers subscribed to *name*.

        Updates the mountpoint's last_seen timestamp.
        Returns subscriber count, or 0 if *name* is unknown or has no rovers.
        Never raises KeyError.
        """

    @abstractmethod
    def subscribe(self, name: str) -> AbstractAsyncContextManager[TransportSubscriber]:
        """Return an async context manager that streams frames to a rover.

        Raises KeyError if *name* is not registered.

        Usage::

            async with caster.subscribe(mountpoint) as sub:
                while (frame := await sub.get()) is not None:
                    ...
        """

    @abstractmethod
    async def start(self) -> None:
        """Start any background tasks (e.g. stale-mountpoint reaper).

        Call once after the event loop is running — typically in a Sanic
        before_server_start listener.
        """

    @abstractmethod
    async def stop(self) -> None:
        """Cancel background tasks and release resources gracefully.

        Call in a Sanic after_server_stop listener.
        """


class NTRIPCaster(Caster):
    """Default single-server NTRIPCaster (V2) backed by QueueTransport.

    Args:
        transport_factory: Callable that returns a fresh Transport for each
            new mountpoint.  Defaults to QueueTransport.  Inject a different
            factory to switch the transport backend (Redis, NATS, …) without
            changing any other code.
        expiry: Seconds of silence after which a mountpoint is considered stale
            and automatically unregistered.  Set to None to disable expiry.
        reap_interval: How often (seconds) the background reaper checks for
            stale mountpoints.
    """

    _METADATA_FIELDS: Final[set[str]] = {
        "name",
        "format",
        "format_detail",
        "carrier",
        "nav_system",
        "network",
        "country",
        "latitude",
        "longitude",
        "nmea",
        "mask",
        "solution",
        "generator",
        "compression",
        "auth",
        "fee",
        "bitrate",
    }

    def __init__(
        self,
        opa: OPAClient | None = None,
        transport_factory: type[Transport] | None = None,
        expiry: float | None = 3600.0,
        reap_interval: float = 10.0,
    ) -> None:
        self._opa = opa
        self._transport_factory: type[Transport] = transport_factory or QueueTransport
        self._expiry = expiry
        self._reap_interval = reap_interval
        self._mountpoints: dict[str, Mountpoint] = {}
        self._transports: dict[str, Transport] = {}
        self._reaper_task: asyncio.Task[None] | None = None
        self._arp_reference: dict[str, tuple[float, float, float]] = {}
        self._frame_buffer: dict[str, bytes] = {}
        self._quality: dict[str, MountpointQuality] = {}
        # Keyed by mountpoint → {connection_id → (lat, lon)}.  Updated by the
        # GGA reader in the rover GET handler; cleared on disconnect/reaper.
        self._rover_positions: dict[str, dict[str, tuple[float, float]]] = {}

    async def register(self, mountpoint: str, **kwargs: dict) -> Mountpoint:
        if mountpoint in self._mountpoints:
            instance = self._mountpoints[mountpoint]
            for key, value in kwargs.items():
                if key in NTRIPCaster._METADATA_FIELDS and value is not None:
                    setattr(instance, key, value)

            # Check if the transport is defined, this can happen if the transport is closed but the mountpoint
            # hasn't been unregistered.
            if not self._transports.get(mountpoint):
                self._transports[mountpoint] = self._transport_factory()

            return instance

        kwargs.pop(
            "name", None
        )  # Ensure `name` is not present in mountpoint metadata, it will be supplied explicitely below.

        mp = Mountpoint(name=mountpoint, **kwargs)
        self._mountpoints[mountpoint] = mp
        self._transports[mountpoint] = self._transport_factory()

        return mp

    async def available(self, mountpoint: str) -> bool:
        if mountpoint not in self._mountpoints:
            return False

        return self._transports.get(mountpoint) is not None

    def set_rover_position(self, mountpoint: str, connection_id: str, lat: float, lon: float) -> None:
        self._rover_positions.setdefault(mountpoint, {})[connection_id] = (lat, lon)

    def clear_rover_position(self, mountpoint: str, connection_id: str) -> None:
        per_mp = self._rover_positions.get(mountpoint)
        if per_mp is not None:
            per_mp.pop(connection_id, None)
            if not per_mp:
                self._rover_positions.pop(mountpoint, None)

    def get_rover_positions(self, mountpoint: str) -> dict[str, tuple[float, float]]:
        """Return {rover_id: (lat, lon)} for all rovers with a known position on *mountpoint*."""
        return dict(self._rover_positions.get(mountpoint, {}))

    async def close(self, mountpoint: str) -> None:
        if mountpoint not in self._mountpoints:
            return

        transport = self._transports[mountpoint]
        del self._transports[mountpoint]
        self._frame_buffer.pop(mountpoint, None)
        self._quality.pop(mountpoint, None)
        self._rover_positions.pop(mountpoint, None)
        await transport.shutdown()  # Signal the subscribers the base-station is leaving.

    async def unregister(self, mountpoint: str) -> None:
        if mountpoint not in self._mountpoints:
            return  # Idempotent

        transport = self._transports.pop(mountpoint, None)
        self._frame_buffer.pop(mountpoint, None)
        self._quality.pop(mountpoint, None)
        self._rover_positions.pop(mountpoint, None)
        del self._mountpoints[mountpoint]

        if transport is not None:
            await transport.shutdown()

    @property
    def mountpoints(self) -> dict[str, Mountpoint]:
        return self._mountpoints

    @property
    def transports(self) -> dict[str, Transport]:
        return self._transports

    @property
    def rover_positions(self) -> dict[str, dict[str, tuple[float, float]]]:
        """Live view of {mountpoint: {rover_id: (lat, lon)}} for all connected rovers with a known position."""
        return self._rover_positions

    async def authenticate_base_station(self, username: str, password: str, mountpoint: str) -> bool:
        if self._opa is None:
            return False

        input = {
            "username": username,
            "mountpoint": mountpoint,
            "transport": {
                "available": await self.available(mountpoint),
            },
        }

        result = await self._opa.query("corshub/base_station", input)
        if not result.get("allow"):
            metrics.auth_requests_total.labels(role="base_station", result="failure").inc()
            return False

        stored_hash: str = result.get("password_hash", "")
        allowed = bool(stored_hash) and await secrets.verify(password, stored_hash)
        metrics.auth_requests_total.labels(role="base_station", result="success" if allowed else "failure").inc()
        return allowed

    async def authenticate_rover(self, username: str, password: str, mountpoint: str) -> tuple[bool, int | None]:
        """Authenticate a rover against the OPA policy.

        Returns a ``(allowed, max_session_seconds)`` tuple.  ``max_session_seconds``
        is ``None`` when the policy imposes no session limit.
        """
        if self._opa is None:
            return False, None

        result = await self._opa.query("corshub/rover", {"username": username, "mountpoint": mountpoint})
        if not result.get("allow"):
            metrics.auth_requests_total.labels(role="rover", result="failure").inc()
            return False, None

        stored_hash: str = result.get("password_hash", "")
        allowed = bool(stored_hash) and await secrets.verify(password, stored_hash)
        metrics.auth_requests_total.labels(role="rover", result="success" if allowed else "failure").inc()
        # Policy decides whether this should be returned.
        max_session_seconds: int | None = result.get("max_session_seconds")

        return allowed, max_session_seconds

    async def publish(self, mountpoint: str, frame: bytes) -> int:
        transport = self._transports.get(mountpoint)
        if transport is None:
            return 0

        now = time.monotonic()
        mp = self._mountpoints[mountpoint]
        interval = now - mp.last_seen
        mp.last_seen = now

        delivered = await transport.publish(frame)

        metrics.frames_published_total.labels(mountpoint=mountpoint).inc()
        metrics.bytes_published_total.labels(mountpoint=mountpoint).inc(len(frame))
        metrics.frame_size_bytes.labels(mountpoint=mountpoint).observe(len(frame))
        metrics.frames_delivered_total.labels(mountpoint=mountpoint).inc(delivered)
        metrics.frame_interval_seconds.labels(mountpoint=mountpoint).observe(interval)

        _observe_rtcm_quality(mountpoint, frame, self._arp_reference, self._frame_buffer, self._quality)

        return delivered

    @asynccontextmanager
    async def _metered_subscribe(self, mountpoint: str, transport: Transport) -> AsyncGenerator[TransportSubscriber]:
        metrics.rover_sessions_total.labels(mountpoint=mountpoint).inc()
        async with transport.subscribe() as sub:
            yield sub

    def subscribe(self, mountpoint: str) -> AbstractAsyncContextManager[TransportSubscriber]:
        transport = self._transports.get(mountpoint)
        if transport is None:
            raise KeyError(mountpoint)

        return self._metered_subscribe(mountpoint, transport)

    async def start(self) -> None:
        if self._expiry is not None:
            self._reaper_task = asyncio.create_task(self._reap_loop())

    async def stop(self) -> None:
        if self._reaper_task is not None:
            self._reaper_task.cancel()

            try:
                await self._reaper_task
            except asyncio.CancelledError:
                pass

            self._reaper_task = None

    async def _reap_loop(self) -> None:
        """Periodically unregister mountpoints that have gone silent."""
        while True:
            await asyncio.sleep(self._reap_interval)
            await self._reap()

    async def _reap(self) -> None:
        if self._expiry is None:
            return

        cutoff = time.monotonic() - self._expiry

        stale = [name for name, mp in self._mountpoints.items() if mp.last_seen < cutoff or mp.last_seen is None]

        for name in stale:
            await self.unregister(name)

        if stale:
            metrics.mountpoints_reaped_total.inc(len(stale))
