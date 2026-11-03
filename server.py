"""Socket.IO server, session registry, rooms, and routing logic (R2, R3, R4).

This module implements the server-side Socket.IO application for the Simple Chat
Application. It hosts the ChatEngine state machine and manages client connect/disconnect
lifecycles, session mapping (sid -> username), room-based group multiplexing,
presence broadcasting, and identity derivation to enforce the Server Trust Boundary.
"""

from __future__ import annotations

import argparse
import logging
import threading
from datetime import datetime
from socketserver import ThreadingMixIn
from typing import Any
from wsgiref.simple_server import WSGIServer, make_server

import socketio

from protocol import (
    EVENT_ANNOUNCEMENT,
    EVENT_CREATE_GROUP,
    EVENT_GROUP_LIST,
    EVENT_GROUP_MSG,
    EVENT_JOIN_GROUP,
    EVENT_PRIVATE_MSG,
    EVENT_READ_ACK,
    EVENT_REGISTER,
    EVENT_REGISTER_ACK,
    EVENT_SEND_STICKER,
    EVENT_STICKER_MSG,
    EVENT_USER_LIST,
    AnnouncementPayload,
    CreateGroupPayload,
    GroupInfo,
    GroupListPayload,
    GroupMessagePayload,
    GroupMessageServerPayload,
    JoinGroupPayload,
    PrivateMessagePayload,
    PrivateMessageServerPayload,
    ProtocolValidationError,
    ReadAckClientPayload,
    ReadAckServerPayload,
    RegisterAckPayload,
    RegisterPayload,
    SendStickerPayload,
    StickerMessageServerPayload,
    UserListPayload,
    parse_client_event,
    validate_group_name,
    validate_username,
)

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Headless Server State Machine: ChatEngine
# -----------------------------------------------------------------------------


