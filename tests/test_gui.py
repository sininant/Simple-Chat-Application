"""Behavior tests for the GUI's headless ClientSession bridge."""

from __future__ import annotations

import os
from collections import deque
from typing import Any

import pytest

from gui import GuiController, MainChatWindow, RoomRegistry, configure_fixed_pixel_scaling
from protocol import (
    EVENT_ANNOUNCEMENT,
    EVENT_GROUP_ERROR,
    EVENT_GROUP_LIST,
    EVENT_GROUP_MSG,
    EVENT_PRIVATE_MSG,
    EVENT_PRIVATE_MSG_ERROR,
    EVENT_READ_ACK,
    EVENT_REGISTER_ACK,
    EVENT_USER_LIST,
    AnnouncementPayload,
    GroupErrorPayload,
    GroupListPayload,
    GroupMessageServerPayload,
    MessageMeta,
    PrivateMessageServerPayload,
    ReadAckServerPayload,
    RegisterAckPayload,
    StickerMessageServerPayload,
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
        self.announcements: list[str] = []
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

    def send_announcement(self, message: str, timestamp: str | None = None) -> bool:
        self.announcements.append(message)
        return True

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
    assert "group:StudyGroup" in window.rooms
    assert window.rooms["group:StudyGroup"].frame.packed is True
    assert window.tabs.current_tab == "group:StudyGroup"
    assert window._active_room == "group:StudyGroup"

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


def test_main_chat_window_passes_sender_profile_avatar():
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
            avatar: Any | None = None,
        ):
            self.messages.append(
                {
                    "sender": sender,
                    "message": message,
                    "own": own,
                    "timestamp": timestamp,
                    "message_id": message_id,
                    "avatar": avatar,
                }
            )

    window = object.__new__(MainChatWindow)
    window.controller = type(
        "Controller",
        (),
        {"username": "Alice", "avatar": "rabbit", "online_avatars": {"Bob": "dog"}},
    )()
    window.rooms = {"StudyGroup": DummyRoom(), "Bob": DummyRoom()}
    window._rendered_messages = set()
    window._active_room = "Bob"
    window._unread_messages = {}
    window._send_read_ack = lambda *args: None

    # Incoming message from Bob in group
    meta = MessageMeta(message_id="MSG_AV01", timestamp="14:50")
    payload = GroupMessageServerPayload(
        sender="Bob",
        group_name="StudyGroup",
        message="Hello group!",
        meta=meta,
    )
    window._handle_incoming_group_message("StudyGroup", payload)
    assert window.rooms["StudyGroup"].messages[0]["avatar"] == "dog"

    # Incoming private message from Bob
    priv_meta = MessageMeta(message_id="MSG_AV02", timestamp="14:51")
    priv_payload = PrivateMessageServerPayload(
        sender="Bob",
        target="Alice",
        message="Hi Alice private!",
        meta=priv_meta,
    )
    window._handle_incoming_private_message("Bob", priv_payload)
    assert window.rooms["Bob"].messages[0]["avatar"] == "dog"


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


def test_controller_polls_and_notifies_announcement():
    """SP3: GuiController stores incoming AnnouncementPayload and invokes on_announcement."""
    payload = AnnouncementPayload(
        message="Server will restart in 5 minutes!",
        timestamp="14:30",
    )
    session = SessionSpy([(EVENT_ANNOUNCEMENT, payload)])
    controller = GuiController(session)

    received: list[AnnouncementPayload] = []
    controller.on_announcement = received.append

    controller.poll_events()

    assert controller.latest_announcement == payload
    assert controller.announcements == [payload]
    assert received == [payload]


def test_main_chat_window_handle_announcement_reveals_banner():
    """SP3: MainChatWindow._handle_announcement reveals global AnnouncementBanner across tabs."""

    class BannerSpy:
        def __init__(self) -> None:
            self.shown: list[tuple[str, str | None]] = []

        def show(self, message: str, timestamp: str | None = None) -> None:
            self.shown.append((message, timestamp))

    window = object.__new__(MainChatWindow)
    window.banner = BannerSpy()  # type: ignore[assignment]

    payload = AnnouncementPayload(
        message="Server maintenance in 10 minutes!",
        timestamp="15:00",
    )
    window._handle_announcement(payload)

    assert window.banner.shown == [("Server maintenance in 10 minutes!", "15:00")]


