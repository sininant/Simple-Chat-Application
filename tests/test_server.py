"""Unit and integration tests for Socket.IO server and ChatEngine (R2, R3, R4)."""

import threading
import time
from typing import Any

import pytest
import socketio

from client import ClientSession
from protocol import (
    EVENT_CREATE_GROUP,
    EVENT_GROUP_ERROR,
    EVENT_GROUP_LIST,
    EVENT_JOIN_GROUP,
    EVENT_PRIVATE_MSG,
    EVENT_PRIVATE_MSG_ERROR,
    EVENT_READ_ACK,
    EVENT_REGISTER_ACK,
    EVENT_USER_LIST,
    MessageMeta,
)
from server import ChatEngine, ChatServer, ReadAckRouteResult, RouteResult

# -----------------------------------------------------------------------------
# Fixtures & Test Utilities
# -----------------------------------------------------------------------------


class SocketIOServerSpy:
    """Helper test double to record emissions and room operations from ChatServer."""

    def __init__(self) -> None:
        self.sio = socketio.Server(async_mode="threading")
        self.emissions: list[dict[str, Any]] = []
        self.entered_rooms: list[tuple[str, str]] = []
        self.left_rooms: list[tuple[str, str]] = []

        def spy_emit(
            event: str,
            data: Any = None,
            to: Any = None,
            room: Any = None,
            **kwargs: Any,
        ) -> Any:
            self.emissions.append({"event": event, "data": data, "room": room, "to": to})
            return None

        self.sio.emit = spy_emit  # type: ignore[assignment]

        def spy_enter_room(sid: str, room: str, namespace: str | None = None) -> None:
            del namespace
            self.entered_rooms.append((sid, room))

        self.sio.enter_room = spy_enter_room  # type: ignore[assignment]

        def spy_leave_room(sid: str, room: str, namespace: str | None = None) -> None:
            del namespace
            self.left_rooms.append((sid, room))

        self.sio.leave_room = spy_leave_room  # type: ignore[assignment]

    def trigger(self, event: str, sid: str, data: Any = None) -> None:
        """Trigger an inbound Socket.IO event handler."""
        if data is not None:
            self.sio._trigger_event(event, "/", sid, data)
        else:
            self.sio._trigger_event(event, "/", sid)

    def get_events(
        self,
        event_name: str | None = None,
        room: str | None = None,
    ) -> list[dict[str, Any]]:
        """Filter captured emissions by event name and target room."""
        res = self.emissions
        if event_name is not None:
            res = [e for e in res if e["event"] == event_name]
        if room is not None:
            res = [e for e in res if e["room"] == room]
        return res

    def clear(self) -> None:
        self.emissions.clear()


# -----------------------------------------------------------------------------
# 1. ChatEngine Unit Tests (Headless Domain State Machine)
# -----------------------------------------------------------------------------


def test_chat_engine_initial_state():
    engine = ChatEngine()
    assert engine.get_online_users() == []
    assert not engine.is_registered("sid_1")


def test_chat_engine_register_user_success():
    engine = ChatEngine()
    ack = engine.register_user("sid_1", "Alice")
    assert ack.success is True
    assert ack.username == "Alice"
    assert ack.message == "Registered successfully"
    assert ack.error is None

    assert engine.get_username("sid_1") == "Alice"
    assert engine.get_sid("Alice") == "sid_1"
    assert engine.is_registered("sid_1") is True
    assert engine.get_online_users() == ["Alice"]


def test_chat_engine_register_user_duplicate_rejected():
    engine = ChatEngine()
    ack1 = engine.register_user("sid_1", "Alice")
    assert ack1.success is True

    # Same username from another session must be rejected
    ack2 = engine.register_user("sid_2", "Alice")
    assert ack2.success is False
    assert ack2.error == "Username already taken"
    assert ack2.username is None

    # Alice remains mapped to sid_1
    assert engine.get_sid("Alice") == "sid_1"
    assert engine.get_username("sid_2") is None


