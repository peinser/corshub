"""Tests for the RTCM UDP egress server.

These drive the server's datagram handlers directly with a stub transport and a
real NTRIPCaster, so no sockets are involved.
"""

from __future__ import annotations

import asyncio

import pytest

from corshub.crypto import sign
from corshub.ntrip.v2.caster import NTRIPCaster
from corshub.rtcm.keys import SigningKey
from corshub.rtcm.tokens import issue_session_token
from corshub.rtcm.udp import ErrorCode
from corshub.rtcm.udp import RTCMDatagramServer
from corshub.rtcm.v1 import rtcm_udp_pb2 as pb


_SECRET = "udp-session-secret-at-least-32-bytes-long"
_ADDR = ("203.0.113.5", 40000)
_FRAME = b"\xd3\x00\x13rtcm-correction-bytes"


class FakeTransport:
    def __init__(self) -> None:
        self.sent: list[tuple[bytes, tuple[str, int]]] = []

    def sendto(self, data: bytes, addr: tuple[str, int]) -> None:
        self.sent.append((data, addr))

    def close(self) -> None:
        pass


@pytest.fixture
async def caster() -> NTRIPCaster:
    c = NTRIPCaster(expiry=None)
    await c.register(name="BASE1", mountpoint="BASE1", latitude=50.85, longitude=4.35)
    await c.register(name="BASE2", mountpoint="BASE2", latitude=52.37, longitude=4.90)
    return c


@pytest.fixture
def signing_key() -> SigningKey:
    return SigningKey.generate()


@pytest.fixture
async def server(caster: NTRIPCaster, signing_key: SigningKey):
    srv = RTCMDatagramServer(
        caster,
        token_secret=_SECRET,
        signing_key=signing_key,
        signing_enabled=True,
        session_ttl=30.0,
    )
    srv._transport = FakeTransport()  # type: ignore[assignment]
    yield srv
    await srv.stop()


def _transport(server: RTCMDatagramServer) -> FakeTransport:
    return server._transport  # type: ignore[return-value]


def _hello(mountpoint: str = "BASE1", token_mountpoint: str | None = None, position=None) -> pb.Datagram:
    token = issue_session_token(
        secret=_SECRET, username="rover1", mountpoint=token_mountpoint or mountpoint, ttl_seconds=60
    )
    hello = pb.Hello(token=token, mountpoint=mountpoint)
    if position is not None:
        hello.position.latitude, hello.position.longitude = position
    return pb.Datagram(version=1, hello=hello)


def _sent_messages(server: RTCMDatagramServer) -> list[pb.Datagram]:
    return [pb.Datagram.FromString(data) for data, _ in _transport(server).sent]


class TestHello:
    async def test_valid_hello_acks_with_session_and_kid(
        self, server: RTCMDatagramServer, signing_key: SigningKey
    ) -> None:
        await server._on_hello(_hello("BASE1"), _ADDR)
        acks = [m for m in _sent_messages(server) if m.WhichOneof("body") == "hello_ack"]
        assert len(acks) == 1
        ack = acks[0].hello_ack
        assert ack.mountpoint == "BASE1"
        assert ack.session_id != 0
        assert ack.signing_kid == signing_key.kid
        assert server.session_count == 1

    async def test_invalid_token_rejected(self, server: RTCMDatagramServer) -> None:
        bad = pb.Datagram(version=1, hello=pb.Hello(token="garbage", mountpoint="BASE1"))
        await server._on_hello(bad, _ADDR)
        errors = [m for m in _sent_messages(server) if m.WhichOneof("body") == "error"]
        assert errors and errors[0].error.code == ErrorCode.INVALID_TOKEN
        assert server.session_count == 0

    async def test_unauthorized_mountpoint_rejected(self, server: RTCMDatagramServer) -> None:
        # Token authorizes BASE1, but the rover asks for BASE2.
        await server._on_hello(_hello(mountpoint="BASE2", token_mountpoint="BASE1"), _ADDR)
        errors = [m for m in _sent_messages(server) if m.WhichOneof("body") == "error"]
        assert errors and errors[0].error.code == ErrorCode.UNAUTHORIZED_MOUNTPOINT

    async def test_wildcard_token_allows_any_mountpoint(self, server: RTCMDatagramServer) -> None:
        await server._on_hello(_hello(mountpoint="BASE2", token_mountpoint="*"), _ADDR)
        assert server.session_count == 1