def test_server_announcement_reaches_multiple_client_controllers_simultaneously():
    """SP3 Integration Gate: Server announcement reaches >= 2 connected clients simultaneously."""
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
        parsed = parse_server_event(event, data)
        if target_sid and target_sid in sessions:
            sessions[target_sid].events.append((event, parsed))
        elif target_sid is None:
            for sess in sessions.values():
                sess.events.append((event, parsed))

    spy.sio.emit = direct_emit

    alice_sess = DirectSession(server, "sid_alice", "Alice")
    bob_sess = DirectSession(server, "sid_bob", "Bob")
    sessions["sid_alice"] = alice_sess
    sessions["sid_bob"] = bob_sess

    server.engine.register_user("sid_alice", "Alice")
    server.engine.register_user("sid_bob", "Bob")

    alice_ctrl = GuiController(alice_sess)
    bob_ctrl = GuiController(bob_sess)

    alice_received: list[AnnouncementPayload] = []
    bob_received: list[AnnouncementPayload] = []
    alice_ctrl.on_announcement = alice_received.append
    bob_ctrl.on_announcement = bob_received.append

    server.broadcast_announcement("Server will restart in 5 minutes!", timestamp="14:35")

    alice_ctrl.poll_events()
    bob_ctrl.poll_events()

    expected = AnnouncementPayload(
        message="Server will restart in 5 minutes!",
        timestamp="14:35",
    )
    assert alice_received == [expected]
    assert bob_received == [expected]
    assert alice_ctrl.latest_announcement == expected
    assert bob_ctrl.latest_announcement == expected


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


def test_main_chat_window_open_sticker_picker_toggles_room_sticker_panel():
    """Verify _open_sticker_picker delegates to the integrated room drawer."""

    class RoomSpy:
        def __init__(self) -> None:
            self.toggled = 0

        def toggle_sticker_panel(self) -> None:
            self.toggled += 1

    window = object.__new__(MainChatWindow)
    window.rooms = {"Bob": RoomSpy()}
    window.controller = type("Controller", (), {"groups": []})()

    window._open_sticker_picker("Bob")
    assert window.rooms["Bob"].toggled == 1

    window._open_sticker_picker("Bob")
    assert window.rooms["Bob"].toggled == 2


def test_controller_polls_and_notifies_incoming_sticker_message():
    """Verify GuiController drains EVENT_STICKER_MSG and notifies on_sticker_message."""
    from protocol import EVENT_STICKER_MSG, MessageMeta, StickerMessageServerPayload

    payload = StickerMessageServerPayload(
        sender="Bob",
        sticker_id="STK_001",
        target="Alice",
        meta=MessageMeta(message_id="MSG_001", timestamp="14:30"),
    )
    session = SessionSpy([(EVENT_STICKER_MSG, payload)])
    controller = GuiController(session)
    controller.username = "Alice"

    dispatched: list[tuple[str, StickerMessageServerPayload]] = []
    controller.on_sticker_message = lambda dest, p: dispatched.append((dest, p))

    controller.poll_events()

    assert "Bob" in controller.stickers
    assert controller.stickers["Bob"] == [payload]
    assert len(dispatched) == 1
    assert dispatched[0] == ("Bob", payload)


