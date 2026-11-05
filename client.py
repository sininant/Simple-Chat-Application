"""Desktop client Socket.IO adapter and headless ClientSession (R2, R3, R4).

This module provides the headless ClientSession adapter wrapping python-socketio Client.
It manages connection lifecycles, user authentication, and dispatches incoming network
events to a thread-safe queue.Queue so the CustomTkinter GUI thread never freezes.
"""

from __future__ import annotations

import argparse
import logging
import queue
import threading
from typing import Any

import socketio

from protocol import (
    EVENT_REGISTER,
    EVENT_REGISTER_ACK,
    EVENT_USER_LIST,
    ProtocolValidationError,
    RegisterAckPayload,
    RegisterPayload,
    UserListPayload,
    parse_server_event,
    validate_username,
)

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Headless Client Session Adapter: ClientSession
# -----------------------------------------------------------------------------


class ClientSession:
    """Headless client-side state and Socket.IO adapter for the Simple Chat Application.

    Wraps socketio.Client(), manages session lifecycle and active presence,
    and forwards incoming server events into a thread-safe queue.Queue for GUI polling.

    Note on event parsing: Core lifecycle and presence events (register_ack, user_list)
    are strictly parsed via protocol.py schemas. Downstream issue owners (e.g. #7 private
    messages, #9 groups, #11 stickers) should register explicit typed handlers using
    protocol.parse_server_event rather than relying solely on raw catch_all dictionaries.
    """

    def __init__(
        self,
        server_url: str = "http://127.0.0.1:5000",
        event_queue: queue.Queue[tuple[str, Any]] | None = None,
        sio: socketio.Client | None = None,
    ) -> None:
        self.server_url = server_url
        self.event_queue: queue.Queue[tuple[str, Any]] = (
            event_queue if event_queue is not None else queue.Queue()
        )
        self.sio = sio if sio is not None else socketio.Client(reconnection=True, request_timeout=5)

        self._lock = threading.RLock()
        self._username: str | None = None
        self._online_users: list[str] = []
        self._is_connected: bool = False
        self._is_registered: bool = False

        self._register_handlers()

    def _register_handlers(self) -> None:
        """Register Socket.IO event callbacks that push parsed payloads to the event queue."""

        @self.sio.event
        def connect() -> None:
            with self._lock:
                self._is_connected = True
            logger.info("Connected to server: %s", self.server_url)
            self.event_queue.put(("connect", None))

        @self.sio.event
        def disconnect() -> None:
            with self._lock:
                self._is_connected = False
                self._is_registered = False
            logger.info("Disconnected from server")
            self.event_queue.put(("disconnect", None))

        @self.sio.event
        def register_ack(data: Any) -> None:
            payload = parse_server_event(EVENT_REGISTER_ACK, data)
            assert isinstance(payload, RegisterAckPayload)
            with self._lock:
                if payload.success and payload.username:
                    self._is_registered = True
                    self._username = payload.username
                elif not self._is_registered:
                    self._is_registered = False
                    self._username = None
            self.event_queue.put((EVENT_REGISTER_ACK, payload))

        @self.sio.event
        def user_list(data: Any) -> None:
            payload = parse_server_event(EVENT_USER_LIST, data)
            assert isinstance(payload, UserListPayload)
            with self._lock:
                self._online_users = list(payload.users)
            self.event_queue.put((EVENT_USER_LIST, payload))

        @self.sio.on("*")
        def catch_all(event: str, data: Any) -> None:
            # Pass unhandled server events through to the queue for downstream UI handlers.
            # Downstream features (#7, #9, #11) should register explicit typed handlers
            # via parse_server_event.
            if event not in {EVENT_REGISTER_ACK, EVENT_USER_LIST, "connect", "disconnect"}:
                self.event_queue.put((event, data))

    # --- Connection & Session Operations ---

    def connect(
        self,
        server_url: str | None = None,
        transports: list[str] | None = None,
    ) -> bool:
        """Connect to the Socket.IO server at server_url."""
        if server_url:
            self.server_url = server_url
        with self._lock:
            if self._is_connected:
                return True
        try:
            kwargs: dict[str, Any] = {}
            if transports is not None:
                kwargs["transports"] = transports
            self.sio.connect(self.server_url, **kwargs)
            return True
        except Exception as e:
            logger.error("Failed to connect to %s: %s", self.server_url, e)
            return False

    def disconnect(self) -> None:
        """Disconnect from the Socket.IO server cleanly."""
        try:
            if self.sio.connected:
                self.sio.disconnect()
        except Exception as e:
            logger.warning("Error during disconnect: %s", e)
        finally:
            with self._lock:
                self._is_connected = False
                self._is_registered = False

    def register(self, username: str) -> bool:
        """Send a registration request with the desired username (R3).

        Validates the username format against protocol specifications.
        If invalid, avoids raising a synchronous exception on the calling (e.g. GUI) thread,
        instead enqueuing a synthetic failed RegisterAckPayload and returning False.
        """
        try:
            clean_username = validate_username(username)
        except ProtocolValidationError as e:
            self.event_queue.put(
                (EVENT_REGISTER_ACK, RegisterAckPayload(success=False, error=e.message))
            )
            return False

        payload = RegisterPayload(username=clean_username)
        self.sio.emit(EVENT_REGISTER, payload.to_dict())
        return True

    def get_next_event(
        self, block: bool = False, timeout: float | None = None
    ) -> tuple[str, Any] | None:
        """Fetch the next event from the queue without blocking the UI thread."""
        try:
            return self.event_queue.get(block=block, timeout=timeout)
        except queue.Empty:
            return None

    # --- Thread-Safe State Accessors ---

    @property
    def is_connected(self) -> bool:
        """Return True if the underlying socket is connected."""
        with self._lock:
            return self._is_connected

    @property
    def is_registered(self) -> bool:
        """Return True if successfully registered with a unique username."""
        with self._lock:
            return self._is_registered

    @property
    def username(self) -> str | None:
        """Return the registered username, or None if unregistered."""
        with self._lock:
            return self._username

    @property
    def online_users(self) -> list[str]:
        """Return the latest list of registered online usernames (R4)."""
        with self._lock:
            return list(self._online_users)

    @property
    def sid(self) -> str | None:
        """Return the assigned Socket.IO session ID if connected."""
        return self.sio.sid if self.sio.connected else None


# -----------------------------------------------------------------------------
# CLI Entrypoint
# -----------------------------------------------------------------------------


def main() -> None:
    """CLI runner for headless ClientSession."""
    parser = argparse.ArgumentParser(description="Simple Chat Application Client Session")
    parser.add_argument(
        "--server-url",
        default="http://127.0.0.1:5000",
        help="Socket.IO server URL (default: http://127.0.0.1:5000)",
    )
    parser.add_argument(
        "--username",
        help="Username to register with immediately upon connection",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose debug logging",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    session = ClientSession(server_url=args.server_url)
    print(f"Connecting to {args.server_url}...")
    if not session.connect():
        print("Failed to connect to server.")
        return

    if args.username:
        print(f"Registering username '{args.username}'...")
        session.register(args.username)

    print("ClientSession running. Press Ctrl+C to disconnect.")
    try:
        while True:
            event = session.get_next_event(block=True, timeout=1.0)
            if event is not None:
                event_type, payload = event
                print(f"[Event: {event_type}] {payload}")
    except KeyboardInterrupt:
        print("\nDisconnecting...")
        session.disconnect()


if __name__ == "__main__":
    main()
