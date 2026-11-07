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
import uuid
from datetime import datetime
from typing import Any

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
    EVENT_USER_LIST,
    AnnouncementPayload,
    CreateGroupPayload,
    GroupErrorPayload,
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
    parse_server_event,
    validate_avatar,
    validate_group_name,
    validate_message_id,
    validate_message_text,
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
        self._avatar = "rabbit"
        self._online_users: list[str] = []
        self._groups: list[dict[str, Any]] = []
        self._announcements: list[AnnouncementPayload] = []
        self._is_connected: bool = False
        self._is_registered: bool = False

        self._register_handlers()

    def _register_handlers(self) -> None:
        """Register Socket.IO event callbacks that push parsed payloads to the event queue."""

        @self.sio.event
        def connect() -> None:
            with self._lock:
                self._is_connected = True
                username_to_reregister = self._username
                avatar_to_reregister = self._avatar
            logger.info("Connected to server: %s", self.server_url)
            self.event_queue.put(("connect", None))
            if username_to_reregister is not None:
                logger.info("Auto re-registering on reconnect as: %s", username_to_reregister)
                self.register(username_to_reregister, avatar_to_reregister)

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

        @self.sio.event
        def group_list(data: Any) -> None:
            try:
                payload = parse_server_event(EVENT_GROUP_LIST, data)
                assert isinstance(payload, GroupListPayload)
            except ProtocolValidationError as error:
                logger.warning("Received invalid group_list from server: %s", error)
                return
            with self._lock:
                self._groups = [group.to_dict() for group in payload.groups]
            self.event_queue.put((EVENT_GROUP_LIST, payload))

        @self.sio.event
        def group_error(data: Any) -> None:
            try:
                payload = parse_server_event(EVENT_GROUP_ERROR, data)
                assert isinstance(payload, GroupErrorPayload)
            except ProtocolValidationError as error:
                logger.warning("Received invalid group_error from server: %s", error)
                return
            self.event_queue.put((EVENT_GROUP_ERROR, payload))

        @self.sio.event
        def group_message(data: Any) -> None:
            try:
                payload = parse_server_event(EVENT_GROUP_MSG, data)
                assert isinstance(payload, GroupMessageServerPayload)
                self.event_queue.put((EVENT_GROUP_MSG, payload))
            except ProtocolValidationError as e:
                logger.warning("Received invalid group_message from server: %s", e)

        @self.sio.on(EVENT_RECEIVE_GROUP)
        def receive_group(data: Any) -> None:
            try:
                payload = parse_server_event(EVENT_RECEIVE_GROUP, data)
                assert isinstance(payload, GroupMessageServerPayload)
                self.event_queue.put((EVENT_GROUP_MSG, payload))
            except ProtocolValidationError as e:
                logger.warning("Received invalid RECEIVE_GROUP from server: %s", e)

        @self.sio.event
        def private_message(data: Any) -> None:
            try:
                payload = parse_server_event(EVENT_PRIVATE_MSG, data)
                assert isinstance(payload, PrivateMessageServerPayload)
                self.event_queue.put((EVENT_PRIVATE_MSG, payload))
            except ProtocolValidationError as e:
                logger.warning("Received invalid private_message from server: %s", e)

        @self.sio.event
        def private_message_error(data: Any) -> None:
            self.event_queue.put((EVENT_PRIVATE_MSG_ERROR, data))

        @self.sio.event
        def read_ack(data: Any) -> None:
            try:
                payload = parse_server_event(EVENT_READ_ACK, data)
                assert isinstance(payload, ReadAckServerPayload)
                self.event_queue.put((EVENT_READ_ACK, payload))
            except ProtocolValidationError as e:
                logger.warning("Received invalid read_ack from server: %s", e)

        @self.sio.event
        def announcement(data: Any) -> None:
            try:
                payload = parse_server_event(EVENT_ANNOUNCEMENT, data)
                assert isinstance(payload, AnnouncementPayload)
            except ProtocolValidationError as e:
                logger.warning("Received invalid announcement from server: %s", e)
                return
            with self._lock:
                self._announcements.append(payload)
            self.event_queue.put((EVENT_ANNOUNCEMENT, payload))

        @self.sio.on("*")
        def catch_all(event: str, data: Any) -> None:
            # Pass unhandled server events through to the queue for downstream UI handlers.
            # Downstream features (#9, #11) should register explicit typed handlers
            # via parse_server_event.
            if event not in {
                EVENT_REGISTER_ACK,
                EVENT_USER_LIST,
                EVENT_GROUP_LIST,
                EVENT_GROUP_ERROR,
                EVENT_GROUP_MSG,
                EVENT_RECEIVE_GROUP,
                EVENT_PRIVATE_MSG,
                EVENT_PRIVATE_MSG_ERROR,
                EVENT_READ_ACK,
                EVENT_ANNOUNCEMENT,
                "connect",
                "disconnect",
            }:
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

    def register(self, username: str, avatar: str = "rabbit") -> bool:
        """Send a registration request with the desired username (R3).

        Validates the username format against protocol specifications.
        If invalid, avoids raising a synchronous exception on the calling (e.g. GUI) thread,
        instead enqueuing a synthetic failed RegisterAckPayload and returning False.
        """
        try:
            clean_username = validate_username(username)
            clean_avatar = validate_avatar(avatar)
        except ProtocolValidationError as e:
            self.event_queue.put(
                (EVENT_REGISTER_ACK, RegisterAckPayload(success=False, error=e.message))
            )
            return False

        with self._lock:
            self._avatar = clean_avatar
        payload = RegisterPayload(username=clean_username, avatar=clean_avatar)
        self.sio.emit(EVENT_REGISTER, payload.to_dict())
        return True

    def send_private_message(
        self,
        target: str,
        message: str,
        message_id: str | None = None,
        timestamp: str | None = None,
    ) -> str | None:
        """Send a 1-to-1 private message to target peer over Socket.IO (R7).

        Returns:
            The message_id if emission succeeded, or None if validation/emission failed.
        """
        if not self.is_connected or (self._username is not None and not self.is_registered):
            logger.warning("Cannot send private message: client is not connected and registered")
            return None

        try:
            clean_target = validate_username(target, field_name="target")
            clean_message = validate_message_text(message)
        except ProtocolValidationError as e:
            logger.warning("Invalid private message input: %s", e)
            return None

        msg_id = message_id or f"MSG_{uuid.uuid4().hex[:8]}"
        ts = timestamp or datetime.now().strftime("%H:%M")

        try:
            meta = MessageMeta(message_id=msg_id, timestamp=ts)
            payload = PrivateMessagePayload(
                target=clean_target,
                message=clean_message,
                meta=meta,
            )
        except ProtocolValidationError as e:
            logger.warning("Private message metadata validation failed: %s", e)
            return None

        self.sio.emit(EVENT_PRIVATE_MSG, payload.to_dict())
        return msg_id

    def create_group(self, group_name: str) -> bool:
        """Request creation of a group with this client as the initial member (R8)."""
        if not self.is_connected or not self.is_registered:
            logger.warning("Cannot create group: client is not connected and registered")
            return False
        try:
            payload = CreateGroupPayload(group_name=validate_group_name(group_name))
        except ProtocolValidationError as error:
            logger.warning("Invalid group creation request: %s", error)
            return False
        self.sio.emit(EVENT_CREATE_GROUP, payload.to_dict())
        return True

    def join_group(self, group_name: str) -> bool:
        """Request that this client voluntarily joins an existing group (R10)."""
        if not self.is_connected or not self.is_registered:
            logger.warning("Cannot join group: client is not connected and registered")
            return False
        try:
            payload = JoinGroupPayload(group_name=validate_group_name(group_name))
        except ProtocolValidationError as error:
            logger.warning("Invalid group join request: %s", error)
            return False
        self.sio.emit(EVENT_JOIN_GROUP, payload.to_dict())
        return True

    def send_group_message(
        self,
        group_name: str,
        message: str,
        message_id: str | None = None,
        timestamp: str | None = None,
    ) -> str | None:
        """Send a message to a joined group over Socket.IO (R11).

        Returns:
            The message_id if emission succeeded, or None if validation/emission failed.
        """
        if not self.is_connected or not self.is_registered:
            logger.warning("Cannot send group message: client is not connected and registered")
            return None

        try:
            clean_group = validate_group_name(group_name, field_name="group_name")
            clean_message = validate_message_text(message)
        except ProtocolValidationError as e:
            logger.warning("Invalid group message input: %s", e)
            return None

        msg_id = message_id or f"MSG_{uuid.uuid4().hex[:8]}"
        ts = timestamp or datetime.now().strftime("%H:%M")

        try:
            meta = MessageMeta(message_id=msg_id, timestamp=ts)
            payload = GroupMessagePayload(
                group_name=clean_group,
                message=clean_message,
                meta=meta,
            )
        except ProtocolValidationError as e:
            logger.warning("Group message metadata validation failed: %s", e)
            return None

        self.sio.emit(EVENT_GROUP_MSG, payload.to_dict())
        return msg_id

    def send_read_ack(self, message_id: str, target: str) -> bool:
        """Send a 1-to-1 read receipt acknowledgment over Socket.IO (SP4).

        Returns:
            True if emission succeeded, False if disconnected or validation failed.
        """
        if not self.is_connected or (self._username is not None and not self.is_registered):
            logger.warning("Cannot send read_ack: client is not connected and registered")
            return False

        try:
            clean_mid = validate_message_id(message_id, field_name="message_id")
            clean_target = validate_username(target, field_name="target")
            payload = ReadAckClientPayload(message_id=clean_mid, target=clean_target)
        except ProtocolValidationError as e:
            logger.warning("Invalid read_ack payload: %s", e)
            return False

        self.sio.emit(EVENT_READ_ACK, payload.to_dict())
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
    def groups(self) -> list[dict[str, Any]]:
        """Return the latest group list and member rosters received from the server (R9)."""
        with self._lock:
            return [group.copy() | {"members": list(group["members"])} for group in self._groups]

    @property
    def announcements(self) -> list[AnnouncementPayload]:
        """Return the history of server announcements received during this session (SP3)."""
        with self._lock:
            return list(self._announcements)

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