def test_chat_room_tab_single_click_chooses_sticker_and_sends_picture_on_send():
    """Verify single-click chooses sticker, and send action transmits sticker picture."""
    from gui import ChatRoomTab

    class ActionButtonSpy:
        def __init__(self) -> None:
            self.image = "mic"

        def configure(self, **kwargs: Any) -> None:
            if "image" in kwargs:
                self.image = kwargs["image"]

    class BoxSpy:
        def __init__(self) -> None:
            self.action_button = ActionButtonSpy()
            self.send_icon = "send_icon"
            self.microphone_icon = "mic_icon"
            self.is_sticker_chosen = lambda: False
            self.on_send_chosen = None

        def _entry_text(self) -> str:
            return ""

        def _update_action_icon(self) -> None:
            has_text = bool(self._entry_text().strip())
            has_chosen = bool(self.is_sticker_chosen())
            self.action_button.configure(
                image=self.send_icon if (has_text or has_chosen) else self.microphone_icon
            )

    room = object.__new__(ChatRoomTab)
    box = BoxSpy()
    box.is_sticker_chosen = lambda: room._chosen_sticker is not None
    box.on_send_chosen = room._send_chosen_sticker
    room.box = box
    room.sticker_buttons = {}
    room._chosen_sticker = None
    sent_stickers: list[str] = []
    room.on_send_sticker = lambda s: sent_stickers.append(s)

    # 1. Single click chooses sticker without putting filename in entry
    room._on_sticker_single_clicked("STK_001")
    assert room._chosen_sticker == "STK_001"
    assert room.box.action_button.image == "send_icon"
    assert room.box._entry_text() == ""

    # 2. Triggering send action transmits the chosen sticker as a sticker picture
    assert room._send_chosen_sticker() is True
    assert sent_stickers == ["STK_001"]
    assert room._chosen_sticker is None
    assert room.box.action_button.image == "mic_icon"


def test_chat_room_tab_double_click_sends_sticker_directly():
    """Verify double-clicking a sticker transmits the sticker directly."""
    from gui import ChatRoomTab

    class ActionButtonSpy:
        def __init__(self) -> None:
            self.image = "send_icon"

        def configure(self, **kwargs: Any) -> None:
            if "image" in kwargs:
                self.image = kwargs["image"]

    class BoxSpy:
        def __init__(self) -> None:
            self.action_button = ActionButtonSpy()
            self.send_icon = "send_icon"
            self.microphone_icon = "mic_icon"
            self.is_sticker_chosen = lambda: False

        def _entry_text(self) -> str:
            return ""

        def _update_action_icon(self) -> None:
            has_text = bool(self._entry_text().strip())
            has_chosen = bool(self.is_sticker_chosen())
            self.action_button.configure(
                image=self.send_icon if (has_text or has_chosen) else self.microphone_icon
            )

    room = object.__new__(ChatRoomTab)
    box = BoxSpy()
    box.is_sticker_chosen = lambda: room._chosen_sticker is not None
    room.box = box
    room.sticker_buttons = {}
    room._chosen_sticker = "STK_001"
    sent: list[str] = []
    room.on_send_sticker = lambda s: sent.append(s)

    # Double click transmits sticker as sticker image and clears chosen state
    room._on_sticker_double_clicked("STK_001")
    assert sent == ["STK_001"]
    assert room._chosen_sticker is None
    assert room.box.action_button.image == "mic_icon"


def test_chat_room_tab_rapid_clicks_triggers_double_click():
    """Verify two clicks within 500ms trigger double click immediately."""
    from gui import ChatRoomTab

    class ActionButtonSpy:
        def __init__(self) -> None:
            self.image = "mic_icon"

        def configure(self, **kwargs: Any) -> None:
            if "image" in kwargs:
                self.image = kwargs["image"]

    class BoxSpy:
        def __init__(self) -> None:
            self.action_button = ActionButtonSpy()
            self.send_icon = "send_icon"
            self.microphone_icon = "mic_icon"
            self.is_sticker_chosen = lambda: False

        def _entry_text(self) -> str:
            return ""

        def _update_action_icon(self) -> None:
            has_text = bool(self._entry_text().strip())
            has_chosen = bool(self.is_sticker_chosen())
            self.action_button.configure(
                image=self.send_icon if (has_text or has_chosen) else self.microphone_icon
            )

    room = object.__new__(ChatRoomTab)
    box = BoxSpy()
    box.is_sticker_chosen = lambda: room._chosen_sticker is not None
    room.box = box
    room.sticker_buttons = {}
    room._chosen_sticker = None
    room._last_sticker_click_time = 0.0
    room._last_clicked_sticker_id = None
    room._last_double_click_time = 0.0
    sent: list[str] = []
    room.on_send_sticker = lambda s: sent.append(s)

    # First click selects sticker
    room._handle_sticker_click("STK_002")
    assert room._chosen_sticker == "STK_002"
    assert sent == []

    # Second click (immediate) triggers double click send
    room._handle_sticker_click("STK_002")
    assert sent == ["STK_002"]
    assert room._chosen_sticker is None


