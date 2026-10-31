"""Unit tests for Socket.IO event contracts, payload schemas, and validators (R2)."""

import pytest

from protocol import (
    ALL_EVENTS,
    CLIENT_EVENT_PAYLOAD_MAP,
    EVENT_ANNOUNCEMENT,
    EVENT_PRIVATE_MSG,
    EVENT_REGISTER,
    EVENT_REGISTER_ACK,
    EVENT_STICKER_MSG,
    EVENT_USER_LIST,
    SERVER_EVENT_PAYLOAD_MAP,
    AnnouncementPayload,
    CreateGroupPayload,
    GroupListPayload,
    GroupMessagePayload,
    GroupMessageServerPayload,
    JoinGroupPayload,
    MessageMeta,
    PrivateMessagePayload,
    PrivateMessageServerPayload,
    ProtocolValidationError,
    ReadAckClientPayload,
    ReadAckServerPayload,
    RegisterAckPayload,
    RegisterPayload,
    SendStickerPayload,
    StickerMessageServerPayload,
    UserListPayload,
    parse_client_event,
    parse_server_event,
    validate_destination,
    validate_group_name,
    validate_message_id,
    validate_message_text,
    validate_sticker_id,
    validate_timestamp,
    validate_username,
)

# -----------------------------------------------------------------------------
# 1. Event Constants & Directional Maps Tests
# -----------------------------------------------------------------------------


def test_canonical_event_constants_defined():
    """Verify that all canonical event names match protocol.md specifications."""
    expected_events = {
        "connect",
        "disconnect",
        "register",
        "register_ack",
        "user_list",
        "private_message",
        "create_group",
        "join_group",
        "group_list",
        "group_message",
        "send_sticker",
        "sticker_message",
        "announcement",
        "read_ack",
    }
    assert ALL_EVENTS == expected_events


def test_directional_event_maps():
    """Verify client and server event maps are explicitly partitioned."""
    # Client map: only client-to-server events
    assert EVENT_REGISTER in CLIENT_EVENT_PAYLOAD_MAP
    assert EVENT_PRIVATE_MSG in CLIENT_EVENT_PAYLOAD_MAP
    assert EVENT_ANNOUNCEMENT not in CLIENT_EVENT_PAYLOAD_MAP
    assert EVENT_USER_LIST not in CLIENT_EVENT_PAYLOAD_MAP

    # Server map: only server-to-client events
    assert EVENT_REGISTER_ACK in SERVER_EVENT_PAYLOAD_MAP
    assert EVENT_USER_LIST in SERVER_EVENT_PAYLOAD_MAP
    assert EVENT_ANNOUNCEMENT in SERVER_EVENT_PAYLOAD_MAP
    assert EVENT_STICKER_MSG in SERVER_EVENT_PAYLOAD_MAP


def test_protocol_validation_error_is_exception_not_value_error():
    """Verify ProtocolValidationError inherits directly from Exception, not ValueError."""
    assert issubclass(ProtocolValidationError, Exception)
    assert not issubclass(ProtocolValidationError, ValueError)

    err = ProtocolValidationError("Field error", field="target")
    assert str(err) == "ProtocolValidationError on 'target': Field error"

    err_no_field = ProtocolValidationError("General error")
    assert str(err_no_field) == "ProtocolValidationError: General error"


# -----------------------------------------------------------------------------
# 2. Primitive & Boundary Validators Tests
# -----------------------------------------------------------------------------


def test_validate_username_valid():
    assert validate_username("Alice") == "Alice"
    assert validate_username("Bob_123") == "Bob_123"
    assert validate_username("user_with_20_chars12") == "user_with_20_chars12"


@pytest.mark.parametrize(
    "invalid_user",
    [
        "",  # empty
        "   ",  # whitespace only
        " Alice ",  # leading/trailing spaces must be rejected (not stripped)
        "Bob ",  # trailing space
        " Charlie",  # leading space
        "user with space",  # space inside
        "alice!",  # punctuation
        "user@name",  # symbol
        "this_username_is_way_too_long_to_be_accepted",  # > 20 chars
        123,  # non-string
        None,  # NoneType
    ],
)
def test_validate_username_invalid(invalid_user):
    with pytest.raises(ProtocolValidationError):
        validate_username(invalid_user)


