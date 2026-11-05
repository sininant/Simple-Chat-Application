"""Unit and integration tests for Socket.IO server and ChatEngine (R2, R3, R4)."""

import threading
import time
from typing import Any

import pytest
import socketio

from client import ClientSession
from protocol import (
    EVENT_PRIVATE_MSG,
    EVENT_PRIVATE_MSG_ERROR,
    EVENT_REGISTER_ACK,
    EVENT_USER_LIST,
    MessageMeta,
)
from server import ChatEngine, ChatServer, RouteResult

# -----------------------------------------------------------------------------
# Fixtures & Test Utilities
# -----------------------------------------------------------------------------


class SocketIOServerSpy:
    """Helper test double to record emissions and room operations from ChatServer."""

    def __init__(self) -> None:
        self.sio = socketio.Server(async_mode="threading")
        self.emissions: list[dict[str, Any]] = []

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
