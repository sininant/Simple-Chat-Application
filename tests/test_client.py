"""Unit tests for headless ClientSession adapter (R2, R3, R4)."""

from typing import Any

import socketio

from client import ClientSession
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
    EVENT_SEND_STICKER,
    EVENT_STICKER_MSG,
    EVENT_USER_LIST,
    AnnouncementPayload,
    GroupErrorPayload,
    GroupListPayload,
    GroupMessageServerPayload,
    PrivateMessageServerPayload,
    ReadAckServerPayload,
    RegisterAckPayload,
    StickerMessageServerPayload,
    UserListPayload,
)


class SocketIOClientSpy:
    """Helper test double simulating socketio.Client without network dependencies."""

    def __init__(self) -> None:
        self.sio = socketio.Client(reconnection=False)
        self.emitted: list[tuple[str, Any]] = []
        self._connected: bool = False

        def spy_emit(event: str, data: Any = None, **kwargs: Any) -> Any:
            self.emitted.append((event, data))
            return None

        self.sio.emit = spy_emit  # type: ignore[assignment]

    def trigger(self, event: str, *args: Any) -> None:
        """Simulate an incoming event from the server to client callbacks."""
        self.sio._trigger_event(event, "/", *args)

    @property
    def connected(self) -> bool:
        return self._connected

    def set_connected(self, value: bool) -> None:
        self._connected = value


# -----------------------------------------------------------------------------
# Unit Tests for ClientSession
# -----------------------------------------------------------------------------


def test_client_session_initial_state():
    spy = SocketIOClientSpy()
    session = ClientSession(sio=spy.sio)

    assert session.is_connected is False
    assert session.is_registered is False
    assert session.username is None
    assert session.online_users == []
    assert session.sid is None
    assert session.get_next_event(block=False) is None


def test_client_session_connect_event_dispatched_to_queue():
    spy = SocketIOClientSpy()
    session = ClientSession(sio=spy.sio)

    spy.trigger("connect")

    assert session.is_connected is True
    event = session.get_next_event(block=False)
    assert event is not None
    assert event[0] == "connect"
    assert event[1] is None


def test_client_session_disconnect_event_cleans_state_and_enqueues():
    spy = SocketIOClientSpy()
    session = ClientSession(sio=spy.sio)

    spy.trigger("connect")
    _ = session.get_next_event(block=False)

    spy.trigger("disconnect")

    assert session.is_connected is False
    assert session.is_registered is False
    event = session.get_next_event(block=False)
    assert event is not None
    assert event[0] == "disconnect"


def test_client_session_register_emits_payload():
    spy = SocketIOClientSpy()
    session = ClientSession(sio=spy.sio)

    result = session.register("Alice")
    assert result is True

    assert len(spy.emitted) == 1
    event, data = spy.emitted[0]
    assert event == EVENT_REGISTER
    assert data == {"username": "Alice"}


def test_client_session_register_emits_selected_avatar():
    spy = SocketIOClientSpy()
    session = ClientSession(sio=spy.sio)

    assert session.register("Alice", "cat") is True

    assert spy.emitted == [(EVENT_REGISTER, {"username": "Alice", "avatar": "cat"})]


def test_client_session_register_rejects_unknown_avatar():
    spy = SocketIOClientSpy()
    session = ClientSession(sio=spy.sio)

    assert session.register("Alice", "unicorn") is False
    assert spy.emitted == []


def test_client_session_register_rejects_invalid_username():
    """Invalid username fails validation, enqueues failed ACK, and returns False without raising."""
    spy = SocketIOClientSpy()
    session = ClientSession(sio=spy.sio)

    # 1. Invalid username with spaces
    result = session.register("Alice with spaces")
    assert result is False
    assert len(spy.emitted) == 0

    event = session.get_next_event(block=False)
    assert event is not None
    event_name, payload = event
    assert event_name == EVENT_REGISTER_ACK
    assert isinstance(payload, RegisterAckPayload)
    assert payload.success is False
    assert payload.error is not None

    # 2. Empty username
    result_empty = session.register("")
    assert result_empty is False
    assert len(spy.emitted) == 0

    event_empty = session.get_next_event(block=False)
    assert event_empty is not None
    assert event_empty[0] == EVENT_REGISTER_ACK
    assert isinstance(event_empty[1], RegisterAckPayload)
    assert event_empty[1].success is False