class TestNearest:
    async def test_nearest_resolves_to_closest(self, server: RTCMDatagramServer) -> None:
        # Position near BASE1 (50.85, 4.35); token is wildcard.
        await server._on_hello(_hello(mountpoint="NEAREST", token_mountpoint="*", position=(50.84, 4.36)), _ADDR)
        ack = next(m for m in _sent_messages(server) if m.WhichOneof("body") == "hello_ack").hello_ack
        assert ack.mountpoint == "BASE1"

    async def test_nearest_without_position_errors(self, server: RTCMDatagramServer) -> None:
        await server._on_hello(_hello(mountpoint="NEAREST", token_mountpoint="*"), _ADDR)
        errors = [m for m in _sent_messages(server) if m.WhichOneof("body") == "error"]
        assert errors and errors[0].error.code == ErrorCode.MISSING_POSITION


class TestCorrectionStream:
    async def _open(self, server: RTCMDatagramServer, mountpoint: str = "BASE1", addr=_ADDR) -> int:
        await server._on_hello(_hello(mountpoint), addr)
        await asyncio.sleep(0)  # let the egress task enter the subscription
        ack = next(m for m in _sent_messages(server) if m.WhichOneof("body") == "hello_ack").hello_ack
        return ack.session_id

    async def test_published_frame_is_signed_and_delivered(
        self, server: RTCMDatagramServer, caster: NTRIPCaster, signing_key: SigningKey
    ) -> None:
        await self._open(server)
        _transport(server).sent.clear()

        await caster.publish("BASE1", _FRAME)
        await asyncio.sleep(0)

        corrections = [m for m in _sent_messages(server) if m.WhichOneof("body") == "correction"]
        assert len(corrections) == 1
        signed = corrections[0].correction
        # Signature verifies against the published key, and the payload carries our frame.
        assert sign.ed25519_verify(signed.payload, signed.signature, signing_key.public_key)
        frame = pb.CorrectionFrame.FromString(signed.payload)
        assert frame.rtcm == _FRAME
        assert frame.mountpoint == "BASE1"

    async def test_sign_once_fanout_identical_payload(self, server: RTCMDatagramServer, caster: NTRIPCaster) -> None:
        await self._open(server, addr=("203.0.113.5", 40000))
        await self._open(server, addr=("203.0.113.6", 40001))
        _transport(server).sent.clear()

        await caster.publish("BASE1", _FRAME)
        await asyncio.sleep(0)

        corrections = [m for m in _sent_messages(server) if m.WhichOneof("body") == "correction"]
        assert len(corrections) == 2
        # Same signed inner (incl. identical timestamp) fanned out to both sessions.
        assert corrections[0].correction.payload == corrections[1].correction.payload
        assert corrections[0].correction.signature == corrections[1].correction.signature
        assert {c.session_id for c in corrections}  # distinct outer envelopes

    async def test_signing_disabled_leaves_signature_empty(self, caster: NTRIPCaster) -> None:
        srv = RTCMDatagramServer(caster, token_secret=_SECRET, signing_enabled=False)
        srv._transport = FakeTransport()  # type: ignore[assignment]
        try:
            await srv._on_hello(_hello("BASE1"), _ADDR)
            await asyncio.sleep(0)
            _transport(srv).sent.clear()
            await caster.publish("BASE1", _FRAME)
            await asyncio.sleep(0)
            correction = next(m for m in _sent_messages(srv) if m.WhichOneof("body") == "correction").correction
            assert correction.signature == b""
            assert pb.CorrectionFrame.FromString(correction.payload).rtcm == _FRAME
        finally:
            await srv.stop()


class TestSessionManagement:
    async def test_keepalive_updates_return_address(self, server: RTCMDatagramServer) -> None:
        await server._on_hello(_hello("BASE1"), _ADDR)
        await asyncio.sleep(0)
        sid = next(m for m in _sent_messages(server) if m.WhichOneof("body") == "hello_ack").hello_ack.session_id

        # Same session_id arrives from a rebound NAT address.
        new_addr = ("203.0.113.9", 55555)
        server.handle_datagram(
            pb.Datagram(version=1, session_id=sid, keepalive=pb.KeepAlive()).SerializeToString(), new_addr
        )
        assert server._sessions[sid].addr == new_addr

    async def test_bye_tears_down_session(self, server: RTCMDatagramServer) -> None:
        await server._on_hello(_hello("BASE1"), _ADDR)
        await asyncio.sleep(0)
        sid = next(m for m in _sent_messages(server) if m.WhichOneof("body") == "hello_ack").hello_ack.session_id

        server.handle_datagram(
            pb.Datagram(version=1, session_id=sid, bye=pb.Bye(reason="done")).SerializeToString(), _ADDR
        )
        await asyncio.sleep(0)
        assert server.session_count == 0

    async def test_reaper_expires_idle_session(self, server: RTCMDatagramServer) -> None:
        await server._on_hello(_hello("BASE1"), _ADDR)
        await asyncio.sleep(0)
        assert server.session_count == 1

        # Force expiry in the past and run one reap pass directly.
        for session in server._sessions.values():
            session.expiry = 0.0
        import time as _t

        now = _t.monotonic()
        stale = [s for s in server._sessions.values() if s.expiry < now]
        for s in stale:
            await server._teardown(s)
        assert server.session_count == 0

    async def test_unknown_session_datagram_ignored(self, server: RTCMDatagramServer) -> None:
        # Should not raise and should send nothing.
        server.handle_datagram(
            pb.Datagram(version=1, session_id=999, keepalive=pb.KeepAlive()).SerializeToString(), _ADDR
        )
        assert _transport(server).sent == []

    async def test_undecodable_datagram_ignored(self, server: RTCMDatagramServer) -> None:
        server.handle_datagram(b"\xff\xff not protobuf \x00", _ADDR)
        assert _transport(server).sent == []


