"""Unit and integration tests for Socket.IO server and ChatEngine (R2, R3, R4)."""

import threading
from typing import Any

import socketio

from protocol import (
    EVENT_REGISTER_ACK,
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
