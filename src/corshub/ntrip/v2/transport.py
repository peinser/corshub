"""
Abstract transport layer for NTRIP v2 RTCM frame delivery.

A Transport is responsible solely for routing raw RTCM bytes between a base
station (publisher) and one or more rovers (subscribers).  It has no knowledge
of mountpoint credentials, source-table metadata, or HTTP concerns — those
belong to NTRIPCaster and the route handlers respectively.

Implementations
---------------
AsyncQueueTransport   In-process asyncio.Queue fan-out.  Zero dependencies,
                      suitable for single-server deployments.

Future implementations might route via Redis Pub/Sub, NATS subjects, etc.
The NTRIPCaster accepts any Transport, so swapping is a one-line change.
"""

from __future__ import annotations

import asyncio

from abc import ABC
from abc import abstractmethod
from collections.abc import AsyncGenerator
from contextlib import AbstractAsyncContextManager
from contextlib import asynccontextmanager
from typing import Final


class Transport(ABC):

    @abstractmethod
    async def publish(self, mountpoint: str, frame: bytes) -> int:
        """Deliver *frame* to every active subscriber on *mountpoint*.

        Returns the number of subscribers that received the frame.
        The method should NOT raise a KeyError if *mountpoint* is not known to this transport.
        """
        ...

    @abstractmethod
    def subscribe(self, mountpoint: str) -> AbstractAsyncContextManager[TransportSubscriber]:
        """Return an async context manager that yields a TransportSubscriber.

        Cleanup is guaranteed in the __aexit__ path regardless of how the
        caller exits — normal return, exception, or transport close.
        Raises KeyError if *mountpoint* is not known to this transport.

        Usage::

            async with transport.subscribe(mountpoint) as sub:
                while (frame := await sub.get()) is not None:
                    ...  # process frame; sub.get() returns None on close
        """
        ...

    @abstractmethod
    async def open(self, mountpoint: str) -> None:
        """Register *mountpoint* so it can accept publishers and subscribers.

        Raises ValueError if *mountpoint* is already open.
        """
        ...

    @abstractmethod
    async def close(self, mountpoint: str) -> None:
        """Deregister *mountpoint* and signal all active subscribers to stop.

        Raises KeyError if *mountpoint* is not open.
        """
        ...


class TransportSubscriber(ABC):

    @abstractmethod
    async def cleanup(self) -> None:
        """Mark this subscriber as cancelled and drain any remaining items.

        This should be called when the subscriber is done consuming frames, to ensure
        that any pending frames are cleaned up and no tasks are left hanging.
        """
        ...

    @abstractmethod
    async def get(self) -> bytes | None:
        """Get the next frame from the queue, or None if cancelled."""
        ...


class QueueTransportSubscriber(TransportSubscriber):
    """
    Helper class to manage a single subscriber's queue and cancellation.

    Designed to be shared safely between multiple producer tasks and one consumer task.
    Guarantees that the queue is drained/cleaned up and no tasks are left hanging.
    """

    def __init__(self) -> None:
        self.queue: asyncio.Queue[bytes] = asyncio.Queue()
        self.cancelled: bool = False
        self._sentinel: Final[object] = object()  # Unique sentinel value to signal cancellation

    def _signal_done(self) -> None:
        """Signal that this subscriber is done consuming frames."""
        self.cancelled = True
        try:
            self.queue.put_nowait(self._sentinel)  # Signal any waiting producers to stop
        except (asyncio.QueueFull, RuntimeError):
            pass  # If the queue is full or closed, we can ignore this

    def drain(self) -> None:
        """
        Drain any remaining items and mark them as done.

        Useful during final cleanup to avoid "Task exception was never retrieved"
        warnings and to let queue.join() complete cleanly if used elsewhere.
        """
        while not self.queue.empty():
            try:
                _ = self.queue.get_nowait()
                self.queue.task_done()
            except:  # Safety net
                break

    async def cleanup(self) -> None:
        """
        Mark this subscriber as cancelled and drain any remaining items.

        This should be called when the subscriber is done consuming frames, to ensure
        that any pending frames are cleaned up and no tasks are left hanging.
        """
        self.cancelled = True
        self.drain()
        self._signal_done()  # Ensure any waiting producers are signaled to stop

    def shutdown(self) -> None:
        """
        Signal this subscriber to stop consuming frames immediately.
        Call this from the task that is consuming frames to break out of the loop and perform cleanup. Safe to call multiple times.
        """
        if self.cancelled:
            return  # Already cancelled, no action needed

        self.cancelled = True
        self._signal_done()  # Signal any waiting producers to stop

    async def publish(self, frame: bytes) -> bool:
        """Publish a frame to this subscriber's queue."""
        if self.cancelled:
            return False  # Don't publish to cancelled subscribers

        try:
            await self.queue.put(frame)
            return True

        except asyncio.CancelledError:
            self.cancelled = True
            raise

        except Exception:
            self.cancelled = True
            raise

    async def get(self) -> bytes | None:
        """Get the next frame from the queue, or None if cancelled."""
        if self.cancelled:
            return None

        try:
            item = await self.queue.get()
            if item is self._sentinel:
                self.queue.task_done()  # Mark the sentinel as done
                return None  # Signal to stop consuming

            return item

        except asyncio.CancelledError:
            self.cancelled = True
            raise



class QueueTransport(Transport):
    """In-process transport using asyncio.Queues for pub/sub.

    Suitable for single-server deployments, zero dependencies.
    """

    def __init__(self) -> None:
        super().__init__()
        self._queues: dict[str, list[QueueTransportSubscriber]] = {}

    async def open(self, mountpoint: str) -> None:
        if mountpoint in self._queues:
            raise ValueError(mountpoint)
        self._queues[mountpoint] = []

    async def close(self, mountpoint: str) -> None:
        subscribers = self._queues.pop(mountpoint)  # raises KeyError if unknown
        for subscriber in subscribers:
            subscriber.shutdown()

    @asynccontextmanager
    async def subscribe(self, mountpoint: str) -> AsyncGenerator[QueueTransportSubscriber, None]:
        if mountpoint not in self._queues:
            raise KeyError(mountpoint)
        subscriber = QueueTransportSubscriber()
        self._queues[mountpoint].append(subscriber)
        try:
            yield subscriber
        finally:
            subscribers = self._queues.get(mountpoint, [])
            if subscriber in subscribers:
                subscribers.remove(subscriber)
            await subscriber.cleanup()

    async def publish(self, mountpoint: str, frame: bytes) -> int:
        """Deliver *frame* to every active subscriber on *mountpoint*.

        Returns the number of subscribers that received the frame.
        Raises KeyError if *mountpoint* is not open.
        """
        subscribers = self._queues[mountpoint]  # raises KeyError if unknown
        if not subscribers:
            return 0

        fanout_results = await asyncio.gather(
            *(subscriber.publish(frame) for subscriber in list(subscribers)),
            return_exceptions=True,
        )

        acks = 0
        for result in fanout_results:
            if result is True:
                acks += 1

        return acks