class TestHandoff:
    def _open_session_id(self, server: RTCMDatagramServer) -> int:
        return next(m for m in _sent_messages(server) if m.WhichOneof("body") == "hello_ack").hello_ack.session_id

    def _keepalive(self, session_id: int, lat: float, lon: float) -> bytes:
        ka = pb.KeepAlive(position=pb.GgaPosition(latitude=lat, longitude=lon))
        return pb.Datagram(version=1, session_id=session_id, keepalive=ka).SerializeToString()

    async def test_nearest_session_follows_rover(self, server: RTCMDatagramServer) -> None:
        # Open a NEAREST session near BASE1.
        await server._on_hello(_hello("NEAREST", token_mountpoint="*", position=(50.84, 4.36)), _ADDR)
        await asyncio.sleep(0)
        sid = self._open_session_id(server)
        assert server._sessions[sid].mountpoint == "BASE1"

        # A keepalive from near BASE2 re-points the session.
        server.handle_datagram(self._keepalive(sid, 52.37, 4.90), _ADDR)
        assert server._sessions[sid].mountpoint == "BASE2"
        await asyncio.sleep(0)

    async def test_concrete_session_does_not_handoff(self, server: RTCMDatagramServer) -> None:
        # Pinned to BASE1 (not dynamic).
        await server._on_hello(_hello("BASE1"), _ADDR)
        await asyncio.sleep(0)
        sid = self._open_session_id(server)

        server.handle_datagram(self._keepalive(sid, 52.37, 4.90), _ADDR)
        assert server._sessions[sid].mountpoint == "BASE1"


class TestMaxDatagram:
    async def test_oversize_datagram_dropped_and_counted(self, caster: NTRIPCaster) -> None:
        import corshub.metrics as m

        srv = RTCMDatagramServer(caster, token_secret=_SECRET, signing_enabled=False, max_datagram=10)
        srv._transport = FakeTransport()  # type: ignore[assignment]
        try:
            await srv._on_hello(_hello("BASE1"), _ADDR)
            await asyncio.sleep(0)
            _transport(srv).sent.clear()

            before = m.rtcm_udp_oversize_dropped_total.labels(mountpoint="BASE1")._value.get()
            await caster.publish("BASE1", _FRAME)  # far larger than 10 bytes
            await asyncio.sleep(0)

            corrections = [msg for msg in _sent_messages(srv) if msg.WhichOneof("body") == "correction"]
            assert corrections == []
            after = m.rtcm_udp_oversize_dropped_total.labels(mountpoint="BASE1")._value.get()
            assert after - before == 1
        finally:
            await srv.stop()


class _ClientProtocol(asyncio.DatagramProtocol):
    def __init__(self, queue: asyncio.Queue[bytes]) -> None:
        self._queue = queue

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self._queue.put_nowait(data)


class TestRealSocket:
    async def test_start_stop_and_roundtrip(self, caster: NTRIPCaster, signing_key: SigningKey) -> None:
        srv = RTCMDatagramServer(
            caster,
            host="127.0.0.1",
            port=0,  # OS-assigned
            token_secret=_SECRET,
            signing_key=signing_key,
            signing_enabled=True,
        )
        await srv.start()
        try:
            host, port = srv.bound_address  # type: ignore[misc]
            loop = asyncio.get_running_loop()
            received: asyncio.Queue[bytes] = asyncio.Queue()
            client, _ = await loop.create_datagram_endpoint(
                lambda: _ClientProtocol(received),
                remote_addr=(host, port),
            )
            try:
                client.sendto(_hello("BASE1").SerializeToString())
                data = await asyncio.wait_for(received.get(), timeout=2.0)
                msg = pb.Datagram.FromString(data)
                assert msg.WhichOneof("body") == "hello_ack"
                assert msg.hello_ack.mountpoint == "BASE1"
            finally:
                client.close()
        finally:
            await srv.stop()
        assert srv.bound_address is None  # closed