def test_chat_engine_register_user_same_sid_reregister():
    engine = ChatEngine()
    ack1 = engine.register_user("sid_1", "Alice")
    assert ack1.success is True

    # Re-registering identical name from same sid succeeds
    ack2 = engine.register_user("sid_1", "Alice")
    assert ack2.success is True
    assert ack2.username == "Alice"

    # Changing username for same sid updates mappings cleanly
    ack3 = engine.register_user("sid_1", "Alice_New")
    assert ack3.success is True
    assert ack3.username == "Alice_New"
    assert engine.get_sid("Alice") is None
    assert engine.get_sid("Alice_New") == "sid_1"
    assert engine.get_online_users() == ["Alice_New"]


def test_chat_engine_register_user_invalid_formats():
    engine = ChatEngine()
    # Padded spaces
    ack = engine.register_user("sid_1", "  Alice  ")
    assert ack.success is False
    assert ack.error is not None

    # Invalid characters
    ack2 = engine.register_user("sid_1", "Alice!@#")
    assert ack2.success is False
    assert ack2.error is not None

    # Empty string
    ack3 = engine.register_user("sid_1", "")
    assert ack3.success is False


def test_chat_engine_unregister_user():
    engine = ChatEngine()
    engine.register_user("sid_1", "Alice")
    engine.register_user("sid_2", "Bob")

    assert engine.get_online_users() == ["Alice", "Bob"]

    # Unregister Alice
    unreg = engine.unregister_user("sid_1")
    assert unreg == "Alice"
    assert engine.get_online_users() == ["Bob"]
    assert engine.get_username("sid_1") is None
    assert engine.get_sid("Alice") is None

    # Unregistering unknown sid returns None
    assert engine.unregister_user("sid_unknown") is None

    # Freed username can now be registered by another sid
    ack = engine.register_user("sid_3", "Alice")
    assert ack.success is True
    assert engine.get_sid("Alice") == "sid_3"


def test_chat_engine_concurrency():
    """Verify thread-safe registration across simultaneous concurrent threads."""
    engine = ChatEngine()
    results: list[bool] = []
    lock = threading.Lock()

    def worker(idx: int):
        ack = engine.register_user(f"sid_{idx}", f"user_{idx}")
        with lock:
            results.append(ack.success)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 20
    assert all(results)
    assert len(engine.get_online_users()) == 20


def test_chat_engine_route_private_message():
    engine = ChatEngine()
    engine.register_user("sid_alice", "Alice")
    engine.register_user("sid_bob", "Bob")

    meta = MessageMeta(message_id="MSG_001", timestamp="14:31")

    # Valid route Alice -> Bob
    result = engine.route_private_message(
        sender_sid="sid_alice",
        target="Bob",
        message="Hi Bob!",
        meta=meta,
    )
    assert isinstance(result, RouteResult)
    assert result.success is True
    assert result.error is None
    assert result.target_sid == "sid_bob"
    assert result.payload is not None
    assert result.payload.sender == "Alice"
    assert result.payload.target == "Bob"
    assert result.payload.message == "Hi Bob!"
    assert result.payload.meta.message_id == "MSG_001"

    # Also supports tuple unpacking for backwards compatibility
    success, err, payload, target_sid = result
    assert success is True
    assert target_sid == "sid_bob"

    # Route from unregistered sender sid fails
    success, err, payload, target_sid = engine.route_private_message(
        sender_sid="sid_unknown",
        target="Bob",
        message="Hi",
        meta=meta,
    )
    assert success is False
    assert err == "Sender not registered"
    assert payload is None
    assert target_sid is None

    # Route to offline recipient fails
    success, err, payload, target_sid = engine.route_private_message(
        sender_sid="sid_alice",
        target="OfflineCharlie",
        message="Hi",
        meta=meta,
    )
    assert success is False
    assert err == "User 'OfflineCharlie' is offline"
    assert payload is None
    assert target_sid is None