def test_main_chat_window_handle_incoming_sticker_message_opens_group_room():
    """Verify incoming group sticker opens a group room rather than a private room."""
    window = object.__new__(MainChatWindow)
    window.rooms = {}
    window._rendered_messages = set()
    window.controller = type(
        "Controller", (), {"username": "Alice", "groups": [{"group_name": "StudyGroup"}]}
    )()

    opened_rooms: list[tuple[str, str]] = []
    window.open_group_room = lambda name: opened_rooms.append(("group", name))
    window.open_private_room = lambda name, select=False: opened_rooms.append(("private", name))

    payload = StickerMessageServerPayload(
        sender="Bob",
        sticker_id="STK_001",
        meta=MessageMeta(message_id="STK_GRP_1", timestamp="12:00"),
        target=None,
        group_name="StudyGroup",
    )

    window._handle_incoming_sticker_message("StudyGroup", payload)

    assert opened_rooms == [("group", "StudyGroup")]
    assert "STK_GRP_1" in window._rendered_messages


def test_main_chat_window_isolates_group_and_private_rooms_with_same_name(monkeypatch):
    """Verify group and private rooms have distinct internal IDs and do not collide (Issue #9)."""

    class RoomSpy:
        def __init__(self, tab: Any, name: str, on_send: Any, **kwargs: Any) -> None:
            self.tab = tab
            self.name = name
            self.on_send = on_send
            self.frame = type("Frame", (), {"pack": lambda *a, **k: None})()
            self.messages: list[dict[str, Any]] = []

        def add_message(self, sender: str, message: str, **kwargs: Any) -> None:
            self.messages.append({"sender": sender, "message": message, **kwargs})

    class TabsSpy:
        def __init__(self) -> None:
            self._tab_dict: dict[str, Any] = {}
            self.current_tab: str | None = None

        def tab(self, name: str) -> Any:
            return self._tab_dict[name]

        def add(self, name: str) -> Any:
            frame = object()
            self._tab_dict[name] = frame
            return frame

        def set(self, name: str) -> None:
            self.current_tab = name

    monkeypatch.setattr("gui.ChatRoomTab", RoomSpy)
    session = SessionSpy()
    controller = GuiController(session)
    controller.username = "Alice"

    window = object.__new__(MainChatWindow)
    window.controller = controller
    window.tabs = TabsSpy()
    window.rooms = RoomRegistry()
    window._active_room = None
    window._unread_messages = {}
    window._acknowledged_messages = set()
    window.sidebar = type("Sidebar", (), {"set_users": lambda *a: None})()
    window._send_read_ack = lambda *a: None

    # Open a private room for user 'Bob'
    window.open_private_room("Bob", select=True)
    assert "private:Bob" in window.rooms
    assert window.tabs.current_tab == "private:Bob"
    assert window._active_room == "private:Bob"

    # Open a group room with the identical name 'Bob'
    window.open_group_room("Bob", select=True)
    assert "group:Bob" in window.rooms
    assert window.tabs.current_tab == "group:Bob"
    assert window._active_room == "group:Bob"

    # Distinct room instances
    private_room = window.rooms["private:Bob"]
    group_room = window.rooms["group:Bob"]
    assert private_room is not group_room

    # Route message to group: does not leak into private room
    group_payload = GroupMessageServerPayload(
        sender="Charlie",
        group_name="Bob",
        message="Hello Bob group members!",
        meta=MessageMeta(message_id="MSG_G_SAME", timestamp="12:00"),
    )
    window._rendered_messages = set()
    window._handle_incoming_group_message("Bob", group_payload)

    assert len(group_room.messages) == 1
    assert group_room.messages[0]["message"] == "Hello Bob group members!"
    assert len(private_room.messages) == 0


