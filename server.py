"""Socket.IO server, session registry, and presence broadcasting (R2, R3, R4).

This module implements the server-side Socket.IO application for the Simple Chat
Application. It hosts the ChatEngine state machine and manages client connect/disconnect
lifecycles, session mapping (sid -> username), presence broadcasting, and identity
derivation to enforce the Server Trust Boundary.
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
from dataclasses import dataclass
from datetime import datetime
from socketserver import ThreadingMixIn
from typing import Any, Generic, TextIO, TypeVar
from wsgiref.simple_server import WSGIServer, make_server

import socketio

from protocol import (
    EVENT_ANNOUNCEMENT,
    EVENT_CREATE_GROUP,
    EVENT_GROUP_ERROR,
    EVENT_GROUP_LIST,
    EVENT_GROUP_MSG,
    EVENT_JOIN_GROUP,
    EVENT_PRIVATE_MSG,
    EVENT_PRIVATE_MSG_ERROR,
    EVENT_READ_ACK,
    EVENT_RECEIVE_GROUP,
    EVENT_REGISTER,
    EVENT_REGISTER_ACK,
    EVENT_SEND_GROUP,
    EVENT_USER_LIST,
    AnnouncementPayload,
    CreateGroupPayload,
    GroupInfo,
    GroupListPayload,
    GroupMessagePayload,
    GroupMessageServerPayload,
    JoinGroupPayload,
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
    validate_avatar,
    validate_group_name,
    validate_message_text,
    validate_timestamp,
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


@dataclass(frozen=True)
class GroupRouteResult:
    """Encapsulates a group creation or voluntary-join decision."""

    success: bool
    error: str | None = None


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
        self._avatars: dict[str, str] = {}  # username -> selected character
        self._private_messages: dict[str, tuple[str, str]] = {}  # message_id -> (sender, target)
        self._groups: dict[str, set[str]] = {}  # group_name -> member usernames

    # --- Session & Identity Management (R3, R4) ---

    def register_user(self, sid: str, username: str, avatar: str = "rabbit") -> RegisterAckPayload:
        """Register a unique username for a connected client session.

        Enforces non-empty, unpadded alphanumeric format and uniqueness.
        Validates username as a defensive domain invariant for direct engine usage.
        """
        try:
            username = validate_username(username)
            avatar = validate_avatar(avatar)
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
                self._avatars.pop(old_user, None)
                for members in self._groups.values():
                    members.discard(old_user)

            self._sessions[sid] = username
            self._users[username] = sid
            self._avatars[username] = avatar
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
                self._avatars.pop(username, None)
                for members in self._groups.values():
                    members.discard(username)
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

    def get_active_sids(self) -> list[str]:
        """Return all active registered client Socket.IO session IDs (SP3)."""
        with self._lock:
            return [self._users[username] for username in self.get_online_users()]

    def get_online_avatars(self) -> dict[str, str]:
        """Return selected characters for the active user roster."""
        with self._lock:
            return {username: self._avatars[username] for username in self.get_online_users()}

    # --- Group Lifecycle (R8, R9, R10) ---

    def create_group(self, creator_sid: str, group_name: str) -> GroupRouteResult:
        """Create a group whose sole initial member is the authenticated creator (R8)."""
        try:
            group_name = validate_group_name(group_name)
        except ProtocolValidationError as error:
            return GroupRouteResult(success=False, error=error.message)

        with self._lock:
            creator = self._sessions.get(creator_sid)
            if creator is None:
                return GroupRouteResult(success=False, error="Creator not registered")
            if group_name in self._groups:
                return GroupRouteResult(success=False, error="Group name already exists")
            self._groups[group_name] = {creator}
            return GroupRouteResult(success=True)

    def join_group(self, member_sid: str, group_name: str) -> GroupRouteResult:
        """Add the authenticated requester to an existing group (R10)."""
        try:
            group_name = validate_group_name(group_name)
        except ProtocolValidationError as error:
            return GroupRouteResult(success=False, error=error.message)

        with self._lock:
            member = self._sessions.get(member_sid)
            if member is None:
                return GroupRouteResult(success=False, error="Member not registered")
            members = self._groups.get(group_name)
            if members is None:
                return GroupRouteResult(success=False, error="Group does not exist")
            members.add(member)
            return GroupRouteResult(success=True)

    def get_groups(self) -> list[GroupInfo]:
        """Return each group with its sorted current member list (R9)."""
        with self._lock:
            return [
                GroupInfo(group_name=name, members=sorted(members))
                for name, members in sorted(self._groups.items())
            ]

    def get_groups_for_user(self, username: str) -> list[str]:
        """Return group rooms currently containing username, for transport cleanup."""
        with self._lock:
            return [name for name, members in self._groups.items() if username in members]

    # --- Group Message Routing (R11) ---

    def route_group_message(
        self,
        sender_sid: str,
        group_name: str,
        message: str,
        meta: MessageMeta,
    ) -> RouteResult[GroupMessageServerPayload]:
        """Route a group message enforcing member verification (R10, R11).

        Returns:
            RouteResult: Encapsulates routing decision and verified group payload.
        """
        try:
            group_name = validate_group_name(group_name)
            message = validate_message_text(message)
        except ProtocolValidationError as e:
            return RouteResult(success=False, error=e.message)

        with self._lock:
            sender = self._sessions.get(sender_sid)
            if sender is None:
                return RouteResult(success=False, error="Sender not registered")

            members = self._groups.get(group_name)
            if members is None:
                return RouteResult(success=False, error=f"Group '{group_name}' does not exist")

            if sender not in members:
                return RouteResult(
                    success=False,
                    error=f"User '{sender}' is not a member of group '{group_name}'",
                )

            server_payload = GroupMessageServerPayload(
                sender=sender,
                group_name=group_name,
                message=message,
                meta=meta,
            )
            return RouteResult(
                success=True,
                payload=server_payload,
            )

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
            self._private_messages[meta.message_id] = (sender, target)
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
        """Route a read receipt acknowledgment (SP4).

        Enforces Server Trust Boundary and message ownership verification.

        Returns:
            ReadAckRouteResult: Encapsulates routing decision, verified reader, and target sid.
        """
        with self._lock:
            reader = self._sessions.get(reader_sid)
            if reader is None:
                return ReadAckRouteResult(success=False, error="Reader not registered")

            # Ownership verification: reject arbitrary or forged message ACKs (SP4)
            ownership = self._private_messages.get(message_id)
            if ownership is None:
                return ReadAckRouteResult(success=False, error=f"Message '{message_id}' not found")

            original_sender, original_recipient = ownership
            if original_recipient != reader or original_sender != target:
                return ReadAckRouteResult(
                    success=False,
                    error=f"Read ACK ownership mismatch for message '{message_id}'",
                )

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
            initial_roster = self._user_list_payload()
            self.sio.emit(
                EVENT_USER_LIST,
                initial_roster.to_dict(),
                room=sid,
            )
            self.sio.emit(EVENT_GROUP_LIST, self._group_list_payload().to_dict(), room=sid)

        @self.sio.event
        def disconnect(sid: str) -> None:
            username = self.engine.unregister_user(sid)
            if username is not None:
                logger.info("Client disconnected: user=%s sid=%s", username, sid)
                self.broadcast_user_list()
                self.broadcast_group_list()
            else:
                logger.info("Unregistered client disconnected: sid=%s", sid)

        @self.sio.event
        def register(sid: str, data: Any) -> None:
            prior_username = self.engine.get_username(sid)
            try:
                payload = parse_client_event(EVENT_REGISTER, data)
                assert isinstance(payload, RegisterPayload)
                prior_groups = (
                    self.engine.get_groups_for_user(prior_username)
                    if prior_username is not None and prior_username != payload.username
                    else []
                )
                ack = self.engine.register_user(sid, payload.username, payload.avatar)
            except ProtocolValidationError as e:
                ack = RegisterAckPayload(success=False, error=e.message)
                prior_groups = []

            self.sio.emit(EVENT_REGISTER_ACK, ack.to_dict(), room=sid)

            if ack.success and ack.username:
                for group_name in prior_groups:
                    self.sio.leave_room(sid, group_name)
                logger.info("User registered successfully: user=%s sid=%s", ack.username, sid)
                self.broadcast_user_list()
                self.broadcast_group_list()

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
        def create_group(sid: str, data: Any) -> None:
            try:
                payload = parse_client_event(EVENT_CREATE_GROUP, data)
                assert isinstance(payload, CreateGroupPayload)
            except ProtocolValidationError as error:
                logger.warning("Invalid create_group from sid=%s: %s", sid, error)
                self.sio.emit(EVENT_GROUP_ERROR, {"error": error.message}, room=sid)
                return

            result = self.engine.create_group(sid, payload.group_name)
            if not result.success:
                logger.warning("Failed to create group from sid=%s: %s", sid, result.error)
                self.sio.emit(EVENT_GROUP_ERROR, {"error": result.error}, room=sid)
                return
            self.sio.enter_room(sid, payload.group_name)
            self.broadcast_group_list()

        @self.sio.event
        def join_group(sid: str, data: Any) -> None:
            try:
                payload = parse_client_event(EVENT_JOIN_GROUP, data)
                assert isinstance(payload, JoinGroupPayload)
            except ProtocolValidationError as error:
                logger.warning("Invalid join_group from sid=%s: %s", sid, error)
                self.sio.emit(EVENT_GROUP_ERROR, {"error": error.message}, room=sid)
                return

            result = self.engine.join_group(sid, payload.group_name)
            if not result.success:
                logger.warning("Failed to join group from sid=%s: %s", sid, result.error)
                self.sio.emit(EVENT_GROUP_ERROR, {"error": result.error}, room=sid)
                return
            self.sio.enter_room(sid, payload.group_name)
            self.broadcast_group_list()

        @self.sio.event
        def group_message(sid: str, data: Any) -> None:
            self._handle_group_message(sid, data)

        @self.sio.on(EVENT_SEND_GROUP)
        def handle_send_group(sid: str, data: Any) -> None:
            self._handle_group_message(sid, data)

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

    def _handle_group_message(self, sid: str, data: Any) -> None:
        """Route group messages to group room members only (R11)."""
        try:
            payload = parse_client_event(EVENT_GROUP_MSG, data)
            assert isinstance(payload, GroupMessagePayload)
        except ProtocolValidationError as error:
            logger.warning("Invalid group_message from sid=%s: %s", sid, error)
            self.sio.emit(EVENT_GROUP_ERROR, {"error": error.message}, room=sid)
            self.sio.emit("ERROR", {"error": error.message}, room=sid)
            return

        result = self.engine.route_group_message(
            sender_sid=sid,
            group_name=payload.group_name,
            message=payload.message,
            meta=payload.meta,
        )
        if not result.success or result.payload is None:
            logger.warning("Failed to route group message from sid=%s: %s", sid, result.error)
            error_msg = result.error or "Failed to deliver group message"
            self.sio.emit(EVENT_GROUP_ERROR, {"error": error_msg}, room=sid)
            self.sio.emit("ERROR", {"error": error_msg}, room=sid)
            return

        outbound_dict = result.payload.to_dict()
        self.sio.emit(EVENT_GROUP_MSG, outbound_dict, room=payload.group_name)
        self.sio.emit(EVENT_RECEIVE_GROUP, outbound_dict, room=payload.group_name)

    # --- Broadcast Helpers (R4) ---
        @self.sio.event
        def announcement(sid: str, data: Any = None) -> None:
            del data
            logger.warning(
                "Rejected unauthorized client announcement spoof attempt from sid=%s",
                sid,
            )

    # --- Broadcast Helpers (R4, R9, SP3) ---

    def _user_list_payload(self) -> UserListPayload:
        """Build the current presence roster once for direct and broadcast delivery."""
        return UserListPayload(
            users=self.engine.get_online_users(), avatars=self.engine.get_online_avatars()
        )

    def broadcast_user_list(self) -> None:
        """Broadcast updated connected user roster to all clients (R4)."""
        payload = self._user_list_payload()
        self.sio.emit(EVENT_USER_LIST, payload.to_dict())

    def _group_list_payload(self) -> GroupListPayload:
        """Build the current group roster once for direct and broadcast delivery."""
        return GroupListPayload(groups=self.engine.get_groups())

    def broadcast_group_list(self) -> None:
        """Broadcast all group names and their member lists (R9)."""
        self.sio.emit(EVENT_GROUP_LIST, self._group_list_payload().to_dict())

    def broadcast_announcement(
        self,
        message: str,
        timestamp: str | None = None,
    ) -> AnnouncementPayload:
        """Broadcast a system-wide announcement to all connected clients (SP3).

        Iterates over all active client sockets (with a broadcast fallback when no
        registered sessions are tracked) and delivers the announcement regardless
        of which chat rooms clients have open.
        """
        clean_message = validate_message_text(message, field_name="message")
        clean_timestamp = validate_timestamp(
            timestamp if timestamp is not None else datetime.now().strftime("%H:%M")
        )
        payload = AnnouncementPayload(message=clean_message, timestamp=clean_timestamp)
        outbound = payload.to_dict()
        active_sids = self.engine.get_active_sids()
        if active_sids:
            for client_sid in active_sids:
                self.sio.emit(EVENT_ANNOUNCEMENT, outbound, room=client_sid)
        else:
            self.sio.emit(EVENT_ANNOUNCEMENT, outbound)
        logger.info(
            "Broadcast announcement [%s] to %d client(s): %s",
            clean_timestamp,
            len(active_sids),
            clean_message,
        )
        return payload

    def run_console_loop(self, stream: TextIO | None = None) -> None:
        """Read operator lines from server console stdin and broadcast announcements (SP3)."""
        input_stream = stream if stream is not None else sys.stdin
        for raw_line in input_stream:
            text = raw_line.strip()
            if not text:
                continue
            if text.startswith("/announce"):
                text = text[len("/announce") :].strip()
                if not text:
                    continue
            try:
                self.broadcast_announcement(text)
            except ProtocolValidationError as error:
                logger.warning("Ignored invalid console announcement: %s", error)

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
        print("  Type a message (or /announce <msg>) and press Enter to broadcast (SP3).")
        print("  Press Ctrl+C to terminate.")
        print("============================================================")
        console_thread = threading.Thread(
            target=self.run_console_loop,
            name="server-console-announcement",
            daemon=True,
        )
        console_thread.start()
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
