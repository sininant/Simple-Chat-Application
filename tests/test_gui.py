"""Behavior tests for the GUI's headless ClientSession bridge."""

from __future__ import annotations

from collections import deque
from typing import Any

import pytest

from gui import GuiController, MainChatWindow
from protocol import (
    EVENT_CONNECT,
    EVENT_PRIVATE_MSG,
    EVENT_PRIVATE_MSG_ERROR,
    EVENT_READ_ACK,
    EVENT_REGISTER_ACK,
    EVENT_USER_LIST,
    MessageMeta,
    PrivateMessageServerPayload,
    ReadAckServerPayload,
    RegisterAckPayload,
    UserListPayload,
    parse_server_event,
)
from server import ChatServer
from tests.test_server import SocketIOServerSpy


class SessionSpy:
    """Small public ClientSession-shaped fake with no GUI or network dependency."""

    def __init__(
        self,
        events: list[tuple[str, Any]] | None = None,
        connect_result: bool = True,
        send_read_ack_result: bool = True,
    ) -> None:
        self.events = deque(events or [])
        self.connected_to: list[str] = []
        self.registered: list[str] = []
        self.sent_private: list[tuple[str, str, str | None, str | None]] = []
        self.sent_read_ack: list[tuple[str, str]] = []
        self.connect_result = connect_result
        self.send_read_ack_result = send_read_ack_result

    def connect(self, server_url: str) -> bool:
        self.connected_to.append(server_url)
        return self.connect_result

    def register(self, username: str) -> bool:
        self.registered.append(username)
        return True

    def send_private_message(
        self,
        target: str,
        message: str,
        message_id: str | None = None,
        timestamp: str | None = None,
    ) -> str | None:
        self.sent_private.append((target, message, message_id, timestamp))
        return message_id or "MSG_TEST"

    def send_read_ack(self, message_id: str, target: str) -> bool:
        if self.send_read_ack_result:
            self.sent_read_ack.append((message_id, target))
            return True
        return False

    def get_next_event(self, *, block: bool = False) -> tuple[str, Any] | None:
        del block
        return self.events.popleft() if self.events else None


def test_controller_connects_then_registers_the_requested_user():
    session = SessionSpy()
    controller = GuiController(session)

    assert controller.connect_and_register("127.0.0.1", "5000", "Alice") is True
    assert session.connected_to == ["http://127.0.0.1:5000"]
    assert session.registered == ["Alice"]


def test_controller_preserves_invalid_username_for_clientsession_validation():
    session = SessionSpy()
    controller = GuiController(session)

    controller.connect_and_register("127.0.0.1", "5000", " Alice ")

    assert session.registered == [" Alice "]


def test_controller_applies_queued_login_and_presence_events():
    session = SessionSpy(
        [
            (
                EVENT_REGISTER_ACK,
                RegisterAckPayload(success=True, username="Alice", message="Welcome"),
            ),
            (EVENT_USER_LIST, UserListPayload(users=["Alice", "Bob"])),
        ]
    )
    controller = GuiController(session)

    controller.poll_events()

    assert controller.username == "Alice"
    assert controller.online_users == ["Alice", "Bob"]
    assert controller.login_error is None


def test_controller_adds_the_registered_user_before_the_first_presence_update():
    session = SessionSpy(
        [
            (
                EVENT_REGISTER_ACK,
                RegisterAckPayload(success=True, username="Alice", message="Welcome"),
            )
        ]
    )
    controller = GuiController(session)

    controller.poll_events()

    assert controller.online_users == ["Alice"]


def test_controller_exposes_login_rejection_inline():
    session = SessionSpy(
        [(EVENT_REGISTER_ACK, RegisterAckPayload(success=False, error="Username already taken"))]
    )
    controller = GuiController(session)

    controller.poll_events()

    assert controller.username is None
    assert controller.login_error == "Username already taken"


def test_controller_rejects_missing_connection_fields_without_connecting():
    session = SessionSpy()
    controller = GuiController(session)

    assert controller.connect_and_register("", "5000", "Alice") is False
    assert controller.connect_and_register("127.0.0.1", "", "Alice") is False
    assert controller.connect_and_register("127.0.0.1", "5000", "") is False
    assert session.connected_to == []


def test_controller_rejects_invalid_port_values():
    session = SessionSpy()
    controller = GuiController(session)

    assert controller.connect_and_register("127.0.0.1", "abc", "Alice") is False
    assert controller.connect_and_register("127.0.0.1", "0", "Alice") is False
    assert controller.connect_and_register("127.0.0.1", "65536", "Alice") is False
    assert session.connected_to == []