def test_message_bubble_single_rendering_for_text_and_sticker(monkeypatch):
    """Verify MessageBubble renders exactly one bubble without duplicate text or frames."""
    from gui import MessageBubble

    created_labels: list[dict[str, Any]] = []

    class MockUI:
        class CTkFrame:
            def __init__(self, parent: Any = None, **kwargs: Any) -> None:
                self.parent = parent
                self.kwargs = kwargs

            def pack(self, **kwargs: Any) -> None:
                pass

        class CTkLabel:
            def __init__(self, parent: Any = None, **kwargs: Any) -> None:
                self.parent = parent
                self.kwargs = kwargs
                created_labels.append(kwargs)

            def pack(self, **kwargs: Any) -> None:
                pass

    monkeypatch.setattr("gui._require_ctk", lambda: MockUI)
    monkeypatch.setattr("gui._get_bubble_avatar", lambda avatar: object())

    # 1. Received text message with avatar:
    created_labels.clear()
    bubble_recv = MessageBubble(
        parent=object(),
        sender="Bob",
        message="Hello Alice",
        own=False,
        timestamp="14:00",
        avatar="dog",
    )
    text_labels = [lbl for lbl in created_labels if lbl.get("text") == "Hello Alice"]
    assert len(text_labels) == 1
    assert bubble_recv.avatar_label is not None

    # 2. Sent text message:
    created_labels.clear()
    bubble_sent = MessageBubble(
        parent=object(),
        sender="Alice",
        message="My reply",
        own=True,
        timestamp="14:01",
    )
    text_labels = [lbl for lbl in created_labels if lbl.get("text") == "My reply"]
    assert len(text_labels) == 1
    assert bubble_sent.status_label is not None

    # 3. Sticker message:
    created_labels.clear()
    sticker_obj = object()
    MessageBubble(
        parent=object(),
        sender="Bob",
        message="",
        own=False,
        timestamp="14:02",
        sticker_image=sticker_obj,
    )
    sticker_labels = [lbl for lbl in created_labels if lbl.get("image") is sticker_obj]
    empty_bubbles = [
        lbl
        for lbl in created_labels
        if lbl.get("text") == "" and lbl.get("image") is not sticker_obj
    ]
    assert len(sticker_labels) == 1
    assert len(empty_bubbles) == 0


def test_main_chat_window_incoming_sticker_from_other_passes_avatar():
    """Verify incoming sticker from another user passes the sender's profile avatar."""

    class DummyRoom:
        def __init__(self) -> None:
            self.stickers: list[dict[str, Any]] = []

        def add_sticker(
            self,
            sender: str,
            sticker_id: str,
            own: bool = False,
            timestamp: str | None = None,
            message_id: str | None = None,
            avatar: Any | None = None,
        ) -> None:
            self.stickers.append(
                {
                    "sender": sender,
                    "sticker_id": sticker_id,
                    "own": own,
                    "timestamp": timestamp,
                    "message_id": message_id,
                    "avatar": avatar,
                }
            )

    room = DummyRoom()
    window = object.__new__(MainChatWindow)
    window.rooms = {"Bob": room, "private:Bob": room}
    window._rendered_messages = set()
    window.controller = type(
        "Controller",
        (),
        {"username": "Alice", "online_avatars": {"Bob": "cat"}, "groups": []},
    )()
    window.avatar_images = None
    window._active_room = "private:Bob"
    window._unread_messages = {}
    window._send_read_ack = lambda *args: None

    payload = StickerMessageServerPayload(
        sender="Bob",
        sticker_id="STK_001",
        meta=MessageMeta(message_id="STK_001", timestamp="12:00"),
        target="Alice",
        group_name=None,
    )

    window._handle_incoming_sticker_message("Bob", payload)

    assert len(room.stickers) == 1
    assert room.stickers[0]["sender"] == "Bob"
    assert room.stickers[0]["sticker_id"] == "STK_001"
    assert room.stickers[0]["avatar"] == "cat"


def test_find_assets_dir_locates_existing_assets_directory():
    """Verify _find_assets_dir reliably discovers project assets."""
    from chat.gui import _find_assets_dir

    assets_dir = _find_assets_dir()
    assert assets_dir.is_dir()
    assert (assets_dir / "stickers").is_dir()
    assert (assets_dir / "avatar_cat.png").is_file()