def test_chat_engine_route_read_ack():
    engine = ChatEngine()
    engine.register_user("sid_alice", "Alice")
    engine.register_user("sid_bob", "Bob")
    engine.register_user("sid_charlie", "Charlie")

    # Alice sends message MSG_001 to Bob
    engine.route_private_message(
        sender_sid="sid_alice",
        target="Bob",
        message="Hello Bob",
        meta=MessageMeta(message_id="MSG_001", timestamp="12:00"),
    )

    # Bob reads Alice's message MSG_001
    result = engine.route_read_ack(
        reader_sid="sid_bob",
        message_id="MSG_001",
        target="Alice",
    )
    assert isinstance(result, ReadAckRouteResult)
    assert result.success is True
    assert result.error is None
    assert result.target_sid == "sid_alice"
    assert result.payload is not None
    assert result.payload.message_id == "MSG_001"
    assert result.payload.reader == "Bob"

    # Tuple unpacking support
    success, err, payload, target_sid = result
    assert success is True
    assert target_sid == "sid_alice"

    # Unregistered reader fails
    res_unreg = engine.route_read_ack(
        reader_sid="sid_unknown",
        message_id="MSG_001",
        target="Alice",
    )
    assert res_unreg.success is False
    assert res_unreg.error == "Reader not registered"
    assert res_unreg.payload is None

    # Unknown message fails (SP4 ownership verification)
    res_unknown = engine.route_read_ack(
        reader_sid="sid_bob",
        message_id="MSG_UNKNOWN",
        target="Alice",
    )
    assert res_unknown.success is False
    assert res_unknown.error == "Message 'MSG_UNKNOWN' not found"

    # Ownership mismatch: Alice sent MSG_002 to Charlie; Bob cannot ACK it
    engine.route_private_message(
        sender_sid="sid_alice",
        target="Charlie",
        message="Hello Charlie",
        meta=MessageMeta(message_id="MSG_002", timestamp="12:01"),
    )
    res_mismatch = engine.route_read_ack(
        reader_sid="sid_bob",
        message_id="MSG_002",
        target="Alice",
    )
    assert res_mismatch.success is False
    assert res_mismatch.error == "Read ACK ownership mismatch for message 'MSG_002'"

    # Sender mismatch: Charlie sent MSG_003 to Bob; Bob cannot ACK it to Alice
    engine.route_private_message(
        sender_sid="sid_charlie",
        target="Bob",
        message="Hi Bob",
        meta=MessageMeta(message_id="MSG_003", timestamp="12:02"),
    )
    res_sender_mismatch = engine.route_read_ack(
        reader_sid="sid_bob",
        message_id="MSG_003",
        target="Alice",
    )
    assert res_sender_mismatch.success is False
    assert res_sender_mismatch.error == "Read ACK ownership mismatch for message 'MSG_003'"

    # Offline target fails
    engine.route_private_message(
        sender_sid="sid_alice",
        target="Bob",
        message="Hello",
        meta=MessageMeta(message_id="MSG_OFFLINE", timestamp="12:03"),
    )
    engine.unregister_user("sid_alice")
    res_offline = engine.route_read_ack(
        reader_sid="sid_bob",
        message_id="MSG_OFFLINE",
        target="Alice",
    )
    assert res_offline.success is False
    assert res_offline.error == "Target user 'Alice' is offline"
    assert res_offline.payload is None


# -----------------------------------------------------------------------------
# 2. ChatServer Integration & Event Routing Tests
# -----------------------------------------------------------------------------


def test_server_connect_event():
    spy = SocketIOServerSpy()
    server = ChatServer(sio=spy.sio)
    server.engine.register_user("sid_existing", "ExistingUser")

    spy.trigger("connect", "sid_new", {"REMOTE_ADDR": "127.0.0.1"})

    # Connect event delivers current user list to connecting socket
    events = spy.get_events(EVENT_USER_LIST, room="sid_new")
    assert len(events) == 1
    assert events[0]["data"] == {"users": ["ExistingUser"]}


def test_server_register_success_and_broadcast():
    spy = SocketIOServerSpy()
    _ = ChatServer(sio=spy.sio)

    spy.trigger("register", "sid_1", {"username": "Alice"})

    # 1. Direct register_ack to sid_1
    acks = spy.get_events(EVENT_REGISTER_ACK, room="sid_1")
    assert len(acks) == 1
    assert acks[0]["data"]["success"] is True
    assert acks[0]["data"]["username"] == "Alice"

    # 2. Broadcast updated user_list to all clients
    user_lists = spy.get_events(EVENT_USER_LIST, room=None)
    assert len(user_lists) == 1
    assert user_lists[0]["data"] == {"users": ["Alice"]}