def test_validate_group_name_valid():
    assert validate_group_name("StudyGroup") == "StudyGroup"
    assert validate_group_name("General Chat 101") == "General Chat 101"


@pytest.mark.parametrize(
    "invalid_group",
    [
        "",
        "   ",
        12345,
        None,
    ],
)
def test_validate_group_name_invalid(invalid_group):
    with pytest.raises(ProtocolValidationError):
        validate_group_name(invalid_group)


def test_validate_message_text_valid():
    assert validate_message_text("Hello World!") == "Hello World!"
    assert validate_message_text("  trimmed  ") == "trimmed"


@pytest.mark.parametrize(
    "invalid_msg",
    [
        "",
        "   ",
        "a" * 1001,  # exceeds default 1000 limit
        None,
        ["not", "a", "string"],
    ],
)
def test_validate_message_text_invalid(invalid_msg):
    with pytest.raises(ProtocolValidationError):
        validate_message_text(invalid_msg)


def test_validate_message_id():
    assert validate_message_id("MSG_001") == "MSG_001"
    assert validate_message_id("  UUID-1234  ") == "UUID-1234"

    with pytest.raises(ProtocolValidationError):
        validate_message_id("")
    with pytest.raises(ProtocolValidationError):
        validate_message_id("   ")
    with pytest.raises(ProtocolValidationError):
        validate_message_id(None)


def test_validate_timestamp_pure():
    """Verify validate_timestamp is a pure validator without fallback mutation."""
    # Valid HH:MM
    assert validate_timestamp("14:30") == "14:30"
    assert validate_timestamp("00:00") == "00:00"
    assert validate_timestamp("23:59") == "23:59"

    # Missing or invalid timestamp must raise ProtocolValidationError (no hidden auto-generate)
    with pytest.raises(ProtocolValidationError):
        validate_timestamp(None)
    with pytest.raises(ProtocolValidationError):
        validate_timestamp("")
    with pytest.raises(ProtocolValidationError):
        validate_timestamp("25:00")
    with pytest.raises(ProtocolValidationError):
        validate_timestamp("12:60")
    with pytest.raises(ProtocolValidationError):
        validate_timestamp("invalid")


def test_validate_sticker_id():
    assert validate_sticker_id("STK_001") == "STK_001"
    assert validate_sticker_id("STK_999") == "STK_999"

    with pytest.raises(ProtocolValidationError):
        validate_sticker_id("STK_1")  # not 3 digits
    with pytest.raises(ProtocolValidationError):
        validate_sticker_id("stk_001")  # lowercase
    with pytest.raises(ProtocolValidationError):
        validate_sticker_id("STICKER_01")
    with pytest.raises(ProtocolValidationError):
        validate_sticker_id(None)


def test_message_meta_composite():
    d = {"message_id": "MSG_100", "timestamp": "12:30"}
    meta = MessageMeta.from_dict(d)
    assert meta.message_id == "MSG_100"
    assert meta.timestamp == "12:30"
    assert meta.to_dict() == d


def test_validate_destination_helper():
    # Target only
    t, g = validate_destination("Alice", None)
    assert t == "Alice"
    assert g is None

    # Group only
    t, g = validate_destination(None, "StudyGroup")
    assert t is None
    assert g == "StudyGroup"

    # Both target and group raises error
    with pytest.raises(ProtocolValidationError):
        validate_destination("Alice", "StudyGroup")

    # Neither raises error
    with pytest.raises(ProtocolValidationError):
        validate_destination(None, None)


# -----------------------------------------------------------------------------
# 3. Payload Schemas & Serialization Tests
# -----------------------------------------------------------------------------


def test_register_payload_roundtrip():
    raw = {"username": "Alice"}
    payload = RegisterPayload.from_dict(raw)
    assert payload.username == "Alice"
    assert payload.to_dict() == raw


def test_register_payload_extra_field_sanitized():
    raw = {"username": "Alice", "hacker_injected_field": "exploit"}
    payload = RegisterPayload.from_dict(raw)
    assert payload.username == "Alice"
    assert not hasattr(payload, "hacker_injected_field")
    assert payload.to_dict() == {"username": "Alice"}


