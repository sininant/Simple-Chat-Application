"""Socket.IO server, session registry, and presence broadcasting (R2, R3, R4).

This module implements the server-side Socket.IO application for the Simple Chat
Application. It hosts the ChatEngine state machine and manages client connect/disconnect
lifecycles, session mapping (sid -> username), presence broadcasting, and identity
derivation to enforce the Server Trust Boundary.
"""

from __future__ import annotations

import argparse
import logging
import threading
from dataclasses import dataclass
from socketserver import ThreadingMixIn
from typing import Any, Generic, TypeVar
from wsgiref.simple_server import WSGIServer, make_server

import socketio

from protocol import (
    EVENT_PRIVATE_MSG,
    EVENT_PRIVATE_MSG_ERROR,
    EVENT_READ_ACK,
    EVENT_REGISTER,
    EVENT_REGISTER_ACK,
    EVENT_USER_LIST,
    MessageMeta,
    PrivateMessagePayload,
    PrivateMessageServerPayload,
    ProtocolValidationError,
    ReadAckClientPayload,
    ReadAckServerPayload,
    RegisterAckPayload,
    RegisterPayload,
    UserListPayload,
    parse_client_event,
    validate_username,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass(frozen=True)
class RouteResult(Generic[T]):
    """Encapsulates the result of routing a 1-to-1 message or acknowledgment."""

    success: bool
    error: str | None = None
    payload: T | None = None
    target_sid: str | None = None

    def __iter__(self):
        """Allow 4-element tuple unpacking for backwards compatibility."""
        return iter((self.success, self.error, self.payload, self.target_sid))


# Specialized subclass for read acknowledgments (SP4)
@dataclass(frozen=True)
class ReadAckRouteResult(RouteResult[ReadAckServerPayload]):
    """Encapsulates the result of routing a 1-to-1 read acknowledgment (SP4)."""


# -----------------------------------------------------------------------------
# Headless Server State Machine: ChatEngine
# -----------------------------------------------------------------------------


class ChatEngine:
    """Headless state and routing engine for the Simple Chat Application.

    Manages user sessions, active presence, and identity derivation.
    Thread-safe and decoupled from network transports and desktop GUI logic for
    headless testability.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sessions: dict[str, str] = {}  # sid -> username
        self._users: dict[str, str] = {}  # username -> sid

    # --- Session & Identity Management (R3, R4) ---

    def register_user(self, sid: str, username: str) -> RegisterAckPayload:
        """Register a unique username for a connected client session.

        Enforces non-empty, unpadded alphanumeric format and uniqueness.
        Validates username as a defensive domain invariant for direct engine usage.
        """
        try:
            username = validate_username(username)
        except ProtocolValidationError as e:
            return RegisterAckPayload(success=False, error=e.message)

        with self._lock:
            current_owner = self._users.get(username)
            if current_owner is not None and current_owner != sid:
                return RegisterAckPayload(
                    success=False,
                    error="Username already taken",
                )

            # If this sid was previously registered with another username, clean up
            old_user = self._sessions.get(sid)
            if old_user is not None and old_user != username:
                self._users.pop(old_user, None)

            self._sessions[sid] = username
            self._users[username] = sid
            return RegisterAckPayload(
                success=True,
                username=username,
                message="Registered successfully",
            )

    def unregister_user(self, sid: str) -> str | None:
        """Remove a client session upon disconnect and return the unregistered username."""
        with self._lock:
            username = self._sessions.pop(sid, None)
            if username is not None:
                self._users.pop(username, None)
            return username

    def get_username(self, sid: str) -> str | None:
        """Derive the authenticated username from a Socket.IO session ID."""
        with self._lock:
            return self._sessions.get(sid)

    def get_sid(self, username: str) -> str | None:
        """Retrieve the active Socket.IO session ID for a username."""
        with self._lock:
            return self._users.get(username)

    def is_registered(self, sid: str) -> bool:
        """Check whether a session ID is currently registered."""
        with self._lock:
            return sid in self._sessions

    def get_online_users(self) -> list[str]:
        """Return a sorted list of all currently registered online usernames (R4)."""
        with self._lock:
            return sorted(self._users.keys())

    # --- 1-to-1 Private Message Routing (R7) ---

    def route_private_message(
        self,
        sender_sid: str,
        target: str,
        message: str,
        meta: MessageMeta,
    ) -> RouteResult:
        """Route a private message enforcing Server Trust Boundary and direct delivery (R7).

        Returns:
            RouteResult: Encapsulates routing decision, sanitized payload, and recipient sid.
        """
        with self._lock:
            sender = self._sessions.get(sender_sid)
            if sender is None:
                return RouteResult(success=False, error="Sender not registered")

            target_sid = self._users.get(target)
            if target_sid is None:
                return RouteResult(success=False, error=f"User '{target}' is offline")

            server_payload = PrivateMessageServerPayload(
                sender=sender,
                target=target,
                message=message,
                meta=meta,
            )
            return RouteResult(
                success=True,
                payload=server_payload,
                target_sid=target_sid,
            )

    # --- 1-to-1 Read Receipt Routing (SP4) ---

    def route_read_ack(
        self,
        reader_sid: str,
        message_id: str,
        target: str,
    ) -> ReadAckRouteResult:
        """Route a read receipt acknowledgment enforcing Server Trust Boundary (SP4).

        Returns:
            ReadAckRouteResult: Encapsulates routing decision, verified reader, and target sid.
        """
        with self._lock:
            reader = self._sessions.get(reader_sid)
            if reader is None:
                return ReadAckRouteResult(success=False, error="Reader not registered")

            target_sid = self._users.get(target)
            if target_sid is None:
                return ReadAckRouteResult(success=False, error=f"Target user '{target}' is offline")

            server_payload = ReadAckServerPayload(
                message_id=message_id,
                reader=reader,
            )
            return ReadAckRouteResult(
                success=True,
                payload=server_payload,
                target_sid=target_sid,
            )


# -----------------------------------------------------------------------------
# Multi-Threaded WSGI Server
# -----------------------------------------------------------------------------


class ThreadedWSGIServer(ThreadingMixIn, WSGIServer):
    """Multi-threaded WSGI HTTP server allowing concurrent Socket.IO client connections."""

    daemon_threads = True


# -----------------------------------------------------------------------------
# Socket.IO Application Server: ChatServer
# -----------------------------------------------------------------------------


class ChatServer:
    """Socket.IO server managing event routing, user presence, and security boundaries."""

    def __init__(
        self,
        engine: ChatEngine | None = None,
        sio: socketio.Server | None = None,
    ) -> None:
        self.engine = engine or ChatEngine()
        self.sio = sio or socketio.Server(cors_allowed_origins="*", async_mode="threading")
        self.app = socketio.WSGIApp(self.sio)
        self._register_handlers()

    def _register_handlers(self) -> None:
        """Register Socket.IO event handlers enforcing the Server Trust Boundary."""

        @self.sio.event
        def connect(sid: str, environ: dict[str, Any]) -> None:
            logger.info("Connection established: sid=%s", sid)
            # Emit initial user roster to the connecting client
            initial_roster = UserListPayload(users=self.engine.get_online_users())
            self.sio.emit(
                EVENT_USER_LIST,
                initial_roster.to_dict(),
                room=sid,
            )

        @self.sio.event
        def disconnect(sid: str) -> None:
            username = self.engine.unregister_user(sid)
            if username is not None:
                logger.info("Client disconnected: user=%s sid=%s", username, sid)
                self.broadcast_user_list()
            else:
                logger.info("Unregistered client disconnected: sid=%s", sid)

        @self.sio.event
        def register(sid: str, data: Any) -> None:
            try:
                payload = parse_client_event(EVENT_REGISTER, data)
                assert isinstance(payload, RegisterPayload)
                ack = self.engine.register_user(sid, payload.username)
            except ProtocolValidationError as e:
                ack = RegisterAckPayload(success=False, error=e.message)

            self.sio.emit(EVENT_REGISTER_ACK, ack.to_dict(), room=sid)

            if ack.success and ack.username:
                logger.info("User registered successfully: user=%s sid=%s", ack.username, sid)
                self.broadcast_user_list()

        @self.sio.event
        def private_message(sid: str, data: Any) -> None:
            try:
                payload = parse_client_event(EVENT_PRIVATE_MSG, data)
                assert isinstance(payload, PrivateMessagePayload)
            except ProtocolValidationError as e:
                logger.warning("Invalid private_message from sid=%s: %s", sid, e)
                return

            result = self.engine.route_private_message(
                sender_sid=sid,
                target=payload.target,
                message=payload.message,
                meta=payload.meta,
            )
            if not result.success or result.payload is None or result.target_sid is None:
                logger.warning("Failed to route private message from sid=%s: %s", sid, result.error)
                self.sio.emit(
                    EVENT_PRIVATE_MSG_ERROR,
                    {
                        "error": result.error or "Failed to deliver message",
                        "target": payload.target,
                    },
                    room=sid,
                )
                return

            outbound_dict = result.payload.to_dict()
            # Deliver strictly to target recipient and sender echo (R7)
            self.sio.emit(EVENT_PRIVATE_MSG, outbound_dict, room=result.target_sid)
            if result.target_sid != sid:
                self.sio.emit(EVENT_PRIVATE_MSG, outbound_dict, room=sid)

        @self.sio.event
        def read_ack(sid: str, data: Any) -> None:
            try:
                payload = parse_client_event(EVENT_READ_ACK, data)
                assert isinstance(payload, ReadAckClientPayload)
            except ProtocolValidationError as e:
                logger.warning("Invalid read_ack from sid=%s: %s", sid, e)
                return

            result = self.engine.route_read_ack(
                reader_sid=sid,
                message_id=payload.message_id,
                target=payload.target,
            )
            if not result.success or result.payload is None or result.target_sid is None:
                logger.warning("Failed to route read_ack from sid=%s: %s", sid, result.error)
                return

            # Deliver strictly to original sender (SP4)
            self.sio.emit(EVENT_READ_ACK, result.payload.to_dict(), room=result.target_sid)

    # --- Broadcast Helpers (R4) ---

    def broadcast_user_list(self) -> None:
        """Broadcast updated connected user roster to all clients (R4)."""
        payload = UserListPayload(users=self.engine.get_online_users())
        self.sio.emit(EVENT_USER_LIST, payload.to_dict())

    # --- HTTP/WSGI Server Lifecycle ---

    def create_http_server(self, host: str = "0.0.0.0", port: int = 5000) -> ThreadedWSGIServer:
        """Create a configured ThreadedWSGIServer instance."""
        return make_server(host, port, self.app, server_class=ThreadedWSGIServer)

    def serve(self, host: str = "0.0.0.0", port: int = 5000) -> None:
        """Start serving incoming Socket.IO requests on host and port."""
        httpd = self.create_http_server(host, port)
        print("============================================================")
        print("  Simple Chat Application - Socket.IO Server")
        print(f"  Listening on http://{host}:{port}")
        print("  Press Ctrl+C to terminate.")
        print("============================================================")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down server gracefully...")
        finally:
            httpd.server_close()


# -----------------------------------------------------------------------------
# CLI Entrypoint
# -----------------------------------------------------------------------------


def main() -> None:
    """CLI entrypoint for running the Socket.IO server."""
    parser = argparse.ArgumentParser(description="Simple Chat Application Socket.IO Server")
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host interface to bind (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=5000,
        help="Port to listen on (default: 5000)",
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

    server = ChatServer()
    server.serve(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