def test_server_broadcasts_selected_avatars_with_presence():
    spy = SocketIOServerSpy()
    _ = ChatServer(sio=spy.sio)

    spy.trigger("register", "sid_1", {"username": "Alice", "avatar": "cat"})
    spy.trigger("register", "sid_2", {"username": "Bob", "avatar": "dog"})

    user_lists = spy.get_events(EVENT_USER_LIST, room=None)
    assert user_lists[-1]["data"] == {
        "users": ["Alice", "Bob"],
        "avatars": {"Alice": "cat", "Bob": "dog"},
    }


def test_server_register_duplicate_rejected():
    spy = SocketIOServerSpy()
    _ = ChatServer(sio=spy.sio)

    spy.trigger("register", "sid_1", {"username": "Alice"})
    spy.clear()

    # Second user requests same username
    spy.trigger("register", "sid_2", {"username": "Alice"})

    # Rejection ACK emitted directly to sid_2
    acks = spy.get_events(EVENT_REGISTER_ACK, room="sid_2")
    assert len(acks) == 1
    assert acks[0]["data"]["success"] is False
    assert acks[0]["data"]["error"] == "Username already taken"

    # No broadcast emitted
    assert len(spy.get_events(EVENT_USER_LIST, room=None)) == 0


def test_server_disconnect_cleanup_and_broadcast():
    spy = SocketIOServerSpy()
    _ = ChatServer(sio=spy.sio)

    spy.trigger("register", "sid_1", {"username": "Alice"})
    spy.trigger("register", "sid_2", {"username": "Bob"})
    spy.clear()

    # Alice disconnects
    spy.trigger("disconnect", "sid_1")

    # Broadcasts updated user list with only Bob
    user_lists = spy.get_events(EVENT_USER_LIST, room=None)
    assert len(user_lists) == 1
    assert user_lists[0]["data"] == {"users": ["Bob"]}

    # Unregistered client disconnecting should not broadcast
    spy.clear()
    spy.trigger("disconnect", "sid_random")
    assert len(spy.get_events(EVENT_USER_LIST, room=None)) == 0


def test_server_creates_group_with_only_its_creator_and_broadcasts_it():
    """R8: a newly created group starts with its creator as its only member."""
    spy = SocketIOServerSpy()
    _ = ChatServer(sio=spy.sio)

    spy.trigger("register", "sid_alice", {"username": "Alice"})
    spy.trigger("register", "sid_bob", {"username": "Bob"})
    spy.clear()

    spy.trigger(EVENT_CREATE_GROUP, "sid_alice", {"group_name": "StudyGroup"})

    group_lists = spy.get_events(EVENT_GROUP_LIST, room=None)
    assert len(group_lists) == 1
    assert group_lists[0]["data"] == {
        "groups": [{"group_name": "StudyGroup", "members": ["Alice"]}]
    }
    assert spy.entered_rooms == [("sid_alice", "StudyGroup")]


def test_server_allows_only_the_requesting_user_to_join_a_group():
    """R9/R10: a join request adds its authenticated sender and rebroadcasts the roster."""
    spy = SocketIOServerSpy()
    _ = ChatServer(sio=spy.sio)

    spy.trigger("register", "sid_alice", {"username": "Alice"})
    spy.trigger("register", "sid_bob", {"username": "Bob"})
    spy.trigger(EVENT_CREATE_GROUP, "sid_alice", {"group_name": "StudyGroup"})
    spy.clear()

    spy.trigger(EVENT_JOIN_GROUP, "sid_bob", {"group_name": "StudyGroup"})

    group_lists = spy.get_events(EVENT_GROUP_LIST, room=None)
    assert len(group_lists) == 1
    assert group_lists[0]["data"] == {
        "groups": [{"group_name": "StudyGroup", "members": ["Alice", "Bob"]}]
    }
    assert spy.entered_rooms == [("sid_alice", "StudyGroup"), ("sid_bob", "StudyGroup")]


def test_server_rejects_a_duplicate_group_name_with_a_group_error():
    spy = SocketIOServerSpy()
    _ = ChatServer(sio=spy.sio)

    spy.trigger("register", "sid_alice", {"username": "Alice"})
    spy.trigger(EVENT_CREATE_GROUP, "sid_alice", {"group_name": "StudyGroup"})
    spy.clear()

    spy.trigger(EVENT_CREATE_GROUP, "sid_alice", {"group_name": "StudyGroup"})

    errors = spy.get_events(EVENT_GROUP_ERROR, room="sid_alice")
    assert errors == [
        {
            "event": EVENT_GROUP_ERROR,
            "data": {"error": "Group name already exists"},
            "room": "sid_alice",
            "to": None,
        }
    ]
    assert spy.get_events(EVENT_GROUP_LIST, room=None) == []