def test_client_session_failed_reregistration_preserves_existing_user():
    """If already registered, a failed re-registration does not clear local identity."""
    spy = SocketIOClientSpy()
    session = ClientSession(sio=spy.sio)

    # First successful registration
    spy.trigger(
        EVENT_REGISTER_ACK,
        {"success": True, "username": "Alice", "message": "Registered successfully"},
    )
    assert session.is_registered is True
    assert session.username == "Alice"
    _ = session.get_next_event(block=False)

    # Failed subsequent registration attempt (e.g. duplicate username on server)
    spy.trigger(
        EVENT_REGISTER_ACK,
        {"success": False, "error": "Username already taken"},
    )
    # Local state must remain registered as Alice
    assert session.is_registered is True
    assert session.username == "Alice"

    event = session.get_next_event(block=False)
    assert event is not None
    assert event[0] == EVENT_REGISTER_ACK
    assert event[1].success is False


def test_client_session_register_ack_success():
    spy = SocketIOClientSpy()
    session = ClientSession(sio=spy.sio)

    ack_data = {"success": True, "username": "Alice", "message": "Registered successfully"}
    spy.trigger(EVENT_REGISTER_ACK, ack_data)

    assert session.is_registered is True
    assert session.username == "Alice"

    event = session.get_next_event(block=False)
    assert event is not None
    event_name, payload = event
    assert event_name == EVENT_REGISTER_ACK
    assert isinstance(payload, RegisterAckPayload)
    assert payload.success is True
    assert payload.username == "Alice"


def test_client_session_register_ack_failure():
    spy = SocketIOClientSpy()
    session = ClientSession(sio=spy.sio)

    ack_data = {"success": False, "error": "Username already taken"}
    spy.trigger(EVENT_REGISTER_ACK, ack_data)

    assert session.is_registered is False
    assert session.username is None

    event = session.get_next_event(block=False)
    assert event is not None
    event_name, payload = event
    assert event_name == EVENT_REGISTER_ACK
    assert isinstance(payload, RegisterAckPayload)
    assert payload.success is False
    assert payload.error == "Username already taken"


def test_client_session_user_list_updates_presence():
    spy = SocketIOClientSpy()
    session = ClientSession(sio=spy.sio)

    user_data = {"users": ["Alice", "Bob", "Charlie"]}
    spy.trigger(EVENT_USER_LIST, user_data)

    assert session.online_users == ["Alice", "Bob", "Charlie"]

    event = session.get_next_event(block=False)
    assert event is not None
    event_name, payload = event
    assert event_name == EVENT_USER_LIST
    assert isinstance(payload, UserListPayload)
    assert payload.users == ["Alice", "Bob", "Charlie"]


def test_client_session_tracks_group_list_and_emits_voluntary_group_actions():
    """R8-R10: typed group updates drive explicit create and self-join requests."""
    spy = SocketIOClientSpy()
    session = ClientSession(sio=spy.sio)
    spy.trigger("connect")
    _ = session.get_next_event(block=False)
    spy.trigger(
        EVENT_REGISTER_ACK,
        {"success": True, "username": "Alice", "message": "Registered successfully"},
    )
    _ = session.get_next_event(block=False)

    spy.trigger(
        EVENT_GROUP_LIST,
        {"groups": [{"group_name": "StudyGroup", "members": ["Alice"]}]},
    )

    assert session.groups == [{"group_name": "StudyGroup", "members": ["Alice"]}]
    queued = session.get_next_event(block=False)
    assert queued is not None
    assert queued[0] == EVENT_GROUP_LIST
    assert isinstance(queued[1], GroupListPayload)

    assert session.create_group("StudyGroup") is True
    assert session.join_group("StudyGroup") is True
    assert spy.emitted == [
        (EVENT_CREATE_GROUP, {"group_name": "StudyGroup"}),
        (EVENT_JOIN_GROUP, {"group_name": "StudyGroup"}),
    ]


def test_client_session_rejects_group_actions_when_disconnected():
    spy = SocketIOClientSpy()
    session = ClientSession(sio=spy.sio)

    assert session.create_group("StudyGroup") is False
    assert session.join_group("StudyGroup") is False
    assert spy.emitted == []


def test_client_session_parses_group_errors():
    spy = SocketIOClientSpy()
    session = ClientSession(sio=spy.sio)

    spy.trigger(EVENT_GROUP_ERROR, {"error": "Group name already exists"})

    queued = session.get_next_event(block=False)
    assert queued == (EVENT_GROUP_ERROR, GroupErrorPayload(error="Group name already exists"))


