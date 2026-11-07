"""Behavior tests for the GUI's headless ClientSession bridge."""

from __future__ import annotations

import os
from collections import deque
from typing import Any

import pytest

from gui import GuiController, MainChatWindow, configure_fixed_pixel_scaling
from protocol import (
    EVENT_GROUP_ERROR,
    EVENT_GROUP_LIST,
    EVENT_GROUP_MSG,
    EVENT_PRIVATE_MSG,
    EVENT_PRIVATE_MSG_ERROR,
    EVENT_READ_ACK,
    EVENT_REGISTER_ACK,
    EVENT_USER_LIST,
    GroupErrorPayload,
    GroupListPayload,
    GroupMessageServerPayload,
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
        self.registered: list[tuple[str, str]] = []
        self.sent_private: list[tuple[str, str, str | None, str | None]] = []
        self.sent_read_ack: list[tuple[str, str]] = []
        self.created_groups: list[str] = []
        self.joined_groups: list[str] = []
        self.sent_group: list[tuple[str, str, str | None, str | None]] = []
        self.connect_result = connect_result
        self.send_read_ack_result = send_read_ack_result

    def connect(self, server_url: str) -> bool:
        self.connected_to.append(server_url)
        return self.connect_result

    def register(self, username: str, avatar: str = "rabbit") -> bool:
        self.registered.append((username, avatar))
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

    def create_group(self, group_name: str) -> bool:
        self.created_groups.append(group_name)
        return True

    def join_group(self, group_name: str) -> bool:
        self.joined_groups.append(group_name)
        return True

    def send_group_message(
        self,
        group_name: str,
        message: str,
        message_id: str | None = None,
        timestamp: str | None = None,
    ) -> str | None:
        self.sent_group.append((group_name, message, message_id, timestamp))
        return message_id or "MSG_G_TEST"

    def get_next_event(self, *, block: bool = False) -> tuple[str, Any] | None:
        del block
        return self.events.popleft() if self.events else None


class ScalingSpy:
    def __init__(self) -> None:
        self.automatic_dpi_awareness_disabled = False
        self.widget_scaling: float | None = None
        self.window_scaling: float | None = None

    def deactivate_automatic_dpi_awareness(self) -> None:
        self.automatic_dpi_awareness_disabled = True

    def set_widget_scaling(self, value: float) -> None:
        self.widget_scaling = value

    def set_window_scaling(self, value: float) -> None:
        self.window_scaling = value


def test_fixed_pixel_scaling_disables_automatic_dpi_resizing():
    ui = ScalingSpy()

    configure_fixed_pixel_scaling(ui)

    assert ui.automatic_dpi_awareness_disabled is True
    assert ui.widget_scaling == 1.0
    assert ui.window_scaling == 1.0


def test_controller_connects_then_registers_the_requested_user():
    session = SessionSpy()
    controller = GuiController(session)

    assert controller.connect_and_register("127.0.0.1", "5000", "Alice") is True
    assert session.connected_to == ["http://127.0.0.1:5000"]
    assert session.registered == [("Alice", "rabbit")]


def test_controller_preserves_invalid_username_for_clientsession_validation():
    session = SessionSpy()
    controller = GuiController(session)

    controller.connect_and_register("127.0.0.1", "5000", " Alice ")

    assert session.registered == [(" Alice ", "rabbit")]


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


def test_controller_keeps_each_online_users_selected_character():
    session = SessionSpy(
        [
            (
                EVENT_USER_LIST,
                UserListPayload(users=["Alice", "Bob"], avatars={"Alice": "cat", "Bob": "dog"}),
            )
        ]
    )
    controller = GuiController(session)

    controller.poll_events()

    assert controller.online_avatars == {"Alice": "cat", "Bob": "dog"}


def test_controller_tracks_groups_and_forwards_explicit_create_and_join_actions():
    session = SessionSpy(
        [
            (
                EVENT_GROUP_LIST,
                GroupListPayload.from_dict(
                    {"groups": [{"group_name": "StudyGroup", "members": ["Alice"]}]}
                ),
            )
        ]
    )
    controller = GuiController(session)

    controller.poll_events()

    assert controller.groups == [{"group_name": "StudyGroup", "members": ["Alice"]}]
    assert controller.create_group("StudyGroup") is True
    assert controller.join_group("StudyGroup") is True
    assert session.created_groups == ["StudyGroup"]
    assert session.joined_groups == ["StudyGroup"]


def test_controller_exposes_a_group_error_for_sidebar_rendering():
    session = SessionSpy(
        [(EVENT_GROUP_ERROR, GroupErrorPayload(error="Group name already exists"))]
    )
    controller = GuiController(session)

    controller.poll_events()

    assert controller.group_error == "Group name already exists"


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


def test_open_private_room_reuses_a_tab_left_by_an_interrupted_initialization(monkeypatch):
    """A retry must not ask CTkTabview to create the same named tab twice."""

    class FrameSpy:
        def __init__(self) -> None:
            self.packed = False

        def pack(self, **_options: object) -> None:
            self.packed = True

    class RoomSpy:
        def __init__(self, _parent: object, _user: str, on_send: object) -> None:
            assert callable(on_send)
            self.frame = FrameSpy()

    class TabsSpy:
        def __init__(self) -> None:
            self._tab_dict = {"s": object()}
            self.added: list[str] = []

        def tab(self, name: str) -> object:
            return self._tab_dict[name]

        def add(self, name: str) -> object:
            self.added.append(name)
            raise AssertionError("duplicate tab creation")

    monkeypatch.setattr("gui.ChatRoomTab", RoomSpy)
    window = object.__new__(MainChatWindow)
    window.controller = type("Controller", (), {"username": "Alice"})()
    window.tabs = TabsSpy()
    window.rooms = {}

    window.open_private_room("s", select=False)

    assert window.tabs.added == []
    assert window.rooms["s"].frame.packed is True


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


def test_controller_send_group_message_forwards_to_session():
    session = SessionSpy()
    controller = GuiController(session)

    msg_id = controller.send_group_message("StudyGroup", "Hello all!")
    assert msg_id == "MSG_G_TEST"
    assert session.sent_group == [("StudyGroup", "Hello all!", None, None)]


def test_controller_polls_and_records_incoming_group_messages():
    meta = MessageMeta(message_id="MSG_G01", timestamp="14:32")
    payload = GroupMessageServerPayload(
        sender="Bob",
        group_name="StudyGroup",
        message="Hello group!",
        meta=meta,
    )
    session = SessionSpy([(EVENT_GROUP_MSG, payload)])
    controller = GuiController(session)
    controller.username = "Alice"

    received: list[tuple[str, GroupMessageServerPayload]] = []
    controller.on_group_message = lambda gname, p: received.append((gname, p))

    controller.poll_events()

    assert "StudyGroup" in controller.group_messages
    assert controller.group_messages["StudyGroup"] == [payload]
    assert len(received) == 1
    assert received[0] == ("StudyGroup", payload)


def test_controller_polls_and_records_own_echo_group_messages():
    meta = MessageMeta(message_id="MSG_G02", timestamp="14:33")
    payload = GroupMessageServerPayload(
        sender="Alice",
        group_name="StudyGroup",
        message="My message!",
        meta=meta,
    )
    session = SessionSpy([(EVENT_GROUP_MSG, payload)])
    controller = GuiController(session)
    controller.username = "Alice"

    controller.poll_events()

    assert "StudyGroup" in controller.group_messages
    assert controller.group_messages["StudyGroup"] == [payload]


def test_controller_polls_and_notifies_group_message_error():
    payload = GroupErrorPayload(error="User 'Charlie' is not a member of group 'StudyGroup'")
    session = SessionSpy([(EVENT_GROUP_ERROR, payload)])
    controller = GuiController(session)

    errors: list[tuple[str, str]] = []
    controller.on_group_message_error = lambda gname, err: errors.append((gname, err))

    controller.poll_events()

    assert controller.group_error == "User 'Charlie' is not a member of group 'StudyGroup'"
    assert len(errors) == 1
    assert "not a member" in errors[0][1]


def test_open_group_room_sets_up_chat_room_tab_and_on_send(monkeypatch):
    class FrameSpy:
        def __init__(self) -> None:
            self.packed = False

        def pack(self, **_options: object) -> None:
            self.packed = True

    class RoomSpy:
        def __init__(self, _parent: object, room_name: str, on_send: object) -> None:
            assert callable(on_send)
            self.room_name = room_name
            self.on_send = on_send
            self.frame = FrameSpy()

    class TabsSpy:
        def __init__(self) -> None:
            self._tab_dict: dict[str, object] = {}
            self.current_tab: str | None = None

        def add(self, name: str) -> object:
            tab = object()
            self._tab_dict[name] = tab
            return tab

        def tab(self, name: str) -> object:
            return self._tab_dict[name]

        def set(self, name: str) -> None:
            self.current_tab = name

    monkeypatch.setattr("gui.ChatRoomTab", RoomSpy)
    session = SessionSpy()
    controller = GuiController(session)
    controller.username = "Alice"

    window = object.__new__(MainChatWindow)
    window.controller = controller
    window.tabs = TabsSpy()
    window.rooms = {}
    window._active_room = None

    window.open_group_room("StudyGroup", select=True)

    assert "StudyGroup" in window.rooms
    assert window.rooms["StudyGroup"].frame.packed is True
    assert window.tabs.current_tab == "StudyGroup"
    assert window._active_room == "StudyGroup"

    # Verify sending forwards to controller and session
    window.rooms["StudyGroup"].on_send("Hello test group!")
    assert session.sent_group == [("StudyGroup", "Hello test group!", None, None)]


def test_main_chat_window_renders_and_deduplicates_incoming_group_message():
    class DummyRoom:
        def __init__(self) -> None:
            self.messages: list[dict[str, Any]] = []

        def add_message(
            self,
            sender: str,
            message: str,
            own: bool = False,
            timestamp: str | None = None,
            message_id: str | None = None,
        ):
            self.messages.append(
                {
                    "sender": sender,
                    "message": message,
                    "own": own,
                    "timestamp": timestamp,
                    "message_id": message_id,
                }
            )

    window = object.__new__(MainChatWindow)
    window.controller = type("Controller", (), {"username": "Alice"})()
    window.rooms = {"StudyGroup": DummyRoom()}
    window._rendered_messages = set()

    meta = MessageMeta(message_id="MSG_G10", timestamp="14:40")
    payload = GroupMessageServerPayload(
        sender="Bob",
        group_name="StudyGroup",
        message="Group chat works!",
        meta=meta,
    )

    # First delivery
    window._handle_incoming_group_message("StudyGroup", payload)
    assert len(window.rooms["StudyGroup"].messages) == 1
    msg = window.rooms["StudyGroup"].messages[0]
    assert msg["sender"] == "Bob"
    assert msg["message"] == "Group chat works!"
    assert msg["own"] is False
    assert msg["message_id"] == "MSG_G10"

    # Duplicate delivery with same message_id must be ignored
    window._handle_incoming_group_message("StudyGroup", payload)
    assert len(window.rooms["StudyGroup"].messages) == 1


def test_main_chat_window_renders_own_echo_group_message():
    class DummyRoom:
        def __init__(self) -> None:
            self.messages: list[dict[str, Any]] = []

        def add_message(
            self,
            sender: str,
            message: str,
            own: bool = False,
            timestamp: str | None = None,
            message_id: str | None = None,
        ):
            self.messages.append(
                {
                    "sender": sender,
                    "message": message,
                    "own": own,
                    "timestamp": timestamp,
                    "message_id": message_id,
                }
            )

    window = object.__new__(MainChatWindow)
    window.controller = type("Controller", (), {"username": "Alice"})()
    window.rooms = {"StudyGroup": DummyRoom()}
    window._rendered_messages = set()

    meta = MessageMeta(message_id="MSG_G11", timestamp="14:41")
    payload = GroupMessageServerPayload(
        sender="Alice",
        group_name="StudyGroup",
        message="My own group message",
        meta=meta,
    )

    window._handle_incoming_group_message("StudyGroup", payload)
    assert len(window.rooms["StudyGroup"].messages) == 1
    msg = window.rooms["StudyGroup"].messages[0]
    assert msg["sender"] == "You"
    assert msg["own"] is True
    assert msg["message"] == "My own group message"


class DirectSession:
    """In-memory session adapter connecting GuiController directly to ChatServer."""

    def __init__(self, server: ChatServer, sid: str, username: str) -> None:
        self.server = server
        self.sid = sid
        self.username = username
        self.events: deque[tuple[str, Any]] = deque()
        self.is_connected = True
        self.is_registered = True

    def disconnect_socket(self) -> None:
        self.is_connected = False
        self.is_registered = False
        self.server.sio._trigger_event("disconnect", "/", self.sid)

    def reconnect_socket(self, new_sid: str) -> None:
        self.sid = new_sid
        self.is_connected = True
        self.is_registered = False
        self.server.sio._trigger_event("connect", "/", self.sid, {})

    def reregister(self) -> None:
        payload = {"username": self.username}
        self.server.sio._trigger_event("register", "/", self.sid, payload)

    def send_private_message(
        self,
        target: str,
        message: str,
        message_id: str | None = None,
        timestamp: str | None = None,
    ) -> str | None:
        if not self.is_connected or not self.is_registered:
            return None
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
        if not self.is_connected or not self.is_registered:
            return False
        payload = {"message_id": message_id, "target": target}
        self.server.sio._trigger_event("read_ack", "/", self.sid, payload)
        return True

    def get_next_event(self, *, block: bool = False) -> tuple[str, Any] | None:
        return self.events.popleft() if self.events else None


@pytest.mark.skipif(
    os.environ.get("RUN_GUI_TESTS") != "1", reason="set RUN_GUI_TESTS=1 with a graphical display"
)
def test_main_window_retries_pending_read_acks_on_reconnect():
    """Verify pending ACKs are retried only after re-registration with new SID (SP4)."""
    try:
        import customtkinter as ctk
    except ImportError:
        pytest.skip("customtkinter is required for UI tests")

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

        # Setup Alice and Bob connected to ChatServer
        alice_sess = DirectSession(server, "sid_alice", "Alice")
        bob_sess = DirectSession(server, "sid_bob_1", "Bob")
        sessions["sid_alice"] = alice_sess
        sessions["sid_bob_1"] = bob_sess

        server.engine.register_user("sid_alice", "Alice")
        server.engine.register_user("sid_bob_1", "Bob")

        alice_ctrl = GuiController(alice_sess)
        alice_ctrl.username = "Alice"
        alice_ctrl.online_users = ["Alice", "Bob"]
        alice_win = MainChatWindow(root, alice_ctrl)
        alice_win.open_private_room("Bob", select=True)

        bob_ctrl = GuiController(bob_sess)
        bob_ctrl.username = "Bob"
        bob_ctrl.online_users = ["Alice", "Bob"]
        bob_win = MainChatWindow(root, bob_ctrl)

        # 1. Alice sends message to Bob
        alice_win.rooms["Bob"].on_send("Hello Bob!")
        alice_ctrl.poll_events()
        alice_bubble = alice_win.rooms["Bob"].bubbles["MSG_001"]
        assert "Sent" in alice_bubble.status_label.cget("text")

        # 2. Bob receives message while Alice tab is unselected -> queued in unread
        bob_ctrl.poll_events()
        assert "MSG_001" in bob_win._unread_messages.get("Alice", set())

        # 3. Network drops: Bob disconnects from server
        bob_sess.disconnect_socket()
        assert not server.engine.is_registered("sid_bob_1")

        # 4. Bob reconnects with a new Socket.IO SID 'sid_bob_2' (unregistered yet)
        sessions["sid_bob_2"] = bob_sess
        bob_sess.reconnect_socket("sid_bob_2")
        assert bob_sess.is_connected is True
        assert bob_sess.is_registered is False
        assert not server.engine.is_registered("sid_bob_2")

        # Unregistered SID failure mode: server rejects ACK attempt
        res_unreg = server.engine.route_read_ack("sid_bob_2", "MSG_001", "Alice")
        assert res_unreg.success is False
        assert res_unreg.error == "Reader not registered"

        # Bob views Alice's room while pending registration: ACK cannot send yet, retained
        bob_win.open_private_room("Alice", select=True)
        assert "MSG_001" in bob_win._unread_messages.get("Alice", set())
        assert "MSG_001" not in bob_win._acknowledged_messages

        # 5. Bob re-registers with new SID 'sid_bob_2'
        bob_sess.reregister()
        assert server.engine.is_registered("sid_bob_2")
        bob_sess.is_registered = True

        # 6. Bob polls events: receives register_ack -> triggers _retry_pending_acks
        bob_ctrl.poll_events()
        assert "MSG_001" in bob_win._acknowledged_messages
        assert "MSG_001" not in bob_win._unread_messages.get("Alice", set())

        # 7. Alice polls events: receives verified READ_ACK routed from sid_bob_2
        alice_ctrl.poll_events()
        assert "Read ✓✓" in alice_bubble.status_label.cget("text")
    finally:
        root.destroy()


@pytest.mark.skipif(
    os.environ.get("RUN_GUI_TESTS") != "1", reason="set RUN_GUI_TESTS=1 with a graphical display"
)
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
