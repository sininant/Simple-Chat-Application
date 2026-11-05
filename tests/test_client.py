"""Unit tests for headless ClientSession adapter (R2, R3, R4)."""

from typing import Any

import pytest
import socketio

from client import ClientSession
from protocol import (
    EVENT_REGISTER,
    EVENT_REGISTER_ACK,
    EVENT_USER_LIST,
    ProtocolValidationError,
    RegisterAckPayload,
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

    session.register("Alice")

    assert len(spy.emitted) == 1
    event, data = spy.emitted[0]
    assert event == EVENT_REGISTER
    assert data == {"username": "Alice"}


def test_client_session_register_rejects_invalid_username():
    spy = SocketIOClientSpy()
    session = ClientSession(sio=spy.sio)

    with pytest.raises(ProtocolValidationError):
        session.register("Alice with spaces")

    with pytest.raises(ProtocolValidationError):
        session.register("")

    assert len(spy.emitted) == 0


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
