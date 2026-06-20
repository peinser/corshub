"""
UDP egress for direct-to-rover RTCM corrections.

A second correction egress alongside the NTRIP/HTTP path. Rovers authenticate
once over HTTPS (see the bootstrap endpoint), receive a short-lived session token,
and open a UDP session with a ``Hello``. The server then streams signed
``CorrectionFrame``s pulled from the caster's per-mountpoint transport.

Key properties (see docs/architecture/rtcm-udp.md):
  * One listener for all mountpoints; the mountpoint is chosen per session.
  * Sessions are keyed by a 64-bit ``session_id`` carried in every datagram, so a
    session survives carrier-grade NAT source-port rebinding.
  * Each correction frame is signed once per mountpoint and fanned out unchanged
    to every session on it; only the outer ``Datagram`` (session_id, seq) differs.
  * Inert unless explicitly started; signing is independently optional.

This module is transport/domain logic. The HTTP endpoints and Sanic lifecycle
wiring live in ``corshub.services.v1.rtcm``.
"""

from __future__ import annotations

import asyncio
import secrets
import time

from dataclasses import dataclass
from dataclasses import field
from typing import TYPE_CHECKING

import jwt

import corshub.metrics as metrics

from corshub.logging import logger
from corshub.ntrip.v2.headers import haversine
from corshub.rtcm.tokens import verify_session_token
from corshub.rtcm.v1 import rtcm_udp_pb2 as pb


if TYPE_CHECKING:
    from collections.abc import Coroutine

    from corshub.ntrip.v2.caster import NTRIPCaster
    from corshub.ntrip.v2.transport import TransportSubscriber
    from corshub.rtcm.keys import SigningKey


PROTOCOL_VERSION = 1

# Sentinel mountpoint scopes that authorize any concrete mountpoint.
_WILDCARD_SCOPES = frozenset({"*", "NEAREST"})


class ErrorCode:
    INVALID_TOKEN = 1
    UNAUTHORIZED_MOUNTPOINT = 2
    MOUNTPOINT_UNAVAILABLE = 3
    MISSING_POSITION = 4
    INTERNAL = 5


@dataclass
class Session:
    """A live UDP correction session."""

    session_id: int
    mountpoint: str
    addr: tuple[str, int]
    expiry: float  # monotonic deadline
    seq: int = 0
    task: asyncio.Task[None] | None = field(default=None, compare=False)

    def next_seq(self) -> int:
        seq = self.seq
        self.seq = (self.seq + 1) & 0xFFFFFFFF
        return seq


class _DatagramProtocol(asyncio.DatagramProtocol):
    def __init__(self, server: RTCMDatagramServer) -> None:
        self._server = server

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self._server._transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self._server.handle_datagram(data, addr)

    def error_received(self, exc: Exception) -> None:
        logger.debug("RTCM UDP error_received: %s", exc)