def test_controller_reports_connection_failure():
    session = SessionSpy(connect_result=False)
    controller = GuiController(session)

    assert controller.connect_and_register("127.0.0.1", "5000", "Alice") is False
    assert controller.login_error == "Could not connect to the server."


def test_controller_send_private_message_forwards_to_session():
    session = SessionSpy()
    controller = GuiController(session)

    msg_id = controller.send_private_message("Bob", "Hello Bob!")
    assert msg_id == "MSG_TEST"
    assert session.sent_private == [("Bob", "Hello Bob!", None, None)]


def test_controller_polls_and_records_incoming_private_messages():
    payload = PrivateMessageServerPayload(
        sender="Bob",
        target="Alice",
        message="Hi Alice!",
        meta=MessageMeta(message_id="MSG_001", timestamp="14:30"),
    )
    session = SessionSpy([(EVENT_PRIVATE_MSG, payload)])
    controller = GuiController(session)
    controller.username = "Alice"

    dispatched: list[tuple[str, PrivateMessageServerPayload]] = []
    controller.on_private_message = lambda peer, p: dispatched.append((peer, p))

    controller.poll_events()

    assert "Bob" in controller.private_messages
    assert controller.private_messages["Bob"] == [payload]
    assert len(dispatched) == 1
    assert dispatched[0] == ("Bob", payload)


def test_controller_polls_and_records_own_echo_private_messages():
    payload = PrivateMessageServerPayload(
        sender="Alice",
        target="Bob",
        message="Hi Bob from me!",
        meta=MessageMeta(message_id="MSG_002", timestamp="14:31"),
    )
    session = SessionSpy([(EVENT_PRIVATE_MSG, payload)])
    controller = GuiController(session)
    controller.username = "Alice"

    controller.poll_events()

    # Even though Alice sent it, the conversation room must be Bob
    assert "Bob" in controller.private_messages
    assert controller.private_messages["Bob"] == [payload]


def test_controller_polls_and_notifies_private_message_error():
    error_data = {"error": "User 'Bob' is offline", "target": "Bob"}
    session = SessionSpy([(EVENT_PRIVATE_MSG_ERROR, error_data)])
    controller = GuiController(session)

    errors: list[tuple[str, str]] = []
    controller.on_private_message_error = lambda target, err: errors.append((target, err))

    controller.poll_events()

    assert len(errors) == 1
    assert errors[0] == ("Bob", "User 'Bob' is offline")


def test_controller_send_read_ack_forwards_to_session():
    session = SessionSpy()
    controller = GuiController(session)

    assert controller.send_read_ack("MSG_001", "Alice") is True
    assert session.sent_read_ack == [("MSG_001", "Alice")]


def test_controller_polls_and_notifies_read_ack():
    payload = ReadAckServerPayload(message_id="MSG_001", reader="Bob")
    session = SessionSpy([(EVENT_READ_ACK, payload)])
    controller = GuiController(session)

    acks: list[tuple[str, str]] = []
    controller.on_read_ack = lambda mid, reader: acks.append((mid, reader))

    controller.poll_events()

    assert len(acks) == 1
    assert acks[0] == ("MSG_001", "Bob")


class DirectSession:
    """In-memory session adapter connecting GuiController directly to ChatServer."""

    def __init__(self, server: ChatServer, sid: str, username: str) -> None:
        self.server = server
        self.sid = sid
        self.username = username
        self.events: deque[tuple[str, Any]] = deque()
        self.is_connected = True

    def send_private_message(
        self,
        target: str,
        message: str,
        message_id: str | None = None,
        timestamp: str | None = None,
    ) -> str | None:
        mid = message_id or "MSG_001"
        ts = timestamp or "12:00"
        payload = {
            "target": target,
            "message": message,
            "message_id": mid,
            "timestamp": ts,
        }
        self.server.sio._trigger_event("private_message", "/", self.sid, payload)
        return mid

    def send_read_ack(self, message_id: str, target: str) -> bool:
        payload = {"message_id": message_id, "target": target}
        self.server.sio._trigger_event("read_ack", "/", self.sid, payload)
        return True

    def get_next_event(self, *, block: bool = False) -> tuple[str, Any] | None:
        return self.events.popleft() if self.events else None