def test_client_session_catch_all_custom_events():
    """Verify custom/downstream events are passed through to the queue without error."""
    spy = SocketIOClientSpy()
    session = ClientSession(sio=spy.sio)

    spy.trigger("custom_server_event", {"some": "data"})

    event = session.get_next_event(block=False)
    assert event is not None
    assert event[0] == "custom_server_event"
    assert event[1] == {"some": "data"}


def test_client_session_get_next_event_empty_returns_none():
    session = ClientSession()
    assert session.get_next_event(block=False) is None
    assert session.get_next_event(block=True, timeout=0.01) is None


def test_client_session_send_private_message_fails_when_disconnected():
    spy = SocketIOClientSpy()
    session = ClientSession(sio=spy.sio)

    # Disconnected session must return None immediately
    assert session.is_connected is False
    assert session.send_private_message(target="Bob", message="Hello Bob!") is None
    assert len(spy.emitted) == 0


def test_client_session_send_private_message_emits_payload():
    spy = SocketIOClientSpy()
    session = ClientSession(sio=spy.sio)
    spy.trigger("connect")

    msg_id = session.send_private_message(
        target="Bob",
        message="Hello Bob!",
        message_id="MSG_CUSTOM",
        timestamp="14:31",
    )
    assert msg_id == "MSG_CUSTOM"
    assert len(spy.emitted) == 1
    event_name, data = spy.emitted[0]
    assert event_name == EVENT_PRIVATE_MSG
    assert data["target"] == "Bob"
    assert data["message"] == "Hello Bob!"
    assert data["message_id"] == "MSG_CUSTOM"
    assert data["timestamp"] == "14:31"


def test_client_session_send_private_message_auto_generates_id_and_timestamp():
    spy = SocketIOClientSpy()
    session = ClientSession(sio=spy.sio)
    spy.trigger("connect")

    msg_id = session.send_private_message(target="Bob", message="Auto values")
    assert msg_id is not None
    assert msg_id.startswith("MSG_")
    assert len(spy.emitted) == 1
    _, data = spy.emitted[0]
    assert data["target"] == "Bob"
    assert data["message"] == "Auto values"
    assert data["message_id"] == msg_id
    assert len(data["timestamp"]) == 5  # "HH:MM"


def test_client_session_send_private_message_validation_failure():
    spy = SocketIOClientSpy()
    session = ClientSession(sio=spy.sio)
    spy.trigger("connect")

    # Empty message
    assert session.send_private_message(target="Bob", message="") is None
    # Whitespace message
    assert session.send_private_message(target="Bob", message="   ") is None
    # Invalid target (special characters)
    assert session.send_private_message(target="Invalid@User", message="Hi") is None

    assert len(spy.emitted) == 0


def test_client_session_receives_private_message_event():
    spy = SocketIOClientSpy()
    session = ClientSession(sio=spy.sio)

    server_data = {
        "sender": "Alice",
        "target": "Bob",
        "message": "Hi there!",
        "message_id": "MSG_005",
        "timestamp": "14:35",
    }
    spy.trigger(EVENT_PRIVATE_MSG, server_data)

    event = session.get_next_event(block=False)
    assert event is not None
    event_name, payload = event
    assert event_name == EVENT_PRIVATE_MSG
    assert isinstance(payload, PrivateMessageServerPayload)
    assert payload.sender == "Alice"
    assert payload.target == "Bob"
    assert payload.message == "Hi there!"
    assert payload.meta.message_id == "MSG_005"
    assert payload.meta.timestamp == "14:35"


def test_client_session_receives_invalid_private_message_dropped():
    spy = SocketIOClientSpy()
    session = ClientSession(sio=spy.sio)

    # Malformed data missing sender
    spy.trigger(EVENT_PRIVATE_MSG, {"target": "Bob", "message": "No sender"})
    assert session.get_next_event(block=False) is None


def test_client_session_receives_private_message_error_event():
    spy = SocketIOClientSpy()
    session = ClientSession(sio=spy.sio)

    error_data = {"error": "User 'Bob' is offline", "target": "Bob"}
    spy.trigger(EVENT_PRIVATE_MSG_ERROR, error_data)

    event = session.get_next_event(block=False)
    assert event is not None
    assert event[0] == EVENT_PRIVATE_MSG_ERROR
    assert event[1] == error_data


