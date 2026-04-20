"""WebSocket session management for Sanic WebSocket connections."""

from __future__ import annotations

import asyncio

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from sanic import Websocket


if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class SessionManager:
    """
    Context manager class for registering and unregistering WebSocket sessions and
    their associated asyncio tasks.

    Manages the session registry and lock as instance attributes, encapsulating
    the state to avoid global variables.
    """

    def __init__(self):
        """Initialize the session manager with an empty session registry and lock."""
        self._sessions: dict[Websocket, asyncio.Task] = {}
        self._lock: asyncio.Lock = asyncio.Lock()

    @asynccontextmanager
    async def __call__(self, ws: Websocket) -> AsyncIterator[None]:
        """Context manager for managing WebSocket sessions.

        Args:
            ws (Websocket): The Sanic WebSocket connection to manage.

        Yields:
            None: Yields control back to the caller while maintaining the session registration.

        Ensures:
            - Registers the WebSocket session on entry
            - Automatically unregisters the session on exit, even if exceptions occur
            - Thread-safe using an asyncio lock
            - Handles exceptions gracefully
        """
        async with self._lock:
            current_task = asyncio.current_task()
            if current_task is None:
                raise RuntimeError("No current task found for session registration")
            self._sessions[ws] = current_task

        try:
            yield
        finally:
            async with self._lock:
                self._sessions.pop(ws, None)

    async def cleanup(self) -> None:
        """Cancel all active WebSocket sessions and wait for their tasks to finish."""
        from contextlib import suppress

        async with self._lock:
            tasks = list(self._sessions.values())
            for task in tasks:
                task.cancel()
            self._sessions = {}

        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task  # Wait for the task to complete.
