"""Unit and integration tests for Socket.IO server and ChatEngine (R2, R3, R4)."""

import threading
from typing import Any

import socketio

from protocol import (
    EVENT_ANNOUNCEMENT,
    EVENT_GROUP_LIST,
    EVENT_GROUP_MSG,
    EVENT_PRIVATE_MSG,
    EVENT_READ_ACK,
    EVENT_REGISTER_ACK,
    EVENT_STICKER_MSG,
    EVENT_USER_LIST,
)
from server import ChatEngine, ChatServer

# -----------------------------------------------------------------------------
# Fixtures & Test Utilities
# -----------------------------------------------------------------------------


class SocketIOServerSpy:
    """Helper test double to record emissions and room operations from ChatServer."""

    def __init__(self) -> None:
        self.sio = socketio.Server(async_mode="threading")
        self.emissions: list[dict[str, Any]] = []
        self.rooms_entered: list[tuple[str, str]] = []

        self._orig_emit = self.sio.emit
        self._orig_enter_room = self.sio.enter_room

        def spy_emit(
            event: str,
            data: Any = None,
            to: Any = None,
            room: Any = None,
            **kwargs: Any,
        ) -> Any:
            self.emissions.append({"event": event, "data": data, "room": room, "to": to})
            return None

        def spy_enter_room(sid: str, room: str, namespace: Any = None) -> None:
            self.rooms_entered.append((sid, room))

        self.sio.emit = spy_emit  # type: ignore[assignment]
        self.sio.enter_room = spy_enter_room  # type: ignore[assignment]

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
        self.rooms_entered.clear()


# -----------------------------------------------------------------------------
# 1. ChatEngine Unit Tests (Headless Domain State Machine)
# -----------------------------------------------------------------------------


def test_chat_engine_initial_state():
    engine = ChatEngine()
    assert engine.get_online_users() == []
    assert engine.get_groups() == []
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


def test_chat_engine_group_lifecycle():
    engine = ChatEngine()
    engine.register_user("sid_1", "Alice")
    engine.register_user("sid_2", "Bob")

    # Create group
    ok, msg = engine.create_group("Alice", "StudyGroup")
    assert ok is True
    assert msg == "Group created successfully"

    assert engine.is_group_member("Alice", "StudyGroup") is True
    assert engine.is_group_member("Bob", "StudyGroup") is False

    # Duplicate group creation
    ok2, msg2 = engine.create_group("Bob", "StudyGroup")
    assert ok2 is False
    assert "already exists" in msg2

    # Voluntary join
    ok3, msg3 = engine.join_group("Bob", "StudyGroup")
    assert ok3 is True
    assert engine.is_group_member("Bob", "StudyGroup") is True

    # Join non-existent group
    ok4, msg4 = engine.join_group("Bob", "NonExistent")
    assert ok4 is False
    assert "does not exist" in msg4

    # Group list model
    g_payload = engine.get_group_list_payload()
    assert len(g_payload.groups) == 1
    assert g_payload.groups[0].group_name == "StudyGroup"
    assert g_payload.groups[0].members == ["Alice", "Bob"]

    # User groups lookup
    assert engine.get_user_groups("Alice") == ["StudyGroup"]
    assert engine.get_user_groups("Charlie") == []


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

    # 3. Direct group_list to sid_1
    group_lists = spy.get_events(EVENT_GROUP_LIST, room="sid_1")
    assert len(group_lists) == 1
    assert group_lists[0]["data"] == {"groups": []}


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


def test_server_private_message_routing_and_sender_trust_boundary():
    """Verify Server Trust Boundary: server derives verified sender and forwards to recipient."""
    spy = SocketIOServerSpy()
    _ = ChatServer(sio=spy.sio)

    spy.trigger("register", "sid_alice", {"username": "Alice"})
    spy.trigger("register", "sid_bob", {"username": "Bob"})
    spy.clear()

    # Alice sends private message to Bob, maliciously attempting to spoof sender as 'Charlie'
    inbound_data = {
        "sender": "Charlie",  # INJECTED SPOOF
        "target": "Bob",
        "message": "Hi Bob!",
        "message_id": "MSG_001",
        "timestamp": "14:31",
    }
    spy.trigger("private_message", "sid_alice", inbound_data)

    # 1. Forwarded to Bob (target)
    bob_events = spy.get_events(EVENT_PRIVATE_MSG, room="sid_bob")
    assert len(bob_events) == 1
    # VERIFIED SENDER IS ALICE (derived from sid_alice, not Charlie)
    assert bob_events[0]["data"]["sender"] == "Alice"
    assert bob_events[0]["data"]["target"] == "Bob"
    assert bob_events[0]["data"]["message"] == "Hi Bob!"

    # 2. Forwarded to Alice (sender copy)
    alice_events = spy.get_events(EVENT_PRIVATE_MSG, room="sid_alice")
    assert len(alice_events) == 1
    assert alice_events[0]["data"]["sender"] == "Alice"


def test_server_create_group_and_broadcast():
    spy = SocketIOServerSpy()
    _ = ChatServer(sio=spy.sio)
    spy.trigger("register", "sid_alice", {"username": "Alice"})
    spy.clear()

    spy.trigger("create_group", "sid_alice", {"group_name": "StudyGroup"})

    # SocketIO room entered
    assert ("sid_alice", "StudyGroup") in spy.rooms_entered

    # Broadcast updated group_list
    g_events = spy.get_events(EVENT_GROUP_LIST, room=None)
    assert len(g_events) == 1
    assert g_events[0]["data"] == {"groups": [{"group_name": "StudyGroup", "members": ["Alice"]}]}