def test_client_session_send_read_ack_when_connected():
    spy = SocketIOClientSpy()
    session = ClientSession(sio=spy.sio)
    spy.trigger("connect")

    assert session.send_read_ack("MSG_001", "Alice") is True
    assert len(spy.emitted) == 1
    event_name, data = spy.emitted[0]
    assert event_name == EVENT_READ_ACK
    assert data["message_id"] == "MSG_001"
    assert data["target"] == "Alice"


def test_client_session_send_read_ack_when_disconnected():
    spy = SocketIOClientSpy()
    session = ClientSession(sio=spy.sio)

    assert session.send_read_ack("MSG_001", "Alice") is False
    assert len(spy.emitted) == 0


def test_client_session_send_read_ack_validation_failure():
    spy = SocketIOClientSpy()
    session = ClientSession(sio=spy.sio)
    spy.trigger("connect")

    # Invalid message_id
    assert session.send_read_ack("", "Alice") is False
    assert session.send_read_ack("   ", "Alice") is False
    # Invalid target
    assert session.send_read_ack("MSG_001", "Invalid@User") is False
    assert len(spy.emitted) == 0


def test_client_session_receives_read_ack_event():
    spy = SocketIOClientSpy()
    session = ClientSession(sio=spy.sio)

    server_data = {
        "message_id": "MSG_001",
        "reader": "Bob",
    }
    spy.trigger(EVENT_READ_ACK, server_data)

    event = session.get_next_event(block=False)
    assert event is not None
    event_name, payload = event
    assert event_name == EVENT_READ_ACK
    assert isinstance(payload, ReadAckServerPayload)
    assert payload.message_id == "MSG_001"
    assert payload.reader == "Bob"


def test_client_session_auto_reregisters_on_reconnect_and_guards_read_ack():
    """Verify ClientSession automatically re-registers on reconnect and guards read_ack."""
    spy = SocketIOClientSpy()
    session = ClientSession(sio=spy.sio)

    # 1. Connect and register
    spy.trigger("connect")
    session.register("Alice")
    spy.trigger("register_ack", {"success": True, "username": "Alice"})
    assert session.is_registered is True
    assert session.username == "Alice"

    # 2. Connection drops
    spy.trigger("disconnect")
    assert session.is_connected is False
    assert session.is_registered is False
    assert session.username == "Alice"

    # 3. Guard: cannot send read_ack while disconnected
    assert session.send_read_ack("MSG_001", "Bob") is False

    # 4. Socket reconnects: connect handler triggers auto re-registration
    spy.emitted.clear()
    spy.trigger("connect")
    assert session.is_connected is True
    assert session.is_registered is False

    assert len(spy.emitted) == 1
    assert spy.emitted[0][0] == "register"
    assert spy.emitted[0][1]["username"] == "Alice"

    # Guarded while pending server register_ack
    assert session.send_read_ack("MSG_001", "Bob") is False

    # 5. Server acknowledges re-registration
    spy.trigger("register_ack", {"success": True, "username": "Alice"})
    assert session.is_registered is True

    # Now read_ack is permitted
    assert session.send_read_ack("MSG_001", "Bob") is True


def test_client_session_send_group_message_emits_payload():
    spy = SocketIOClientSpy()
    session = ClientSession(sio=spy.sio)

    spy.trigger("connect")
    session.register("Alice")
    spy.trigger("register_ack", {"success": True, "username": "Alice"})
    spy.emitted.clear()

    msg_id = session.send_group_message(
        "StudyGroup", "Hello everyone!", message_id="MSG_G01", timestamp="14:32"
    )
    assert msg_id == "MSG_G01"
    assert len(spy.emitted) == 1
    event, data = spy.emitted[0]
    assert event == EVENT_SEND_GROUP
    assert data["target"] == "StudyGroup"
    assert data["group_name"] == "StudyGroup"
    assert data["message"] == "Hello everyone!"
    assert data["message_id"] == "MSG_G01"
    assert data["timestamp"] == "14:32"


def test_client_session_send_group_message_disconnected_or_unregistered():
    spy = SocketIOClientSpy()
    session = ClientSession(sio=spy.sio)

    # Disconnected
    assert session.send_group_message("StudyGroup", "Hello") is None
    assert len(spy.emitted) == 0

    # Connected but not registered
    spy.trigger("connect")
    assert session.send_group_message("StudyGroup", "Hello") is None
    assert len(spy.emitted) == 0