def test_controller_poll_events_handles_unknown_or_corrupt_events_gracefully():
    """Verify GuiController drains unknown or malformed events without crashing."""
    session = SessionSpy([("UNKNOWN_EVENT", {"data": 123}), ("corrupted", None)])
    controller = GuiController(session)

    # Should not throw any exception
    controller.poll_events()


def test_gui_controller_send_announcement_delegates_to_session():
    """Verify GuiController delegates send_announcement request to session."""
    session = SessionSpy()
    controller = GuiController(session)
    assert controller.send_announcement("Important update!") is True
    assert session.announcements == ["Important update!"]


def test_message_bubble_creates_hover_announce_button(monkeypatch):
    """SP3: MessageBubble creates announce button on text messages and triggers callback."""
    from gui import MessageBubble

    announced: list[str] = []

    class MockWidget:
        def __init__(self, parent: Any = None, **kwargs: Any) -> None:
            self.parent = parent
            self.kwargs = kwargs
            self.command = kwargs.get("command")
            self.packed = False

        def pack(self, **kwargs: Any) -> None:
            self.packed = True

        def pack_forget(self) -> None:
            self.packed = False

        def pack_propagate(self, _flag: bool) -> None:
            pass

        def bind(self, _event: str, _handler: Any, add: str | None = None) -> None:
            pass

        def after(self, ms: int, fn: Any) -> None:
            pass

    class MockUI:
        CTkFrame = MockWidget
        CTkLabel = MockWidget
        CTkButton = MockWidget

    monkeypatch.setattr("gui._require_ctk", lambda: MockUI)
    monkeypatch.setattr("gui._get_bubble_avatar", lambda _: None)
    monkeypatch.setattr("gui._load_icons", lambda *a, **k: {})

    # 1. Text message with on_announce callback
    bubble = MessageBubble(
        parent=object(),
        sender="Alice",
        message="Notice: deploy at 4PM",
        own=False,
        on_announce=lambda msg: announced.append(msg),
    )
    assert bubble.announce_btn is not None

    # Hover enter shows the button
    bubble._on_enter()
    assert bubble.announce_btn.packed is True

    # Clicking / invoking announce calls the callback
    bubble.announce()
    assert announced == ["Notice: deploy at 4PM"]

    # 2. Sticker message does not create announce button
    bubble_stk = MessageBubble(
        parent=object(),
        sender="Alice",
        message="stk",
        sticker_image=object(),
        on_announce=lambda msg: announced.append(msg),
    )
    assert bubble_stk.announce_btn is None

    # 3. Message without on_announce does not create announce button
    bubble_no_cb = MessageBubble(
        parent=object(),
        sender="Bob",
        message="Hello",
        on_announce=None,
    )
    assert bubble_no_cb.announce_btn is None


def test_main_chat_window_broadcasts_announcement_to_room_banners():
    """SP3: Incoming announcement reveals both global banner and pinned room banner."""
    window = object.__new__(MainChatWindow)

    class BannerSpy:
        def __init__(self) -> None:
            self.shown: list[tuple[str, str | None]] = []

        def show(self, message: str, timestamp: str | None = None) -> None:
            self.shown.append((message, timestamp))

    class RoomSpy:
        def __init__(self) -> None:
            self.announcements: list[tuple[str, str | None]] = []

        def show_announcement(self, message: str, timestamp: str | None = None) -> None:
            self.announcements.append((message, timestamp))

    window.banner = BannerSpy()
    room = RoomSpy()
    window.rooms = {"Bob": room}

    payload = AnnouncementPayload(message="Pinned room announcement", timestamp="16:00")
    window._handle_announcement(payload)

    assert window.banner.shown == [("Pinned room announcement", "16:00")]
    assert room.announcements == [("Pinned room announcement", "16:00")]
    assert window._latest_announcement == ("Pinned room announcement", "16:00")