def test_server_join_group_and_broadcast():
    spy = SocketIOServerSpy()
    _ = ChatServer(sio=spy.sio)
    spy.trigger("register", "sid_alice", {"username": "Alice"})
    spy.trigger("register", "sid_bob", {"username": "Bob"})
    spy.trigger("create_group", "sid_alice", {"group_name": "StudyGroup"})
    spy.clear()

    # Bob joins StudyGroup
    spy.trigger("join_group", "sid_bob", {"group_name": "StudyGroup"})

    assert ("sid_bob", "StudyGroup") in spy.rooms_entered
    g_events = spy.get_events(EVENT_GROUP_LIST, room=None)
    assert len(g_events) == 1
    assert g_events[0]["data"]["groups"][0]["members"] == ["Alice", "Bob"]


def test_server_group_message_routing_and_access_control():
    spy = SocketIOServerSpy()
    _ = ChatServer(sio=spy.sio)
    spy.trigger("register", "sid_alice", {"username": "Alice"})
    spy.trigger("register", "sid_bob", {"username": "Bob"})
    spy.trigger("register", "sid_charlie", {"username": "Charlie"})
    spy.trigger("create_group", "sid_alice", {"group_name": "StudyGroup"})
    spy.clear()

    # Alice (member) posts to StudyGroup
    spy.trigger(
        "group_message",
        "sid_alice",
        {
            "group_name": "StudyGroup",
            "message": "Hello all!",
            "message_id": "MSG_002",
            "timestamp": "14:32",
        },
    )

    room_events = spy.get_events(EVENT_GROUP_MSG, room="StudyGroup")
    assert len(room_events) == 1
    assert room_events[0]["data"]["sender"] == "Alice"
    assert room_events[0]["data"]["group_name"] == "StudyGroup"
    assert room_events[0]["data"]["message"] == "Hello all!"

    # Charlie (non-member) tries to post to StudyGroup -> access control drops message
    spy.clear()
    spy.trigger(
        "group_message",
        "sid_charlie",
        {
            "group_name": "StudyGroup",
            "message": "Sneaky message",
            "message_id": "MSG_003",
            "timestamp": "14:33",
        },
    )
    assert len(spy.get_events(EVENT_GROUP_MSG, room="StudyGroup")) == 0


def test_server_send_sticker_private_and_group():
    spy = SocketIOServerSpy()
    _ = ChatServer(sio=spy.sio)
    spy.trigger("register", "sid_alice", {"username": "Alice"})
    spy.trigger("register", "sid_bob", {"username": "Bob"})
    spy.trigger("create_group", "sid_alice", {"group_name": "StickerFans"})
    spy.clear()

    # 1. Private sticker Alice -> Bob
    spy.trigger(
        "send_sticker",
        "sid_alice",
        {
            "sticker_id": "STK_001",
            "target": "Bob",
            "group_name": None,
            "message_id": "MSG_010",
            "timestamp": "15:00",
        },
    )
    bob_stk = spy.get_events(EVENT_STICKER_MSG, room="sid_bob")
    assert len(bob_stk) == 1
    assert bob_stk[0]["data"]["sender"] == "Alice"
    assert bob_stk[0]["data"]["sticker_id"] == "STK_001"
    assert bob_stk[0]["data"]["target"] == "Bob"

    # 2. Group sticker Alice -> StickerFans
    spy.clear()
    spy.trigger(
        "send_sticker",
        "sid_alice",
        {
            "sticker_id": "STK_002",
            "target": None,
            "group_name": "StickerFans",
            "message_id": "MSG_011",
            "timestamp": "15:01",
        },
    )
    grp_stk = spy.get_events(EVENT_STICKER_MSG, room="StickerFans")
    assert len(grp_stk) == 1
    assert grp_stk[0]["data"]["sender"] == "Alice"
    assert grp_stk[0]["data"]["group_name"] == "StickerFans"


def test_server_read_ack_routing():
    spy = SocketIOServerSpy()
    _ = ChatServer(sio=spy.sio)
    spy.trigger("register", "sid_alice", {"username": "Alice"})
    spy.trigger("register", "sid_bob", {"username": "Bob"})
    spy.clear()

    # Bob sends read_ack for Alice's message
    spy.trigger("read_ack", "sid_bob", {"message_id": "MSG_001", "target": "Alice"})

    # Server derives Bob as reader and emits read_ack exclusively to Alice
    alice_events = spy.get_events(EVENT_READ_ACK, room="sid_alice")
    assert len(alice_events) == 1
    assert alice_events[0]["data"] == {"message_id": "MSG_001", "reader": "Bob"}


def test_server_broadcast_announcement():
    spy = SocketIOServerSpy()
    server = ChatServer(sio=spy.sio)

    payload = server.broadcast_announcement("Server will restart in 5 minutes!", "14:30")
    assert payload.message == "Server will restart in 5 minutes!"
    assert payload.timestamp == "14:30"

    ann_events = spy.get_events(EVENT_ANNOUNCEMENT, room=None)
    assert len(ann_events) == 1
    assert ann_events[0]["data"] == {
        "message": "Server will restart in 5 minutes!",
        "timestamp": "14:30",
    }