def test_client_session_send_group_message_validation_failure():
    spy = SocketIOClientSpy()
    session = ClientSession(sio=spy.sio)

    spy.trigger("connect")
    session.register("Alice")
    spy.trigger("register_ack", {"success": True, "username": "Alice"})
    spy.emitted.clear()

    # Empty message
    assert session.send_group_message("StudyGroup", "   ") is None
    # Invalid group name (empty)
    assert session.send_group_message("", "Hello") is None
    assert len(spy.emitted) == 0


def test_client_session_receives_and_enqueues_group_message():
    spy = SocketIOClientSpy()
    session = ClientSession(sio=spy.sio)

    spy.trigger("connect")
    _ = session.get_next_event(block=False)

    msg_data = {
        "sender": "Bob",
        "group_name": "StudyGroup",
        "message": "Welcome all!",
        "message_id": "MSG_G02",
        "timestamp": "14:33",
    }
    spy.trigger(EVENT_GROUP_MSG, msg_data)

    event = session.get_next_event(block=False)
    assert event is not None
    event_type, payload = event
    assert event_type == EVENT_RECEIVE_GROUP
    assert isinstance(payload, GroupMessageServerPayload)
    assert payload.sender == "Bob"
    assert payload.group_name == "StudyGroup"
    assert payload.message == "Welcome all!"


def test_client_session_receives_and_enqueues_receive_group():
    spy = SocketIOClientSpy()
    session = ClientSession(sio=spy.sio)

    spy.trigger("connect")
    _ = session.get_next_event(block=False)

    msg_data = {
        "sender": "Bob",
        "group_name": "StudyGroup",
        "message": "Welcome via RECEIVE_GROUP!",
        "message_id": "MSG_G03",
        "timestamp": "14:34",
    }
    spy.trigger(EVENT_RECEIVE_GROUP, msg_data)

    event = session.get_next_event(block=False)
    assert event is not None
    event_type, payload = event
    assert event_type == EVENT_RECEIVE_GROUP
    assert isinstance(payload, GroupMessageServerPayload)
    assert payload.sender == "Bob"
    assert payload.group_name == "StudyGroup"
    assert payload.message == "Welcome via RECEIVE_GROUP!"


def test_client_session_send_sticker_private_and_group():
    """Verify session.send_sticker emits EVENT_SEND_STICKER for private and group destinations."""
    spy = SocketIOClientSpy()
    session = ClientSession(sio=spy.sio)

    spy.trigger("connect")
    session.register("Alice")
    spy.trigger("register_ack", {"success": True, "username": "Alice"})
    spy.emitted.clear()

    # 1. Private sticker destination
    msg_id1 = session.send_sticker(
        sticker_id="STK_001",
        target="Bob",
        message_id="STK_101",
        timestamp="14:00",
    )
    assert msg_id1 == "STK_101"
    assert len(spy.emitted) == 1
    event_name, data = spy.emitted[0]
    assert event_name == EVENT_SEND_STICKER
    assert data["sticker_id"] == "STK_001"
    assert data["target"] == "Bob"
    assert data["group_name"] is None
    assert data["message_id"] == "STK_101"
    assert data["timestamp"] == "14:00"

    # 2. Group sticker destination (with auto message_id and timestamp)
    spy.emitted.clear()
    msg_id2 = session.send_sticker(
        sticker_id="STK_002",
        group_name="StudyGroup",
    )
    assert msg_id2 is not None
    assert len(spy.emitted) == 1
    event_name, data = spy.emitted[0]
    assert event_name == EVENT_SEND_STICKER
    assert data["sticker_id"] == "STK_002"
    assert data["target"] is None
    assert data["group_name"] == "StudyGroup"
    assert data["message_id"] == msg_id2
    assert "timestamp" in data


def test_client_session_send_sticker_rejected_when_disconnected_or_unregistered():
    """Verify send_sticker returns None when disconnected or not yet registered."""
    spy = SocketIOClientSpy()
    session = ClientSession(sio=spy.sio)

    # Disconnected
    assert session.is_connected is False
    assert session.send_sticker("STK_001", target="Bob") is None
    assert len(spy.emitted) == 0

    # Connected but not registered
    spy.trigger("connect")
    assert session.is_connected is True
    assert session.is_registered is False
    assert session.send_sticker("STK_001", target="Bob") is None
    assert len(spy.emitted) == 0