def test_main_window_retries_pending_read_acks_on_reconnect():
    """Verify pending ACKs retained during network failure are retried on reconnect (SP4)."""
    try:
        import customtkinter as ctk
    except ImportError:
        pytest.skip("customtkinter is required for UI tests")

    root = ctk.CTk()
    root.withdraw()
    try:
        session = SessionSpy(send_read_ack_result=False)
        controller = GuiController(session)
        controller.username = "Bob"
        controller.online_users = ["Alice", "Bob"]
        window = MainChatWindow(root, controller)
        window.open_private_room("Alice", select=True)

        payload = PrivateMessageServerPayload(
            sender="Alice",
            target="Bob",
            message="Hello",
            meta=MessageMeta(message_id="MSG_RETRY_1", timestamp="12:00"),
        )
        window._handle_incoming_private_message("Alice", payload)

        # Failed ACK is preserved in _unread_messages
        assert "MSG_RETRY_1" in window._unread_messages.get("Alice", set())
        assert "MSG_RETRY_1" not in window._acknowledged_messages

        # Connection restored: controller notifies reconnect
        session.send_read_ack_result = True
        session.events.append((EVENT_CONNECT, None))
        controller.poll_events()

        # ACK has now been sent and marked acknowledged
        assert ("MSG_RETRY_1", "Alice") in session.sent_read_ack
        assert "MSG_RETRY_1" in window._acknowledged_messages
        assert "MSG_RETRY_1" not in window._unread_messages.get("Alice", set())
    finally:
        root.destroy()


def test_end_to_end_private_message_and_read_ack_flow():
    """Verify true end-to-end UI flow:
    Alice sends message via ChatServer -> Bob receives in background ->
    Bob exercises MainChatWindow room viewing -> ChatServer routes verified
    READ_ACK back to Alice -> Alice's MessageBubble updates to 'Read ✓✓'.
    """
    try:
        import customtkinter as ctk
    except ImportError:
        pytest.skip("customtkinter is required for end-to-end UI flow test")

    root = ctk.CTk()
    root.withdraw()
    try:
        spy = SocketIOServerSpy()
        server = ChatServer(sio=spy.sio)
        sessions: dict[str, DirectSession] = {}

        orig_emit = spy.sio.emit

        def direct_emit(
            event: str,
            data: Any = None,
            to: Any = None,
            room: Any = None,
            **kwargs: Any,
        ) -> None:
            orig_emit(event, data, to=to, room=room, **kwargs)
            target_sid = room or to
            if target_sid and target_sid in sessions:
                parsed = parse_server_event(event, data)
                sessions[target_sid].events.append((event, parsed))

        spy.sio.emit = direct_emit

        # Setup Alice and Bob in server engine
        alice_sess = DirectSession(server, "sid_alice", "Alice")
        bob_sess = DirectSession(server, "sid_bob", "Bob")
        sessions["sid_alice"] = alice_sess
        sessions["sid_bob"] = bob_sess

        server.engine.register_user("sid_alice", "Alice")
        server.engine.register_user("sid_bob", "Bob")

        # 1. Initialize Alice's controller & MainChatWindow
        alice_ctrl = GuiController(alice_sess)
        alice_ctrl.username = "Alice"
        alice_ctrl.online_users = ["Alice", "Bob"]
        alice_win = MainChatWindow(root, alice_ctrl)
        alice_win.open_private_room("Bob", select=True)

        # 2. Initialize Bob's controller & MainChatWindow (Bob has not opened Alice's room yet)
        bob_ctrl = GuiController(bob_sess)
        bob_ctrl.username = "Bob"
        bob_ctrl.online_users = ["Alice", "Bob"]
        bob_win = MainChatWindow(root, bob_ctrl)

        # 3. Alice sends a private message to Bob through MainChatWindow
        alice_win.rooms["Bob"].on_send("Hello Bob!")
        # Alice polls events to process server echo
        alice_ctrl.poll_events()
        assert "MSG_001" in alice_win.rooms["Bob"].bubbles
        alice_bubble = alice_win.rooms["Bob"].bubbles["MSG_001"]
        assert "Sent" in alice_bubble.status_label.cget("text")
        assert "Read ✓✓" not in alice_bubble.status_label.cget("text")

        # 4. Bob receives Alice's message in background (room not viewed yet)
        bob_ctrl.poll_events()
        assert "MSG_001" in bob_win._unread_messages.get("Alice", set())

        # 5. Bob exercises viewing Alice's room in MainChatWindow
        bob_win.open_private_room("Alice", select=True)

        # 6. ChatServer receives Bob's READ_ACK, verifies ownership, and routes to Alice
        alice_ctrl.poll_events()

        # 7. Alice's MessageBubble label is now updated to 'Read ✓✓'
        updated_text = alice_bubble.status_label.cget("text")
        assert "Read ✓✓" in updated_text
    finally:
        root.destroy()
