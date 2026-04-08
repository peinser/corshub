"""
Tests for Transport implementations.

Every concrete Transport must satisfy this contract:

    open(mp)               → registers mountpoint, raises ValueError if duplicate
    close(mp)              → deregisters, raises KeyError if unknown,
                             signals all active subscribers to stop
    publish(mp, frame)     → delivers to all subscribers, returns count,
                             raises KeyError if mountpoint not open
    subscribe(mp)          → async context manager yielding a TransportSubscriber,
                             raises KeyError if not open, cleanup guaranteed on exit

Usage pattern for consumers:
    async with transport.subscribe(mountpoint) as sub:
        while (frame := await sub.get()) is not None:
            ...  # sub.get() returns None when the transport is closed

Currently tested implementation: QueueTransport
    corshub.ntrip.v2.transport.QueueTransport
"""

from __future__ import annotations

import asyncio

import pytest

from corshub.ntrip.v2.transport import QueueTransport


# ── Shared fixture ────────────────────────────────────────────────────────────


@pytest.fixture
async def transport() -> QueueTransport:
    t = QueueTransport()
    await t.open("BASE1")
    return t


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _collect_one(transport: QueueTransport, mountpoint: str) -> list[bytes]:
    """Subscribe, collect exactly one frame, then exit the context manager."""
    received: list[bytes] = []
    async with transport.subscribe(mountpoint) as sub:
        frame = await sub.get()
        if frame is not None:
            received.append(frame)
    return received


# ── open / close ──────────────────────────────────────────────────────────────


class TestLifecycle:
    async def test_open_registers_mountpoint(self) -> None:
        t = QueueTransport()
        await t.open("BASE1")
        assert await t.publish("BASE1", b"\xd3\x00\x00") == 0

    async def test_open_duplicate_raises_value_error(self, transport: QueueTransport) -> None:
        with pytest.raises(ValueError, match="BASE1"):
            await transport.open("BASE1")

    async def test_close_removes_mountpoint(self, transport: QueueTransport) -> None:
        await transport.close("BASE1")
        with pytest.raises(KeyError):
            await transport.publish("BASE1", b"\xd3\x00\x00")

    async def test_close_unknown_raises_key_error(self) -> None:
        with pytest.raises(KeyError):
            await QueueTransport().close("GHOST")

    async def test_close_signals_active_subscriber(self, transport: QueueTransport) -> None:
        frames: list[bytes] = []

        async def consumer() -> None:
            async with transport.subscribe("BASE1") as sub:
                while (frame := await sub.get()) is not None:
                    frames.append(frame)  # pragma: no cover

        task = asyncio.create_task(consumer())
        await asyncio.sleep(0)
        await transport.close("BASE1")
        await task  # must terminate, not hang

        assert frames == []


# ── publish ───────────────────────────────────────────────────────────────────


class TestPublish:
    async def test_no_subscribers_returns_zero(self, transport: QueueTransport) -> None:
        assert await transport.publish("BASE1", b"\xd3\x00\x00") == 0

    async def test_unknown_mountpoint_returns_zero(self, transport: QueueTransport) -> None:
        assert await transport.publish("GHOST", b"\xd3\x00\x00") == 0

    async def test_returns_subscriber_count(self, transport: QueueTransport) -> None:
        received: list[bytes] = []

        async def consumer() -> None:
            async with transport.subscribe("BASE1") as sub:
                frame = await sub.get()
                if frame is not None:
                    received.append(frame)

        task = asyncio.create_task(consumer())
        await asyncio.sleep(0)
        count = await transport.publish("BASE1", b"\xd3\x00\x00")
        await task

        assert count == 1

    async def test_delivers_to_multiple_subscribers(self, transport: QueueTransport) -> None:
        results: list[list[bytes]] = [[], []]

        async def consumer(idx: int) -> None:
            async with transport.subscribe("BASE1") as sub:
                frame = await sub.get()
                if frame is not None:
                    results[idx].append(frame)

        tasks = [asyncio.create_task(consumer(i)) for i in range(2)]
        await asyncio.sleep(0)
        count = await transport.publish("BASE1", b"\xd3\x00\x00")
        await asyncio.gather(*tasks)

        assert count == 2
        assert results[0] == results[1] == [b"\xd3\x00\x00"]


# ── subscribe ─────────────────────────────────────────────────────────────────


class TestSubscribe:
    async def test_unknown_mountpoint_raises_key_error(self, transport: QueueTransport) -> None:
        with pytest.raises(KeyError):
            async with transport.subscribe("GHOST"):
                pass  # pragma: no cover

    async def test_frames_delivered_in_order(self, transport: QueueTransport) -> None:
        frames = [b"\x01", b"\x02", b"\x03"]
        received: list[bytes] = []

        async def consumer() -> None:
            async with transport.subscribe("BASE1") as sub:
                while len(received) < len(frames):
                    frame = await sub.get()
                    if frame is None:
                        break
                    received.append(frame)

        task = asyncio.create_task(consumer())
        await asyncio.sleep(0)
        for f in frames:
            await transport.publish("BASE1", f)
        await task

        assert received == frames

    async def test_subscriber_removed_after_context_exit(self, transport: QueueTransport) -> None:
        task = asyncio.create_task(_collect_one(transport, "BASE1"))
        await asyncio.sleep(0)
        await transport.publish("BASE1", b"\xd3\x00\x00")
        await task

        assert await transport.publish("BASE1", b"\xd3\x00\x00") == 0

    async def test_two_mountpoints_are_isolated(self) -> None:
        t = QueueTransport()
        await t.open("BASE1")
        await t.open("BASE2")

        t1 = asyncio.create_task(_collect_one(t, "BASE1"))
        t2 = asyncio.create_task(_collect_one(t, "BASE2"))
        await asyncio.sleep(0)
        await t.publish("BASE1", b"\xaa")
        await t.publish("BASE2", b"\xbb")
        r1, r2 = await asyncio.gather(t1, t2)

        assert r1 == [b"\xaa"]
        assert r2 == [b"\xbb"]
