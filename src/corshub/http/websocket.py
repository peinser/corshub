"""WebSocket session management for Sanic WebSocket connections.

This module provides functionality for managing WebSocket sessions within the Sanic
application, including a context manager class for safely registering and unregistering
WebSocket sessions with automatic cleanup handling. It encapsulates session management
and exception handling, replacing manual try-finally blocks with a more robust,
exception-safe approach. The session registry and lock are managed as instance attributes
to avoid global state.

This module is located in `http/websocket.py` as it directly pertains to WebSocket
connection handling in the Sanic application.

Suggestions for further improvement:
1. If session management grows in complexity or is needed elsewhere, consider moving
   `SessionManager` to a dedicated `core/session_manager.py` module for better modularity.
2. Add logging for session registration/unregistration events.
3. Consider adding session metadata storage (e.g., timestamps, client info).
4. Implement session timeout policies for automatic cleanup.
5. Add type hints for better static type checking.
6. Consider making `SessionManager` a singleton if only one instance is needed globally.

Example usage:
```python
from http.websocket import SessionManager


async def handle_websocket(
    ws: Websocket,
    request,
    identifier,
    serial_number,
):
    session_manager = SessionManager()
    async with session_manager(ws):
        await _ocpp16(
            request=request,
            ws=ws,
            identifier=identifier,
            serial_number=serial_number,
        )
    # Session is automatically unregistered on context exit, even if exceptions occur
```

Dependencies:
- asyncio: For lock and task management
- sanic: For WebSocket protocol handling
"""

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
        """
        Clean up all active chargepoint sessions. This method can for instance be called
        whenever a SIGTERM has been received.

        Cancels all registered tasks and waits for their completion. Note, after completion,
        the session management structure will be empty.

        Ensures:
            - All sessions are properly cancelled
            - Handles CancelledError exceptions
            - Thread-safe using the session registry lock
        """
        from contextlib import suppress

        async with self._lock:
            tasks = list(self._sessions.values())
            for task in tasks:
                task.cancel()
            self._sessions = {}

        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task  # Wait for the task to complete.