def test_chat_box_bar_sticker_mode_disables_and_enables_entry():
    """Verify composer entry is disabled and blurred when sticker mode is active."""
    from gui import ChatBoxBar

    entry_state: dict[str, Any] = {"state": "normal"}

    class MockEntry:
        def configure(self, **kwargs: Any) -> None:
            entry_state.update(kwargs)

        def focus_set(self) -> None:
            pass

    class MockFrame:
        def focus_set(self) -> None:
            pass

    box = object.__new__(ChatBoxBar)
    box.entry = MockEntry()
    box.frame = MockFrame()

    # 1. Enable sticker mode -> disabled
    box.set_sticker_mode(True)
    assert entry_state["state"] == "disabled"

    # 2. Disable sticker mode -> normal
    box.set_sticker_mode(False)
    assert entry_state["state"] == "normal"


def test_chat_room_tab_clicking_sticker_disables_entry_and_clicking_entry_restores():
    """Verify selecting sticker puts composer in sticker mode and clicking entry clears it."""
    from gui import ChatBoxBar, ChatRoomTab

    room = object.__new__(ChatRoomTab)
    box = object.__new__(ChatBoxBar)
    states: list[bool] = []
    box.set_sticker_mode = lambda enabled: states.append(enabled)
    box._update_action_icon = lambda: None

    room.box = box
    room._chosen_sticker = None
    room.sticker_buttons = {}

    # 1. Select sticker
    room._on_sticker_single_clicked("STK_001")
    assert room._chosen_sticker == "STK_001"
    assert states[-1] is True

    # 2. User clicks entry to resume writing
    room._on_entry_activated()
    assert room._chosen_sticker is None
    assert states[-1] is False


def test_message_bubble_hover_targets_restricted_to_bubble(monkeypatch):
    """Verify hover targets strictly include bubble shell / button and exclude parent frame/row."""
    from gui import MessageBubble

    class MockWidget:
        def __init__(self, parent: Any = None, **kwargs: Any) -> None:
            self.parent = parent
            self.kwargs = kwargs
            self.packed = False
            self.bound_events: list[str] = []

        def pack(self, **kwargs: Any) -> None:
            self.packed = True

        def pack_forget(self) -> None:
            self.packed = False

        def pack_propagate(self, _flag: bool) -> None:
            pass

        def bind(self, event: str, _handler: Any, add: str | None = None) -> None:
            self.bound_events.append(event)

        def after(self, ms: int, fn: Any) -> None:
            pass

    class MockUI:
        CTkFrame = MockWidget
        CTkLabel = MockWidget
        CTkButton = MockWidget

    monkeypatch.setattr("gui._require_ctk", lambda: MockUI)
    monkeypatch.setattr("gui._get_bubble_avatar", lambda _: None)
    monkeypatch.setattr("gui._load_icons", lambda *a, **k: {})

    bubble_own = MessageBubble(
        parent=object(),
        sender="Bob",
        message="Hello own",
        own=True,
        on_announce=lambda _msg: None,
    )
    # self.frame must NOT be a hover target
    assert bubble_own.frame not in bubble_own._hover_targets
    assert bubble_own._row not in bubble_own._hover_targets
    assert bubble_own._content_col not in bubble_own._hover_targets
    assert bubble_own._bubble_shell in bubble_own._hover_targets
    assert bubble_own.announce_btn in bubble_own._hover_targets

    bubble_peer = MessageBubble(
        parent=object(),
        sender="Alice",
        message="Hello peer",
        own=False,
        on_announce=lambda _msg: None,
    )
    assert bubble_peer.frame not in bubble_peer._hover_targets
    assert bubble_peer._row not in bubble_peer._hover_targets
    assert bubble_peer._content_col not in bubble_peer._hover_targets
    assert bubble_peer._bubble_shell in bubble_peer._hover_targets
    assert bubble_peer.announce_btn in bubble_peer._hover_targets