def test_server_rejects_an_empty_group_name_with_a_group_error():
    spy = SocketIOServerSpy()
    _ = ChatServer(sio=spy.sio)

    spy.trigger("register", "sid_alice", {"username": "Alice"})
    spy.clear()
    spy.trigger(EVENT_CREATE_GROUP, "sid_alice", {"group_name": ""})

    errors = spy.get_events(EVENT_GROUP_ERROR, room="sid_alice")
    assert len(errors) == 1
    assert "between 1 and 50" in errors[0]["data"]["error"]


def test_server_broadcasts_group_membership_after_a_member_disconnects():
    spy = SocketIOServerSpy()
    _ = ChatServer(sio=spy.sio)

    spy.trigger("register", "sid_alice", {"username": "Alice"})
    spy.trigger("register", "sid_bob", {"username": "Bob"})
    spy.trigger(EVENT_CREATE_GROUP, "sid_alice", {"group_name": "StudyGroup"})
    spy.trigger(EVENT_JOIN_GROUP, "sid_bob", {"group_name": "StudyGroup"})
    spy.clear()

    spy.trigger("disconnect", "sid_bob")

    group_lists = spy.get_events(EVENT_GROUP_LIST, room=None)
    assert group_lists == [
        {
            "event": EVENT_GROUP_LIST,
            "data": {"groups": [{"group_name": "StudyGroup", "members": ["Alice"]}]},
            "room": None,
            "to": None,
        }
    ]


def test_server_removes_renamed_session_from_its_previous_socketio_group_room():
    spy = SocketIOServerSpy()
    _ = ChatServer(sio=spy.sio)

    spy.trigger("register", "sid_alice", {"username": "Alice"})
    spy.trigger(EVENT_CREATE_GROUP, "sid_alice", {"group_name": "StudyGroup"})
    spy.clear()

    spy.trigger("register", "sid_alice", {"username": "Bob"})

    assert spy.left_rooms == [("sid_alice", "StudyGroup")]


def test_server_private_message_routing_and_charlie_isolation():
    """Verify that private messages between Alice and Bob are never delivered to Charlie (R7)."""
    spy = SocketIOServerSpy()
    _ = ChatServer(sio=spy.sio)

    # Register 3 users: Alice, Bob, Charlie
    spy.trigger("register", "sid_alice", {"username": "Alice"})
    spy.trigger("register", "sid_bob", {"username": "Bob"})
    spy.trigger("register", "sid_charlie", {"username": "Charlie"})
    spy.clear()

    # Alice sends a private message to Bob (with an attempted spoofed sender)
    msg_data = {
        "target": "Bob",
        "message": "Secret message for Bob",
        "message_id": "MSG_001",
        "timestamp": "14:31",
        "sender": "SpoofedCharlie",  # Attack attempt: must be ignored by server
    }
    spy.trigger("private_message", "sid_alice", msg_data)

    # 1. Target recipient (Bob) must receive the message with verified sender "Alice"
    bob_events = spy.get_events(EVENT_PRIVATE_MSG, room="sid_bob")
    assert len(bob_events) == 1
    assert bob_events[0]["data"]["sender"] == "Alice"
    assert bob_events[0]["data"]["target"] == "Bob"
    assert bob_events[0]["data"]["message"] == "Secret message for Bob"
    assert bob_events[0]["data"]["message_id"] == "MSG_001"
    assert bob_events[0]["data"]["timestamp"] == "14:31"

    # 2. Sender (Alice) must receive the echo confirmation
    alice_events = spy.get_events(EVENT_PRIVATE_MSG, room="sid_alice")
    assert len(alice_events) == 1
    assert alice_events[0]["data"]["sender"] == "Alice"
    assert alice_events[0]["data"]["target"] == "Bob"

    # 3. Third party (Charlie) must receive ZERO events (Strict Isolation R7)
    charlie_events = spy.get_events(EVENT_PRIVATE_MSG, room="sid_charlie")
    assert len(charlie_events) == 0

    # 4. No global broadcast should occur (room must be specific sid)
    broadcast_events = [e for e in spy.get_events(EVENT_PRIVATE_MSG) if e["room"] is None]
    assert len(broadcast_events) == 0


