"""
End-to-end integration tests using GNSSNTRIPClient (pygnssutils).

Unlike the other route tests that exercise Sanic via its ASGI test client,
these tests spin up a real TCP server so that GNSSNTRIPClient can connect
over an actual socket — the same path used in production.

Three test classes:

  TestSourceTable      — client fetches the NTRIP source table (mountpoint='')
  TestRoverConnection  — client connects to GET /<mountpoint> and negotiates 200
  TestRtcmDelivery     — full end-to-end: base station pushes RTCM,
                         GNSSNTRIPClient receives and parses it

Server lifecycle: one Sanic instance is started per module in a background
thread (scope="module").  Each test creates its own GNSSNTRIPClient instance
so that state does not leak between test cases.
"""

from __future__ import annotations

import asyncio
import base64
import socket
import threading
from queue import Queue
from unittest.mock import AsyncMock

import pytest
from pygnssutils.gnssntripclient import GNSSNTRIPClient
from sanic import Sanic

from corshub.ntrip.v2.caster import NTRIPCaster
from corshub.services.v1.ntrip import service as ntrip_service


HOST = "127.0.0.1"
MOUNTPOINT = "BASE1"
USER = "test"
PASSWORD = "test"


def _crc24q(data: bytes) -> bytes:
    """Compute the CRC-24Q checksum used by RTCM 10410.1."""
    crc = 0
    for byte in data:
        crc ^= byte << 16
        for _ in range(8):
            crc <<= 1
            if crc & 0x1000000:
                crc ^= 0x1864CFB
    return (crc & 0xFFFFFF).to_bytes(3, "big")


def _rtcm1005(station_id: int = 0) -> bytes:
    """Build a minimal valid RTCM 1005 frame (stationary ARP, GPS only).

    All coordinate fields are zero.  The frame is 25 bytes total:
    1 preamble + 2 length + 19 message body + 3 CRC.
    """
    body = bytearray(19)  # 151 message bits + 1 pad bit → 19 bytes

    def _set(buf: bytearray, offset: int, value: int, nbits: int) -> None:
        """Pack *value* into *nbits* bits starting at bit *offset* (MSB first)."""
        for i in range(nbits):
            if (value >> (nbits - 1 - i)) & 1:
                idx, remainder = divmod(offset + i, 8)
                buf[idx] |= 1 << (7 - remainder)

    _set(body, 0, 1005, 12)         # Message number (RTCM 1005)
    _set(body, 12, station_id, 12)  # Reference station ID
    # Bits 24-29: ITRF realisation year = 0 (already zero)
    _set(body, 30, 1, 1)            # GPS indicator = 1
    # Remaining fields (GLONASS/Galileo indicators, ARP coords) stay 0

    header = bytes([0xD3, 0x00, 0x13])  # D3 preamble + 10-bit length = 19
    frame = header + bytes(body)
    return frame + _crc24q(frame)


def _start_client(
    port: int,
    mountpoint: str = "",
    output: object = None,
) -> GNSSNTRIPClient:
    """Create a GNSSNTRIPClient and start it against the test server.

    *retries=0* prevents the client from reconnecting after the first attempt
    so tests terminate quickly on failure.  *verbosity=-1* suppresses log noise.
    """
    client = GNSSNTRIPClient(retries=0, timeout=3, verbosity=-1)
    client.run(
        server=HOST,
        port=port,
        https=0,
        mountpoint=mountpoint,
        ntripuser=USER,
        ntrippassword=PASSWORD,
        version="2.0",
        ggainterval=-1,
        output=output,
    )
    return client


async def _await_status(client: GNSSNTRIPClient, timeout: float = 5.0) -> None:
    """Wait until the client has received and parsed response headers."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not client.status:
        await asyncio.sleep(0.05)
        if loop.time() > deadline:
            pytest.fail("GNSSNTRIPClient: no response headers received within timeout")


async def _await_done(client: GNSSNTRIPClient, timeout: float = 5.0) -> None:
    """Wait until the client has disconnected (source table retrieved or error)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while client.connected:
        await asyncio.sleep(0.05)
        if loop.time() > deadline:
            pytest.fail("GNSSNTRIPClient: did not finish within timeout")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server_port() -> int:  # type: ignore[return]
    """Start a real Sanic TCP server in a background thread.

    Yields the port number once the server is accepting connections.
    The server is torn down when all tests in the module have finished.
    """
    port = _free_port()
    ready = threading.Event()
    stop = threading.Event()

    def _target() -> None:
        async def _serve() -> None:
            blueprint = ntrip_service.blueprint()
            caster = NTRIPCaster()
            caster.authenticate_base_station = AsyncMock(return_value=True)
            caster.authenticate_rover = AsyncMock(return_value=True)
            await caster.register(
                name=MOUNTPOINT,
                identifier=MOUNTPOINT,
                format="RTCM 3.3",
                country="BEL",
                latitude=50.8503,
                longitude=4.3517,
            )

            app = Sanic(f"live_{port}")
            app.blueprint(blueprint)
            app.ctx.ntrip_caster = caster  # Set directly; before_server_start doesn't fire for create_server

            server = await app.create_server(
                host=HOST, port=port, return_asyncio_server=True
            )
            await server.startup()
            ready.set()

            while not stop.is_set():
                await asyncio.sleep(0.05)

            server.close()

        loop = asyncio.new_event_loop()
        loop.run_until_complete(_serve())
        loop.close()

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    assert ready.wait(timeout=10), "Test server did not start within 10 s"

    yield port

    stop.set()
    thread.join(timeout=5)


