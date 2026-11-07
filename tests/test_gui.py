"""Behavior tests for the GUI's headless ClientSession bridge."""

from __future__ import annotations

import os
from collections import deque
from typing import Any

import pytest

from gui import GuiController, MainChatWindow, configure_fixed_pixel_scaling
from protocol import (
    EVENT_ANNOUNCEMENT,
    EVENT_GROUP_ERROR,
    EVENT_GROUP_LIST,
    EVENT_PRIVATE_MSG,
    EVENT_PRIVATE_MSG_ERROR,
    EVENT_READ_ACK,
    EVENT_REGISTER_ACK,
    EVENT_USER_LIST,
    AnnouncementPayload,
    GroupErrorPayload,
    GroupListPayload,
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
