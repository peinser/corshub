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
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from dataclasses import field
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from corshub.ntrip.v2.transport import Transport
    from corshub.ntrip.v2.transport import TransportSubscriber


_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_-]{1,100}$")


@dataclass
class Mountpoint:
    """Metadata for a single NTRIP mountpoint.

    Fields map directly to the STR record in the NTRIP source table
    (RTCM 10410.1 §4.1) plus the credential used to authenticate the
    base station that pushes corrections to this mountpoint.

    Attributes:
        name: Mountpoint identifier used in the URL path (e.g. ``BASE-1``).
            Alphanumeric, hyphens, and underscores, 1–100 characters (per spec).
        identifier: Human-readable label for the source table STR record,
            typically describing the physical location.
        username: Username the base station must supply via HTTP Basic auth.
            Must be non-empty.
        password: Password the base station must supply via HTTP Basic auth.
            Must be non-empty.
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
    identifier: str
    username: str
    password: str
    format: str
    country: str
    latitude: float
    longitude: float
    format_detail: str = ""
    carrier: int = 0
    nav_system: str = ""
    network: str = ""
    nmea: bool = False
    solution: int = 0
    generator: str = ""
    compression: str = ""
    auth: str = "B"
    fee: str = "N"
    bitrate: int = 0
    last_seen: float = field(default_factory=time.monotonic, compare=False)

    def __post_init__(self) -> None:
        if not _IDENTIFIER_RE.match(self.identifier):
            raise ValueError(
                f"Mountpoint identifier {self.identifier!r} is invalid: must be 1–100 characters, alphanumeric, underscore, or hyphen."
            )
        if not self.username:
            raise ValueError("Mountpoint username must be non-empty.")
        if not self.password:
            raise ValueError("Mountpoint password must be non-empty.")
        if not re.match(r"^[A-Z]{3}$", self.country):
            raise ValueError(
                f"Country {self.country!r} is invalid: must be an ISO 3166-1 alpha-3 code (e.g. 'BEL')."
            )
        if not -90.0 <= self.latitude <= 90.0:
            raise ValueError(f"Latitude {self.latitude} is out of range [-90, 90].")
        if not -180.0 <= self.longitude <= 180.0:
            raise ValueError(f"Longitude {self.longitude} is out of range [-180, 180].")


class Caster(ABC):

    @abstractmethod
    def register(self, mountpoint: Mountpoint) -> None:
        """Add *mountpoint* to the registry and open its transport.

        Raises ValueError if a mountpoint with the same name already exists.
        """

    @abstractmethod
    def unregister(self, name: str) -> None:
        """Remove *name* from the registry and close its transport.

        Raises KeyError if *name* is not registered.
        """

    @property
    @abstractmethod
    def mountpoints(self) -> dict[str, Mountpoint]:
        """Read-only view of currently registered mountpoints."""

    @abstractmethod
    async def authenticate(self, username: str, password: str) -> bool:
        """Return True if *password* is valid for mountpoint's *username*."""

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
    _METADATA_FIELDS = {
        "name", "format", "format_detail", "carrier", "nav_system", "network",
        "country", "latitude", "longitude", "nmea", "solution", "generator",
        "compression", "auth", "fee", "bitrate",
    }

    def __init__(
        self,
        transport_factory: type[Transport] | None = None,
        expiry: float | None = 60.0,
        reap_interval: float = 10.0,
    ) -> None:
        from corshub.ntrip.v2.transport import QueueTransport

        self._transport_factory: type[Transport] = transport_factory or QueueTransport
        self._expiry = expiry
        self._reap_interval = reap_interval
        self._mountpoints: dict[str, Mountpoint] = {}
        self._transports: dict[str, Transport] = {}
        self._reaper_task: asyncio.Task[None] | None = None

    # ── Registry ──────────────────────────────────────────────────────────────

    async def register(self, identifier: str, **kwargs) -> Mountpoint:
        if identifier in self._mountpoints:
            instance = self._mountpoints[identifier]
            # Merge incoming metadata into the existing instance.  Credentials
            # (username, password) and last_seen are intentionally excluded —
            # those are set by the operator and must not be overwritten by
            # whatever the base station reports in its Ntrip-STR header.
            for key, value in kwargs.items():
                if key in NTRIPCaster._METADATA_FIELDS and value is not None:
                    setattr(instance, key, value)

            return instance

        instance = Mountpoint(
            identifier=identifier,
            **kwargs,
        )

        self._mountpoints[identifier] = instance
        self._transports[identifier] = self._transport_factory()

        return instance

    async def unregister(self, identifier: str) -> None:
        if identifier not in self._mountpoints:
            raise KeyError(identifier)

        del self._mountpoints[identifier]
        del self._transports[identifier]

    @property
    def mountpoints(self) -> dict[str, Mountpoint]:
        return self._mountpoints

    # ── Auth ──────────────────────────────────────────────────────────────────

    def authenticate(self, username: str, password: str) -> bool:
        return True  # TODO Implement

    # ── Pub / sub ─────────────────────────────────────────────────────────────

    async def publish(self, mountpoint: Mountpoint, frame: bytes) -> int:
        transport = self._transports.get(mountpoint.identifier)
        if transport is None:
            return 0

        mp = self._mountpoints[mountpoint.identifier]
        mp.last_seen = time.monotonic()

        return await transport.publish(frame)

    def subscribe(self, name: str) -> AbstractAsyncContextManager[TransportSubscriber]:
        transport = self._transports.get(name)
        if transport is None:
            raise KeyError(name)
        return transport.subscribe()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

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

    # ── Reaper ────────────────────────────────────────────────────────────────

    async def _reap_loop(self) -> None:
        """Periodically unregister mountpoints that have gone silent."""
        while True:
            await asyncio.sleep(self._reap_interval)
            self._reap()

    def _reap(self) -> None:
        if self._expiry is None:
            return
        cutoff = time.monotonic() - self._expiry
        stale = [name for name, mp in self._mountpoints.items() if mp.last_seen < cutoff]
        for name in stale:
            self.unregister(name)