class RTCMDatagramServer:
    """asyncio UDP server that streams signed RTCM to authenticated rovers.

    The server is constructed with all of its configuration so it is fully
    testable without environment access or real sockets (drive ``handle_datagram``
    directly and inspect a stub transport's ``sendto``).
    """

    def __init__(
        self,
        caster: NTRIPCaster,
        *,
        host: str = "0.0.0.0",
        port: int = 5009,
        token_secret: str,
        signing_key: SigningKey | None = None,
        signing_enabled: bool = False,
        session_ttl: float = 30.0,
        keepalive_interval: int = 10,
        reap_interval: float = 5.0,
    ) -> None:
        self._caster = caster
        self._host = host
        self._port = port
        self._token_secret = token_secret
        self._signing_key = signing_key
        self._signing_enabled = signing_enabled
        self._session_ttl = session_ttl
        self._keepalive_interval = keepalive_interval
        self._reap_interval = reap_interval

        self._transport: asyncio.DatagramTransport | None = None
        self._sessions: dict[int, Session] = {}
        self._reaper_task: asyncio.Task[None] | None = None
        # Fire-and-forget dispatch tasks; kept referenced so they are not garbage
        # collected mid-flight, and discarded on completion.
        self._bg_tasks: set[asyncio.Task[None]] = set()
        # Per-mountpoint cache of the most recently signed frame, enabling
        # sign-once/fan-out: {mountpoint: (frame_obj, SignedCorrection)}.
        self._signed_cache: dict[str, tuple[bytes, pb.SignedCorrection]] = {}

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        self._transport, _ = await loop.create_datagram_endpoint(
            lambda: _DatagramProtocol(self),
            local_addr=(self._host, self._port),
        )
        self._reaper_task = asyncio.create_task(self._reap_loop())
        logger.info("RTCM UDP egress listening on %s:%d (signing=%s)", self._host, self._port, self._signing_enabled)

    async def stop(self) -> None:
        if self._reaper_task is not None:
            self._reaper_task.cancel()
            try:
                await self._reaper_task
            except asyncio.CancelledError:
                pass
            self._reaper_task = None

        for session in list(self._sessions.values()):
            await self._teardown(session)

        for task in list(self._bg_tasks):
            task.cancel()

        if self._transport is not None:
            self._transport.close()
            self._transport = None

    @property
    def session_count(self) -> int:
        return len(self._sessions)

    @property
    def bound_address(self) -> tuple[str, int] | None:
        """The actual ``(host, port)`` the listener is bound to, or None."""
        if self._transport is None:
            return None
        sock = self._transport.get_extra_info("socket")
        return sock.getsockname() if sock is not None else None

    # -- inbound dispatch --------------------------------------------------

    def handle_datagram(self, data: bytes, addr: tuple[str, int]) -> None:
        """Parse and dispatch a single inbound datagram (called by the loop)."""
        try:
            datagram = pb.Datagram.FromString(data)
        except Exception:
            logger.debug("RTCM UDP: undecodable datagram from %s (%d bytes)", addr, len(data))
            return

        body = datagram.WhichOneof("body")
        if body == "hello":
            self._spawn(self._on_hello(datagram, addr))
            return

        # All other datagrams must reference an established session.
        session = self._sessions.get(datagram.session_id)
        if session is None:
            return  # unknown or expired session; ignore

        # Any authenticated datagram refreshes liveness and the return address
        # (the latter survives CGNAT port rebinding).
        session.addr = addr
        session.expiry = self._deadline()

        if body == "keepalive":
            self._on_keepalive(session, datagram.keepalive)
        elif body == "switch_mountpoint":
            self._spawn(self._on_switch(session, datagram.switch_mountpoint))
        elif body == "bye":
            self._spawn(self._teardown(session))

    def _spawn(self, coro: Coroutine[object, object, None]) -> None:
        task = asyncio.create_task(coro)
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    async def _on_hello(self, datagram: pb.Datagram, addr: tuple[str, int]) -> None:
        hello = datagram.hello
        try:
            claims = verify_session_token(hello.token, self._token_secret)
        except jwt.InvalidTokenError:
            metrics.rtcm_udp_token_rejected_total.inc()
            self._send_error(addr, ErrorCode.INVALID_TOKEN, "Invalid or expired token.")
            return

        token_mp: str = claims["mountpoint"]
        position = (hello.position.latitude, hello.position.longitude) if hello.HasField("position") else None

        try:
            mountpoint = self._resolve_mountpoint(token_mp, hello.mountpoint, position)
        except _AuthorizationError as exc:
            metrics.rtcm_udp_hello_total.labels(result="rejected").inc()
            self._send_error(addr, exc.code, exc.message)
            return

        session = Session(
            session_id=secrets.randbits(64),
            mountpoint=mountpoint,
            addr=addr,
            expiry=self._deadline(),
        )
        self._sessions[session.session_id] = session
        metrics.rtcm_udp_hello_total.labels(result="accepted").inc()
        metrics.rtcm_udp_sessions.inc()

        self._send(
            addr,
            pb.Datagram(
                version=PROTOCOL_VERSION,
                session_id=session.session_id,
                hello_ack=pb.HelloAck(
                    session_id=session.session_id,
                    mountpoint=mountpoint,
                    signing_kid=self._signing_key.kid if (self._signing_key and self._signing_enabled) else "",
                    keepalive_interval_s=self._keepalive_interval,
                    session_ttl_s=int(self._session_ttl),
                ),
            ),
        )

        session.task = asyncio.create_task(self._egress_loop(session))

    def _on_keepalive(self, session: Session, keepalive: pb.KeepAlive) -> None:
        # Position updates feed mountpoint handoff for NEAREST sessions (the
        # re-subscription itself is future work); for now record it on the caster.
        if keepalive.HasField("position"):
            self._caster.set_rover_position(
                session.mountpoint,
                str(session.session_id),
                keepalive.position.latitude,
                keepalive.position.longitude,
            )

    async def _on_switch(self, session: Session, switch: pb.SwitchMountpoint) -> None:
        position = (switch.position.latitude, switch.position.longitude) if switch.HasField("position") else None
        try:
            # A switch is re-authorized against the same token scope as the session's
            # current mountpoint (sessions opened under a wildcard scope may roam).
            scope = session.mountpoint if session.mountpoint not in self._caster.mountpoints else "*"
            mountpoint = self._resolve_mountpoint(scope, switch.mountpoint, position)
        except _AuthorizationError as exc:
            self._send_error(session.addr, exc.code, exc.message)
            return

        if mountpoint == session.mountpoint:
            return

        # Restart the egress loop against the new mountpoint.
        old_task = session.task
        session.mountpoint = mountpoint
        session.task = asyncio.create_task(self._egress_loop(session))
        if old_task is not None:
            old_task.cancel()

    # -- egress ------------------------------------------------------------

    async def _egress_loop(self, session: Session) -> None:
        """Stream signed frames to one session until the subscription ends."""
        mountpoint = session.mountpoint
        try:
            async with self._caster.subscribe(mountpoint) as sub:
                await self._pump(session, sub)
        except KeyError:
            self._send_error(session.addr, ErrorCode.MOUNTPOINT_UNAVAILABLE, f"Mountpoint {mountpoint!r} unavailable.")
            await self._teardown(session)
        except asyncio.CancelledError:
            raise

    async def _pump(self, session: Session, sub: TransportSubscriber) -> None:
        while (frame := await sub.get()) is not None:
            # The subscription may have been re-pointed by a switch; bail if so.
            if session.task is not asyncio.current_task():
                return
            signed = self._sign_frame(session.mountpoint, frame)
            datagram = pb.Datagram(version=PROTOCOL_VERSION, session_id=session.session_id, seq=session.next_seq())
            datagram.correction.CopyFrom(signed)
            payload = datagram.SerializeToString()
            self._raw_send(session.addr, payload)
            metrics.rtcm_udp_datagrams_sent_total.labels(mountpoint=session.mountpoint).inc()
            metrics.rtcm_udp_bytes_sent_total.labels(mountpoint=session.mountpoint).inc(len(payload))

    def _sign_frame(self, mountpoint: str, frame: bytes) -> pb.SignedCorrection:
        """Build (and cache) the signed correction for *frame* on *mountpoint*.

        All sessions on a mountpoint receive the same `bytes` object from the
        transport queue, so an identity check yields sign-once/fan-out in the
        common (lock-step) case; a lagging session simply re-signs.
        """
        cached = self._signed_cache.get(mountpoint)
        if cached is not None and cached[0] is frame:
            return cached[1]

        correction = pb.CorrectionFrame(
            timestamp_ms=int(time.time() * 1000),
            mountpoint=mountpoint,
            rtcm=frame,
        )
        payload = correction.SerializeToString()
        signature = self._signing_key.sign(payload) if (self._signing_key and self._signing_enabled) else b""
        signed = pb.SignedCorrection(payload=payload, signature=signature)
        if self._signing_enabled:
            metrics.rtcm_udp_frames_signed_total.labels(mountpoint=mountpoint).inc()

        self._signed_cache[mountpoint] = (frame, signed)
        return signed

    # -- authorization / resolution ---------------------------------------

    def _resolve_mountpoint(
        self,
        token_scope: str,
        requested: str,
        position: tuple[float, float] | None,
    ) -> str:
        """Authorize *requested* against *token_scope* and resolve NEAREST.

        Raises _AuthorizationError with an error code on any failure.
        """
        wildcard = token_scope in _WILDCARD_SCOPES

        if requested == "NEAREST":
            if position is None:
                raise _AuthorizationError(ErrorCode.MISSING_POSITION, "NEAREST requires a position.")
            resolved = self._nearest(position)
            if resolved is None:
                raise _AuthorizationError(ErrorCode.MOUNTPOINT_UNAVAILABLE, "No mountpoint in range.")
            if not wildcard and resolved != token_scope:
                raise _AuthorizationError(
                    ErrorCode.UNAUTHORIZED_MOUNTPOINT, "Not authorized for the nearest mountpoint."
                )
            return resolved

        if not wildcard and requested != token_scope:
            raise _AuthorizationError(ErrorCode.UNAUTHORIZED_MOUNTPOINT, f"Not authorized for {requested!r}.")

        if requested not in self._caster.mountpoints:
            raise _AuthorizationError(ErrorCode.MOUNTPOINT_UNAVAILABLE, f"Mountpoint {requested!r} unavailable.")

        return requested

    def _nearest(self, position: tuple[float, float]) -> str | None:
        rover_lat, rover_lon = position
        best_id: str | None = None
        best_dist = float("inf")
        for mp_id, mp in self._caster.mountpoints.items():
            if mp.latitude is None or mp.longitude is None:
                continue
            dist = haversine(mp.latitude, mp.longitude, rover_lat, rover_lon)
            if mp.mask > 0.0 and dist > mp.mask:
                continue
            if dist < best_dist:
                best_dist, best_id = dist, mp_id
        return best_id

    # -- helpers -----------------------------------------------------------

    def _deadline(self) -> float:
        return time.monotonic() + self._session_ttl

    def _send(self, addr: tuple[str, int], datagram: pb.Datagram) -> None:
        self._raw_send(addr, datagram.SerializeToString())

    def _raw_send(self, addr: tuple[str, int], payload: bytes) -> None:
        if self._transport is not None:
            self._transport.sendto(payload, addr)

    def _send_error(self, addr: tuple[str, int], code: int, message: str) -> None:
        self._send(addr, pb.Datagram(version=PROTOCOL_VERSION, error=pb.Error(code=code, message=message)))

    async def _teardown(self, session: Session) -> None:
        if self._sessions.pop(session.session_id, None) is None:
            return  # already torn down; keep the sessions gauge from drifting
        metrics.rtcm_udp_sessions.dec()
        self._caster.clear_rover_position(session.mountpoint, str(session.session_id))
        task = session.task
        session.task = None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _reap_loop(self) -> None:
        while True:
            await asyncio.sleep(self._reap_interval)
            now = time.monotonic()
            stale = [s for s in self._sessions.values() if s.expiry < now]
            for session in stale:
                await self._teardown(session)
            if stale:
                metrics.rtcm_udp_sessions_reaped_total.inc(len(stale))


class _AuthorizationError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