def test_register_ack_payload_conditional_validation():
    # Success ACK: must have username, no error
    success_raw = {"success": True, "username": "Alice", "message": "Welcome!"}
    p1 = RegisterAckPayload.from_dict(success_raw)
    assert p1.success is True
    assert p1.username == "Alice"
    assert p1.message == "Welcome!"
    assert p1.error is None
    assert p1.to_dict() == success_raw

    # Success ACK missing username raises ProtocolValidationError
    with pytest.raises(ProtocolValidationError):
        RegisterAckPayload.from_dict({"success": True})

    # Success ACK with error raises ProtocolValidationError
    with pytest.raises(ProtocolValidationError):
        RegisterAckPayload.from_dict(
            {"success": True, "username": "Alice", "error": "Contradiction"}
        )

    # Failure ACK: must have error, no username
    fail_raw = {"success": False, "error": "Username already taken"}
    p2 = RegisterAckPayload.from_dict(fail_raw)
    assert p2.success is False
    assert p2.error == "Username already taken"
    assert p2.username is None
    assert p2.to_dict() == fail_raw

    # Failure ACK missing error raises ProtocolValidationError
    with pytest.raises(ProtocolValidationError):
        RegisterAckPayload.from_dict({"success": False})


def test_user_list_payload():
    raw = {"users": ["Alice", "Bob", "Charlie"]}
    payload = UserListPayload.from_dict(raw)
    assert payload.users == ["Alice", "Bob", "Charlie"]
    assert payload.to_dict() == raw

    with pytest.raises(ProtocolValidationError):
        UserListPayload.from_dict({"users": ["Alice", "Invalid User!"]})


def test_private_message_client_and_server_payloads():
    # Client -> Server (no sender)
    client_raw = {
        "target": "Bob",
        "message": "Hi Bob!",
        "message_id": "MSG_001",
        "timestamp": "14:31",
    }
    client_p = PrivateMessagePayload.from_dict(client_raw)
    assert client_p.target == "Bob"
    assert client_p.message == "Hi Bob!"
    assert client_p.message_id == "MSG_001"
    assert client_p.timestamp == "14:31"
    assert client_p.to_dict() == client_raw

    # Server -> Recipients (includes verified sender)
    server_raw = {
        "sender": "Alice",
        "target": "Bob",
        "message": "Hi Bob!",
        "message_id": "MSG_001",
        "timestamp": "14:31",
    }
    server_p = PrivateMessageServerPayload.from_dict(server_raw)
    assert server_p.sender == "Alice"
    assert server_p.target == "Bob"
    assert server_p.message_id == "MSG_001"
    assert server_p.timestamp == "14:31"
    assert server_p.to_dict() == server_raw


def test_group_payloads():
    # Create group
    create_p = CreateGroupPayload.from_dict({"group_name": "StudyGroup"})
    assert create_p.group_name == "StudyGroup"
    assert create_p.to_dict() == {"group_name": "StudyGroup"}

    # Join group
    join_p = JoinGroupPayload.from_dict({"group_name": "StudyGroup"})
    assert join_p.group_name == "StudyGroup"
    assert join_p.to_dict() == {"group_name": "StudyGroup"}

    # Group list
    group_list_raw = {
        "groups": [
            {"group_name": "StudyGroup", "members": ["Alice", "Bob"]},
            {"group_name": "Gamers", "members": ["Charlie"]},
        ]
    }
    gl_p = GroupListPayload.from_dict(group_list_raw)
    assert len(gl_p.groups) == 2
    assert gl_p.to_dict() == group_list_raw

    # Group message Client -> Server
    gmsg_client = GroupMessagePayload.from_dict(
        {
            "group_name": "StudyGroup",
            "message": "Hello all!",
            "message_id": "MSG_002",
            "timestamp": "14:32",
        }
    )
    assert gmsg_client.group_name == "StudyGroup"
    assert gmsg_client.message_id == "MSG_002"

    # Group message Server -> Room
    gmsg_server = GroupMessageServerPayload.from_dict(
        {
            "sender": "Alice",
            "group_name": "StudyGroup",
            "message": "Hello all!",
            "message_id": "MSG_002",
            "timestamp": "14:32",
        }
    )
    assert gmsg_server.sender == "Alice"
    assert gmsg_server.group_name == "StudyGroup"