def test_server_private_message_invalid_payloads_dropped():
    """Verify malformed or unauthenticated private messages are dropped safely."""
    spy = SocketIOServerSpy()
    _ = ChatServer(sio=spy.sio)

    spy.trigger("register", "sid_alice", {"username": "Alice"})
    spy.clear()

    # Target not online: drops message and emits private_message_error to sender
    spy.trigger(
        "private_message",
        "sid_alice",
        {"target": "OfflineBob", "message": "Hi", "message_id": "MSG_002", "timestamp": "14:32"},
    )
    assert len(spy.get_events(EVENT_PRIVATE_MSG)) == 0
    errors = spy.get_events(EVENT_PRIVATE_MSG_ERROR, room="sid_alice")
    assert len(errors) == 1
    assert errors[0]["data"]["error"] == "User 'OfflineBob' is offline"
    assert errors[0]["data"]["target"] == "OfflineBob"

    # Unregistered sender
    spy.trigger(
        "private_message",
        "sid_unregistered",
        {"target": "Alice", "message": "Hi", "message_id": "MSG_003", "timestamp": "14:33"},
    )
    assert len(spy.get_events(EVENT_PRIVATE_MSG)) == 0

    # Malformed payload (missing message_id)
    spy.trigger(
        "private_message",
        "sid_alice",
        {"target": "Alice", "message": "Hi"},
    )
    assert len(spy.get_events(EVENT_PRIVATE_MSG)) == 0


def test_server_read_ack_routing_and_isolation():
    """Verify read_ack is routed strictly to target sender with verified reader identity (SP4)."""
    spy = SocketIOServerSpy()
    server = ChatServer(sio=spy.sio)

    server.engine.register_user("sid_alice", "Alice")
    server.engine.register_user("sid_bob", "Bob")
    server.engine.register_user("sid_charlie", "Charlie")

    # Alice sends message MSG_001 to Bob
    server.engine.route_private_message(
        sender_sid="sid_alice",
        target="Bob",
        message="Hello Bob",
        meta=MessageMeta(message_id="MSG_001", timestamp="12:00"),
    )

    # Bob reads Alice's message MSG_001
    read_ack_payload = {
        "message_id": "MSG_001",
        "target": "Alice",
        "reader": "SpoofedCharlie",  # Attack attempt: must be derived from sid_bob
    }
    spy.trigger("read_ack", "sid_bob", read_ack_payload)

    # 1. Target recipient (Alice) must receive verified read receipt
    alice_events = spy.get_events(EVENT_READ_ACK, room="sid_alice")
    assert len(alice_events) == 1
    assert alice_events[0]["data"]["message_id"] == "MSG_001"
    assert alice_events[0]["data"]["reader"] == "Bob"

    # 2. Third parties (Charlie, Bob) receive ZERO events
    assert len(spy.get_events(EVENT_READ_ACK, room="sid_charlie")) == 0
    assert len(spy.get_events(EVENT_READ_ACK, room="sid_bob")) == 0

    # 3. No global broadcast
    assert len([e for e in spy.get_events(EVENT_READ_ACK) if e["room"] is None]) == 0


def test_live_server_client_roundtrip():
    """Verify live TCP socket connect, registration, and disconnect over socket."""
    server = ChatServer()
    try:
        httpd = server.create_http_server("127.0.0.1", 0)
    except OSError:
        pytest.skip("Local socket binding not permitted in this environment")

    port = httpd.server_port
    server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    server_thread.start()

    session = ClientSession(f"http://127.0.0.1:{port}")
    try:
        connected = session.connect(transports=["polling"])
        if not connected:
            pytest.skip("Local socket connection not permitted in this environment")

        session.register("LiveAlice")
        time.sleep(0.2)

        assert session.is_registered is True
        assert session.username == "LiveAlice"
        assert "LiveAlice" in session.online_users

        session.disconnect()
        assert session.is_connected is False
    finally:
        httpd.shutdown()
        httpd.server_close()