class TestSourceTable:
    """GNSSNTRIPClient fetches the NTRIP source table (empty mountpoint)."""

    async def test_response_is_ok(self, server_port: int) -> None:
        client = _start_client(server_port, mountpoint="")
        await _await_done(client)
        assert client.responseok

    async def test_content_type_is_sourcetable(self, server_port: int) -> None:
        client = _start_client(server_port, mountpoint="")
        await _await_done(client)
        assert client.is_sourcetable

    async def test_registered_mountpoint_present(self, server_port: int) -> None:
        client = _start_client(server_port, mountpoint="")
        await _await_done(client)
        names = [row[0] for row in client.settings.get("sourcetable", []) if row]
        assert MOUNTPOINT in names


class TestRoverConnection:
    """GNSSNTRIPClient connection behaviour for valid and invalid mountpoints."""

    async def test_unknown_mountpoint_returns_404(self, server_port: int) -> None:
        # 404 is a complete (non-streaming) response, so headers arrive immediately.
        client = _start_client(server_port, mountpoint="DOES_NOT_EXIST")
        await _await_done(client)
        assert not client.responseok
        assert client.status.get("code") == 404


class TestRtcmDelivery:
    """Full end-to-end: base station pushes RTCM → caster fans out → GNSSNTRIPClient.

    Implementation note
    -------------------
    Sanic's ResponseStream buffers headers until the first ``stream.write()`` call.
    GNSSNTRIPClient's outer ``recv`` loop also consumes the first HTTP chunk before
    handing the socket to ``_parse_ntrip_data``.  To work around both behaviours
    the base station connects first and emits frames continuously; by the time the
    rover connects, frames are already flowing and *subsequent* frames are parsed
    correctly by UBXReader.
    """

    async def _run_base_station(
        self,
        server_port: int,
        frame: bytes,
        stop: asyncio.Event,
        interval: float = 0.05,
    ) -> None:
        """Continuously push *frame* via HTTP PUT until *stop* is set."""
        auth = base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()
        put_headers = (
            f"PUT /{MOUNTPOINT} HTTP/1.1\r\n"
            f"Host: {HOST}:{server_port}\r\n"
            "Ntrip-Version: Ntrip/2.0\r\n"
            f"Authorization: Basic {auth}\r\n"
            "Content-Type: gnss/data\r\n"
            "Transfer-Encoding: chunked\r\n"
            "\r\n"
        ).encode()
        chunk = f"{len(frame):x}\r\n".encode() + frame + b"\r\n"

        _, writer = await asyncio.open_connection(HOST, server_port)
        writer.write(put_headers)
        await writer.drain()

        while not stop.is_set():
            writer.write(chunk)
            await writer.drain()
            await asyncio.sleep(interval)

        writer.write(b"0\r\n\r\n")
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    async def test_frame_received_by_rover(self, server_port: int) -> None:
        output: Queue = Queue()
        frame = _rtcm1005()
        stop = asyncio.Event()

        # Start the base station so frames are already flowing when the rover connects.
        bs_task = asyncio.create_task(
            self._run_base_station(server_port, frame, stop)
        )
        await asyncio.sleep(0.3)  # Give the base station time to register and start sending.

        client = _start_client(server_port, mountpoint=MOUNTPOINT, output=output)

        # Wait for at least one frame to reach the rover's output queue.
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 5.0
        while output.empty():
            await asyncio.sleep(0.05)
            if loop.time() > deadline:
                stop.set()
                client.stop()
                await bs_task
                pytest.fail("No RTCM frame received by GNSSNTRIPClient within timeout")

        stop.set()
        client.stop()
        await bs_task

        raw, _ = output.get_nowait()
        assert raw == frame, f"Received unexpected bytes: {raw!r}"

    async def test_rover_connection_is_200_gnss_data(self, server_port: int) -> None:
        """Verify the rover sees 200 OK / gnss/data once real data is flowing."""
        output: Queue = Queue()
        frame = _rtcm1005()
        stop = asyncio.Event()

        bs_task = asyncio.create_task(
            self._run_base_station(server_port, frame, stop)
        )
        await asyncio.sleep(0.3)

        client = _start_client(server_port, mountpoint=MOUNTPOINT, output=output)

        loop = asyncio.get_running_loop()
        deadline = loop.time() + 5.0
        while output.empty():
            await asyncio.sleep(0.05)
            if loop.time() > deadline:
                stop.set()
                client.stop()
                await bs_task
                pytest.fail("Rover did not receive any RTCM frame within timeout")

        stop.set()
        client.stop()
        await bs_task

        assert client.responseok
        assert client.is_gnssdata