class ChatEngine:
    """Headless state and routing engine for the Simple Chat Application.

    Manages user sessions, active presence, group memberships, and identity derivation.
    Thread-safe and decoupled from network transports and desktop GUI logic for
    headless testability.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sessions: dict[str, str] = {}  # sid -> username
        self._users: dict[str, str] = {}  # username -> sid
        self._groups: dict[str, set[str]] = {}  # group_name -> set[username]

    # --- Session & Identity Management (R3, R4) ---

    def register_user(self, sid: str, username: str) -> RegisterAckPayload:
        """Register a unique username for a connected client session.

        Enforces non-empty, unpadded alphanumeric format and uniqueness.
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

    def get_user_list_payload(self) -> UserListPayload:
        """Generate the UserListPayload broadcast model."""
        return UserListPayload(users=self.get_online_users())

    # --- Group Management (R8, R9, R10, R11) ---

    def create_group(self, creator_username: str, group_name: str) -> tuple[bool, str]:
        """Create a new chat group with creator as the sole initial member (R8)."""
        try:
            group_name = validate_group_name(group_name)
        except ProtocolValidationError as e:
            return False, e.message

        with self._lock:
            if group_name in self._groups:
                return False, f"Group '{group_name}' already exists"
            self._groups[group_name] = {creator_username}
            return True, "Group created successfully"

    def join_group(self, username: str, group_name: str) -> tuple[bool, str]:
        """Add a user voluntarily to an existing chat group (R10)."""
        try:
            group_name = validate_group_name(group_name)
        except ProtocolValidationError as e:
            return False, e.message

        with self._lock:
            if group_name not in self._groups:
                return False, f"Group '{group_name}' does not exist"
            self._groups[group_name].add(username)
            return True, "Joined group successfully"

    def is_group_member(self, username: str, group_name: str) -> bool:
        """Verify whether a user is currently a member of a group (R11 access control)."""
        with self._lock:
            return group_name in self._groups and username in self._groups[group_name]

    def get_groups(self) -> list[GroupInfo]:
        """Return all chat groups with their current member rosters (R9)."""
        with self._lock:
            return [
                GroupInfo(group_name=name, members=sorted(members))
                for name, members in sorted(self._groups.items())
            ]

    def get_group_list_payload(self) -> GroupListPayload:
        """Generate the GroupListPayload broadcast model."""
        return GroupListPayload(groups=self.get_groups())

    def get_user_groups(self, username: str) -> list[str]:
        """Return all group names that the user is currently a member of."""
        with self._lock:
            return [name for name, members in sorted(self._groups.items()) if username in members]


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
    """Socket.IO server managing event routing, user presence, rooms, and security boundaries."""

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
            self.sio.emit(
                EVENT_USER_LIST,
                self.engine.get_user_list_payload().to_dict(),
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
                # Re-enter existing group rooms if reconnecting
                for group_name in self.engine.get_user_groups(ack.username):
                    self.sio.enter_room(sid, group_name)
                self.broadcast_user_list()
                self.sio.emit(
                    EVENT_GROUP_LIST,
                    self.engine.get_group_list_payload().to_dict(),
                    room=sid,
                )

        @self.sio.event
        def private_message(sid: str, data: Any) -> None:
            sender = self.engine.get_username(sid)
            if not sender:
                logger.warning("Unregistered sid %s attempted private_message", sid)
                return

            try:
                payload = parse_client_event(EVENT_PRIVATE_MSG, data)
                assert isinstance(payload, PrivateMessagePayload)
            except ProtocolValidationError as e:
                logger.warning("Invalid private_message from %s: %s", sender, e)
                return

            outbound = PrivateMessageServerPayload(
                sender=sender,
                target=payload.target,
                message=payload.message,
                meta=payload.meta,
            )
            outbound_dict = outbound.to_dict()

            target_sid = self.engine.get_sid(payload.target)
            if target_sid is not None:
                self.sio.emit(EVENT_PRIVATE_MSG, outbound_dict, room=target_sid)
            if target_sid != sid:
                self.sio.emit(EVENT_PRIVATE_MSG, outbound_dict, room=sid)

        @self.sio.event
        def create_group(sid: str, data: Any) -> None:
            creator = self.engine.get_username(sid)
            if not creator:
                logger.warning("Unregistered sid %s attempted create_group", sid)
                return

            try:
                payload = parse_client_event(EVENT_CREATE_GROUP, data)
                assert isinstance(payload, CreateGroupPayload)
            except ProtocolValidationError as e:
                logger.warning("Invalid create_group from %s: %s", creator, e)
                return

            success, message = self.engine.create_group(creator, payload.group_name)
            if success:
                self.sio.enter_room(sid, payload.group_name)
                logger.info("Group '%s' created by %s", payload.group_name, creator)
                self.broadcast_group_list()
            else:
                logger.warning("Group creation failed for %s: %s", creator, message)

        @self.sio.event
        def join_group(sid: str, data: Any) -> None:
            username = self.engine.get_username(sid)
            if not username:
                logger.warning("Unregistered sid %s attempted join_group", sid)
                return

            try:
                payload = parse_client_event(EVENT_JOIN_GROUP, data)
                assert isinstance(payload, JoinGroupPayload)
            except ProtocolValidationError as e:
                logger.warning("Invalid join_group from %s: %s", username, e)
                return

            success, message = self.engine.join_group(username, payload.group_name)
            if success:
                self.sio.enter_room(sid, payload.group_name)
                logger.info("User %s joined group '%s'", username, payload.group_name)
                self.broadcast_group_list()
            else:
                logger.warning("Join group failed for %s: %s", username, message)

        @self.sio.event
        def group_message(sid: str, data: Any) -> None:
            sender = self.engine.get_username(sid)
            if not sender:
                logger.warning("Unregistered sid %s attempted group_message", sid)
                return

            try:
                payload = parse_client_event(EVENT_GROUP_MSG, data)
                assert isinstance(payload, GroupMessagePayload)
            except ProtocolValidationError as e:
                logger.warning("Invalid group_message from %s: %s", sender, e)
                return

            # Room access control: ensure sender is currently in the group
            if not self.engine.is_group_member(sender, payload.group_name):
                logger.warning(
                    "User %s attempted message in non-member group '%s'",
                    sender,
                    payload.group_name,
                )
                return

            outbound = GroupMessageServerPayload(
                sender=sender,
                group_name=payload.group_name,
                message=payload.message,
                meta=payload.meta,
            )
            self.sio.emit(EVENT_GROUP_MSG, outbound.to_dict(), room=payload.group_name)

        @self.sio.event
        def send_sticker(sid: str, data: Any) -> None:
            sender = self.engine.get_username(sid)
            if not sender:
                logger.warning("Unregistered sid %s attempted send_sticker", sid)
                return

            try:
                payload = parse_client_event(EVENT_SEND_STICKER, data)
                assert isinstance(payload, SendStickerPayload)
            except ProtocolValidationError as e:
                logger.warning("Invalid send_sticker from %s: %s", sender, e)
                return

            outbound = StickerMessageServerPayload(
                sender=sender,
                sticker_id=payload.sticker_id,
                meta=payload.meta,
                target=payload.target,
                group_name=payload.group_name,
            )
            outbound_dict = outbound.to_dict()

            if payload.target:
                target_sid = self.engine.get_sid(payload.target)
                if target_sid is not None:
                    self.sio.emit(EVENT_STICKER_MSG, outbound_dict, room=target_sid)
                if target_sid != sid:
                    self.sio.emit(EVENT_STICKER_MSG, outbound_dict, room=sid)
            elif payload.group_name:
                if not self.engine.is_group_member(sender, payload.group_name):
                    logger.warning(
                        "User %s attempted sticker in non-member group '%s'",
                        sender,
                        payload.group_name,
                    )
                    return
                self.sio.emit(EVENT_STICKER_MSG, outbound_dict, room=payload.group_name)

        @self.sio.event
        def read_ack(sid: str, data: Any) -> None:
            reader = self.engine.get_username(sid)
            if not reader:
                logger.warning("Unregistered sid %s attempted read_ack", sid)
                return

            try:
                payload = parse_client_event(EVENT_READ_ACK, data)
                assert isinstance(payload, ReadAckClientPayload)
            except ProtocolValidationError as e:
                logger.warning("Invalid read_ack from %s: %s", reader, e)
                return

            target_sid = self.engine.get_sid(payload.target)
            if target_sid is not None:
                outbound = ReadAckServerPayload(
                    message_id=payload.message_id,
                    reader=reader,
                )
                self.sio.emit(EVENT_READ_ACK, outbound.to_dict(), room=target_sid)

    # --- Broadcast Helpers (R4, R9, SP3) ---

    def broadcast_user_list(self) -> None:
        """Broadcast updated connected user roster to all clients (R4)."""
        payload = self.engine.get_user_list_payload()
        self.sio.emit(EVENT_USER_LIST, payload.to_dict())

    def broadcast_group_list(self) -> None:
        """Broadcast updated chat groups with member rosters to all clients (R9)."""
        payload = self.engine.get_group_list_payload()
        self.sio.emit(EVENT_GROUP_LIST, payload.to_dict())

    def broadcast_announcement(
        self, message: str, timestamp: str | None = None
    ) -> AnnouncementPayload:
        """Broadcast a system-wide announcement to all connected clients (SP3)."""
        if timestamp is None:
            timestamp = datetime.now().strftime("%H:%M")
        payload = AnnouncementPayload(message=message, timestamp=timestamp)
        self.sio.emit(EVENT_ANNOUNCEMENT, payload.to_dict())
        logger.info("Broadcasted announcement: '%s' at %s", message, timestamp)
        return payload

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