def test_client_session_send_sticker_rejected_on_invalid_parameters():
    """Verify send_sticker returns None on invalid sticker_id or invalid destinations."""
    spy = SocketIOClientSpy()
    session = ClientSession(sio=spy.sio)

    spy.trigger("connect")
    session.register("Alice")
    spy.trigger("register_ack", {"success": True, "username": "Alice"})
    spy.emitted.clear()

    # Unknown sticker ID (not in allowed registry STK_001-STK_009)
    assert session.send_sticker("STK_999", target="Bob") is None
    # Malformed sticker ID
    assert session.send_sticker("INVALID", target="Bob") is None
    # Both target and group_name
    assert session.send_sticker("STK_001", target="Bob", group_name="StudyGroup") is None
    # Missing destination
    assert session.send_sticker("STK_001") is None
    # Invalid target characters
    assert session.send_sticker("STK_001", target="invalid@user") is None
    # Invalid group_name
    assert session.send_sticker("STK_001", group_name="   ") is None

    assert len(spy.emitted) == 0


def test_client_session_receives_sticker_message_event():
    """Verify incoming EVENT_STICKER_MSG is parsed into StickerMessageServerPayload and queued."""
    spy = SocketIOClientSpy()
    session = ClientSession(sio=spy.sio)

    server_data = {
        "sender": "Alice",
        "sticker_id": "STK_001",
        "target": "Bob",
        "group_name": None,
        "message_id": "STK_101",
        "timestamp": "12:00",
    }
    spy.trigger(EVENT_STICKER_MSG, server_data)

    event = session.get_next_event(block=False)
    assert event is not None
    event_name, payload = event
    assert event_name == EVENT_STICKER_MSG
    assert isinstance(payload, StickerMessageServerPayload)
    assert payload.sender == "Alice"
    assert payload.sticker_id == "STK_001"
    assert payload.target == "Bob"
    assert payload.group_name is None
    assert payload.meta.message_id == "STK_101"
    assert payload.meta.timestamp == "12:00"

    # Malformed incoming sticker event is ignored (does not queue invalid payload)
    spy.trigger(EVENT_STICKER_MSG, {"invalid": "missing fields"})
    assert session.get_next_event(block=False) is None


def test_client_session_receives_announcement_event():
    """SP3: ClientSession parses incoming server announcements and enqueues AnnouncementPayload."""
    spy = SocketIOClientSpy()
    session = ClientSession(sio=spy.sio)

    assert session.announcements == []

    spy.trigger(
        EVENT_ANNOUNCEMENT,
        {"message": "Server will restart in 5 minutes!", "timestamp": "14:30"},
    )

    event = session.get_next_event(block=False)
    assert event is not None
    event_name, payload = event
    assert event_name == EVENT_ANNOUNCEMENT
    assert isinstance(payload, AnnouncementPayload)
    assert payload.message == "Server will restart in 5 minutes!"
    assert payload.timestamp == "14:30"
    assert session.announcements == [payload]


def test_client_session_drops_invalid_announcement_event():
    """SP3: Malformed announcement payloads from server are dropped without crashing."""
    spy = SocketIOClientSpy()
    session = ClientSession(sio=spy.sio)

    spy.trigger(EVENT_ANNOUNCEMENT, {"message": "", "timestamp": "14:30"})
    assert session.get_next_event(block=False) is None
    assert session.announcements == []


def test_client_session_concurrent_read_write_thread_safety():
    """Verify ClientSession maintains thread-safe state access under concurrent usage."""
    import threading

    spy = SocketIOClientSpy()
    session = ClientSession(sio=spy.sio)
    errors: list[Exception] = []

    def writer():
        for i in range(100):
            try:
                spy.trigger("connect")
                session.register("Alice")
                spy.trigger("register_ack", {"success": True, "username": "Alice"})
                spy.trigger("user_list", {"users": ["Alice", f"User_{i}"]})
                spy.trigger("disconnect")
            except Exception as e:
                errors.append(e)

    def reader():
        for _ in range(100):
            try:
                _ = session.is_connected
                _ = session.is_registered
                _ = session.username
                _ = session.online_users
                _ = session.groups
                _ = session.get_next_event(block=False)
            except Exception as e:
                errors.append(e)

    threads = [
        threading.Thread(target=writer),
        threading.Thread(target=reader),
        threading.Thread(target=reader),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