def test_send_and_receive_sticker_payload():
    # Valid private sticker
    priv_stk = SendStickerPayload.from_dict(
        {
            "sticker_id": "STK_001",
            "target": "Bob",
            "group_name": None,
            "message_id": "MSG_003",
            "timestamp": "14:33",
        }
    )
    assert priv_stk.target == "Bob"
    assert priv_stk.group_name is None
    assert priv_stk.to_dict() == {
        "sticker_id": "STK_001",
        "message_id": "MSG_003",
        "timestamp": "14:33",
        "target": "Bob",
        "group_name": None,
    }

    # Server forwarded sticker
    srv_stk = StickerMessageServerPayload.from_dict(
        {
            "sender": "Alice",
            "sticker_id": "STK_001",
            "target": "Bob",
            "group_name": None,
            "message_id": "MSG_003",
            "timestamp": "14:33",
        }
    )
    assert srv_stk.sender == "Alice"
    assert srv_stk.to_dict()["sender"] == "Alice"


def test_announcement_payload():
    raw = {"message": "Server will restart in 5 minutes!", "timestamp": "14:30"}
    p = AnnouncementPayload.from_dict(raw)
    assert p.message == "Server will restart in 5 minutes!"
    assert p.timestamp == "14:30"
    assert p.to_dict() == raw


def test_read_ack_payloads():
    # Client -> Server
    client_raw = {"message_id": "MSG_001", "target": "Alice"}
    p1 = ReadAckClientPayload.from_dict(client_raw)
    assert p1.message_id == "MSG_001"
    assert p1.target == "Alice"
    assert p1.to_dict() == client_raw

    # Server -> Sender
    server_raw = {"message_id": "MSG_001", "reader": "Bob"}
    p2 = ReadAckServerPayload.from_dict(server_raw)
    assert p2.message_id == "MSG_001"
    assert p2.reader == "Bob"
    assert p2.to_dict() == server_raw


# -----------------------------------------------------------------------------
# 4. Explicit Directional Dispatcher & Server Trust Boundary Tests
# -----------------------------------------------------------------------------


def test_parse_client_event_inbound():
    """Verify parse_client_event safely parses untrusted client payloads."""
    res = parse_client_event(EVENT_REGISTER, {"username": "Charlie"})
    assert isinstance(res, RegisterPayload)
    assert res.username == "Charlie"


def test_parse_client_event_prevents_identity_spoofing():
    """SECURITY TEST: Verify injected 'sender' does not promote payload to server payload."""
    injected_data = {
        "sender": "admin",  # Attacker attempts identity spoofing
        "target": "Bob",
        "message": "I am admin!",
        "message_id": "MSG_999",
        "timestamp": "10:00",
    }
    # Parsing via parse_client_event MUST return PrivateMessagePayload (Client type)
    # and MUST NOT create a PrivateMessageServerPayload
    payload = parse_client_event(EVENT_PRIVATE_MSG, injected_data)
    assert isinstance(payload, PrivateMessagePayload)
    assert not hasattr(payload, "sender")
    assert payload.target == "Bob"


def test_parse_client_event_rejects_server_only_events():
    """Verify client parser rejects server-exclusive events like announcement."""
    with pytest.raises(ProtocolValidationError):
        parse_client_event(EVENT_ANNOUNCEMENT, {"message": "alert", "timestamp": "12:00"})
    with pytest.raises(ProtocolValidationError):
        parse_client_event(EVENT_USER_LIST, {"users": ["Alice"]})


def test_parse_server_event_inbound():
    """Verify parse_server_event parses server broadcasts cleanly."""
    res = parse_server_event(
        EVENT_ANNOUNCEMENT,
        {"message": "Server maintenance", "timestamp": "01:00"},
    )
    assert isinstance(res, AnnouncementPayload)
    assert res.message == "Server maintenance"

    res_user_list = parse_server_event(
        EVENT_USER_LIST,
        {"users": ["Alice", "Bob"]},
    )
    assert isinstance(res_user_list, UserListPayload)


def test_parse_server_event_rejects_client_only_events():
    """Verify server parser rejects client-exclusive events like register."""
    with pytest.raises(ProtocolValidationError):
        parse_server_event(EVENT_REGISTER, {"username": "Alice"})