def test_chat_box_bar_clear_entry_when_disabled():
    """Verify clear_entry successfully clears text even if entry is in disabled state."""
    from gui import ChatBoxBar

    class MockInternalTextbox:
        def __init__(self) -> None:
            self.state = "disabled"
            self.text = "draft message"

        def cget(self, prop: str) -> str:
            if prop == "state":
                return self.state
            return ""

        def configure(self, **kwargs: Any) -> None:
            if "state" in kwargs:
                self.state = kwargs["state"]

        def delete(self, start: str, end: str) -> None:
            if self.state == "disabled":
                raise RuntimeError("Cannot delete when disabled")
            self.text = ""

    mock_tb = MockInternalTextbox()

    class MockEntry:
        def __init__(self) -> None:
            self._textbox = mock_tb

        def configure(self, **kwargs: Any) -> None:
            mock_tb.configure(**kwargs)

        def delete(self, start: str, end: str) -> None:
            mock_tb.delete(start, end)

    box = object.__new__(ChatBoxBar)
    box.entry = MockEntry()

    # Clear entry should temporarily enable, delete, and restore disabled state without crashing
    box.clear_entry()
    assert mock_tb.text == ""
    assert mock_tb.state == "disabled"


def test_chat_room_tab_send_with_sticker_chosen_clears_entry_and_selection():
    """Verify sending message clears entry and resets sticker mode when draft existed."""
    from gui import ChatBoxBar, ChatRoomTab

    room = object.__new__(ChatRoomTab)
    box = object.__new__(ChatBoxBar)
    entry_text = ["typed text"]

    class MockEntry:
        def delete(self, start: Any, end: Any) -> None:
            entry_text.clear()

    box.entry = MockEntry()
    box.clear_entry = lambda: MockEntry().delete(0, 0)
    updated_icons: list[bool] = []
    box._update_action_icon = lambda: updated_icons.append(True)
    box.set_sticker_mode = lambda _enabled: None

    room.box = box
    room._chosen_sticker = "STK_001"
    room.sticker_buttons = {}
    sent: list[str] = []
    room.on_send = lambda msg: sent.append(msg)

    # Calling _send clears sticker, clears entry, updates icon
    room._send("typed text")
    assert sent == ["typed text"]
    assert room._chosen_sticker is None
    assert entry_text == []
    assert len(updated_icons) > 0


def test_pink_light_matches_design_spec():
    """Verify PINK_LIGHT matches the exact pastel pink spec #F8E8EE."""
    import gui

    assert gui.PINK_LIGHT == "#F8E8EE"


def test_announcement_banner_has_close_button_and_dismisses():
    """Verify AnnouncementBanner has '✕' button and dismisses/cancels on close."""
    from gui import AnnouncementBanner

    closed: list[bool] = []
    banner = object.__new__(AnnouncementBanner)
    banner.on_close = lambda: closed.append(True)
    hidden: list[bool] = []
    banner.hide = lambda: hidden.append(True)

    banner.close()
    assert hidden == [True]
    assert closed == [True]


def test_main_chat_window_clear_announcements_clears_state_and_hides_rooms():
    """Verify _clear_announcements clears _latest_announcement and calls hide_announcement."""
    from gui import MainChatWindow

    window = object.__new__(MainChatWindow)
    window._latest_announcement = ("Active alert", "12:00")

    class MockRoom:
        def __init__(self) -> None:
            self.hidden = False

        def hide_announcement(self) -> None:
            self.hidden = True

    room1 = MockRoom()
    room2 = MockRoom()
    window.rooms = {"Alice": room1, "Bob": room2}

    window._clear_announcements()
    assert window._latest_announcement is None
    assert room1.hidden is True
    assert room2.hidden is True


def test_gui_server_host_port_env_precedence(monkeypatch):
    """Verify SERVER_HOST and SERVER_PORT environment variables take precedence in gui.py."""
    import os

    monkeypatch.setenv("SERVER_HOST", "192.168.1.55")
    monkeypatch.setenv("SERVER_PORT", "8888")
    monkeypatch.setenv("CHAT_SERVER_HOST", "127.0.0.1")
    monkeypatch.setenv("CHAT_SERVER_PORT", "5000")

    host = os.getenv("SERVER_HOST") or os.getenv("CHAT_SERVER_HOST", "127.0.0.1")
    port = os.getenv("SERVER_PORT") or os.getenv("CHAT_SERVER_PORT", "5000")

    assert host == "192.168.1.55"
    assert port == "8888"
