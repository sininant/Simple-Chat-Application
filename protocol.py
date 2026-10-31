"""Socket.IO event contracts, JSON payload schemas, and validators (R2).

This module defines canonical Socket.IO event names, strongly-typed dataclass
payload models, and runtime boundary validators for the Simple Chat Application.
It serves as the Presentation Layer seam within the Application layer,
decoupling wire protocol validation from server routing and desktop GUI logic.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Self

# -----------------------------------------------------------------------------
# Canonical Socket.IO Event Name Constants
# -----------------------------------------------------------------------------

# Connection, Authentication & Presence (R3, R4)
EVENT_CONNECT = "connect"
EVENT_DISCONNECT = "disconnect"
EVENT_REGISTER = "register"
EVENT_REGISTER_ACK = "register_ack"
EVENT_USER_LIST = "user_list"

# 1-to-1 Private Chat (R5, R6, R7)
EVENT_PRIVATE_MSG = "private_message"

# Group Chat & Management (R8, R9, R10, R11)
EVENT_CREATE_GROUP = "create_group"
EVENT_JOIN_GROUP = "join_group"
EVENT_GROUP_LIST = "group_list"
EVENT_GROUP_MSG = "group_message"

# Special Points (SP1 - SP4)
EVENT_SEND_STICKER = "send_sticker"  # SP2
EVENT_STICKER_MSG = "sticker_message"  # SP2
EVENT_ANNOUNCEMENT = "announcement"  # SP3
EVENT_READ_ACK = "read_ack"  # SP4

# Set of all defined application-level events
ALL_EVENTS = frozenset(
    {
        EVENT_CONNECT,
        EVENT_DISCONNECT,
        EVENT_REGISTER,
        EVENT_REGISTER_ACK,
        EVENT_USER_LIST,
        EVENT_PRIVATE_MSG,
        EVENT_CREATE_GROUP,
        EVENT_JOIN_GROUP,
        EVENT_GROUP_LIST,
        EVENT_GROUP_MSG,
        EVENT_SEND_STICKER,
        EVENT_STICKER_MSG,
        EVENT_ANNOUNCEMENT,
        EVENT_READ_ACK,
    }
)


# -----------------------------------------------------------------------------
# Protocol Validation Exceptions
# -----------------------------------------------------------------------------


class ProtocolValidationError(Exception):
    """Raised when an incoming packet or payload violates schema or validation rules."""

    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.message = message
        self.field = field

    def __str__(self) -> str:
        if self.field:
            return f"ProtocolValidationError on '{self.field}': {self.message}"
        return f"ProtocolValidationError: {self.message}"


# -----------------------------------------------------------------------------
# Boundary & Primitive Field Validators
# -----------------------------------------------------------------------------

USERNAME_REGEX = re.compile(r"^[a-zA-Z0-9_]{1,20}$")
STICKER_ID_REGEX = re.compile(r"^STK_\d{3}$")
TIMESTAMP_REGEX = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def validate_username(value: Any, field_name: str = "username") -> str:
    """Validate that a username is 1-20 alphanumeric characters without whitespace."""
    if not isinstance(value, str):
        raise ProtocolValidationError("Username must be a string", field=field_name)
    if not USERNAME_REGEX.match(value):
        raise ProtocolValidationError(
            f"Invalid username '{value}'. "
            "Must be 1-20 characters of letters, digits, or underscores only.",
            field=field_name,
        )
    return value


def validate_group_name(value: Any, field_name: str = "group_name") -> str:
    """Validate that a group name is a non-empty string."""
    if not isinstance(value, str):
        raise ProtocolValidationError("Group name must be a string", field=field_name)
    cleaned = value.strip()
    if not cleaned:
        raise ProtocolValidationError("Group name cannot be empty", field=field_name)
    return cleaned


def validate_message_text(value: Any, max_length: int = 1000, field_name: str = "message") -> str:
    """Validate that a message is a non-empty string within size limits."""
    if not isinstance(value, str):
        raise ProtocolValidationError("Message must be a string", field=field_name)
    cleaned = value.strip()
    if not cleaned:
        raise ProtocolValidationError("Message cannot be empty", field=field_name)
    if len(cleaned) > max_length:
        raise ProtocolValidationError(
            f"Message exceeds maximum length of {max_length} characters",
            field=field_name,
        )
    return cleaned


def validate_message_id(value: Any, field_name: str = "message_id") -> str:
    """Validate that a message ID is a non-empty string identifier."""
    if not isinstance(value, str) or not value.strip():
        raise ProtocolValidationError("Message ID must be a non-empty string", field=field_name)
    return value.strip()


def validate_timestamp(value: Any, field_name: str = "timestamp") -> str:
    """Validate that a timestamp is formatted strictly as 'HH:MM' (24-hour)."""
    if not isinstance(value, str):
        raise ProtocolValidationError("Timestamp must be a string", field=field_name)
    cleaned = value.strip()
    if not TIMESTAMP_REGEX.match(cleaned):
        raise ProtocolValidationError(
            f"Timestamp '{value}' must be in 'HH:MM' 24-hour format", field=field_name
        )
    return cleaned


def validate_sticker_id(value: Any, field_name: str = "sticker_id") -> str:
    """Validate that a sticker ID matches 'STK_XXX' where XXX is a 3-digit number."""
    if not isinstance(value, str):
        raise ProtocolValidationError("Sticker ID must be a string", field=field_name)
    cleaned = value.strip()
    if not STICKER_ID_REGEX.match(cleaned):
        raise ProtocolValidationError(
            f"Invalid sticker ID '{value}'. Must be in format 'STK_XXX' (e.g., 'STK_001').",
            field=field_name,
        )
    return cleaned


def validate_destination(target_raw: Any, group_name_raw: Any) -> tuple[str | None, str | None]:
    """Validate mutually exclusive destination (either private target or group_name)."""
    has_target = target_raw is not None and str(target_raw).strip() != ""
    has_group = group_name_raw is not None and str(group_name_raw).strip() != ""

    if has_target and has_group:
        raise ProtocolValidationError(
            "Destination cannot have both 'target' and 'group_name' specified simultaneously"
        )
    if not has_target and not has_group:
        raise ProtocolValidationError(
            "Destination must specify either 'target' (for private) or 'group_name' (for group)"
        )

    target = validate_username(target_raw, field_name="target") if has_target else None
    group_name = validate_group_name(group_name_raw, field_name="group_name") if has_group else None
    return target, group_name


def _ensure_dict(data: Any) -> dict[str, Any]:
    """Ensure raw payload is a Python dictionary."""
    if not isinstance(data, dict):
        raise ProtocolValidationError("Payload must be a JSON dictionary object")
    return data


# -----------------------------------------------------------------------------
# Composite Value Types
# -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MessageMeta:
    """Composite metadata carried across chat packets (message_id and timestamp)."""

    message_id: str
    timestamp: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        message_id = validate_message_id(data.get("message_id"))
        timestamp = validate_timestamp(data.get("timestamp"))
        return cls(message_id=message_id, timestamp=timestamp)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# -----------------------------------------------------------------------------
# Strongly-Typed Payload Dataclasses (R2 Presentation-Layer Seam)
# -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RegisterPayload:
    """Client -> Server: Request username registration (R3)."""

    username: str

    @classmethod
    def from_dict(cls, data: Any) -> Self:
        d = _ensure_dict(data)
        username = validate_username(d.get("username"))
        return cls(username=username)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RegisterAckPayload:
    """Server -> Client: Acknowledge registration with success or error reason (R3)."""

    success: bool
    username: str | None = None
    message: str | None = None
    error: str | None = None

    @classmethod
    def from_dict(cls, data: Any) -> Self:
        d = _ensure_dict(data)
        success = d.get("success")
        if not isinstance(success, bool):
            raise ProtocolValidationError(
                "RegisterAck 'success' field must be a boolean", field="success"
            )

        username = d.get("username")
        message = d.get("message")
        error = d.get("error")

        if success:
            if not username or not isinstance(username, str):
                raise ProtocolValidationError(
                    "Successful RegisterAck must specify 'username'", field="username"
                )
            validated_username = validate_username(username)
            if error is not None:
                raise ProtocolValidationError(
                    "Successful RegisterAck cannot contain 'error'", field="error"
                )
            if message is not None and not isinstance(message, str):
                raise ProtocolValidationError(
                    "RegisterAck 'message' must be a string", field="message"
                )
            return cls(success=True, username=validated_username, message=message, error=None)
        else:
            if not error or not isinstance(error, str):
                raise ProtocolValidationError(
                    "Failed RegisterAck must specify an 'error' message", field="error"
                )
            return cls(success=False, username=None, message=None, error=error)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"success": self.success}
        if self.username is not None:
            result["username"] = self.username
        if self.message is not None:
            result["message"] = self.message
        if self.error is not None:
            result["error"] = self.error
        return result


@dataclass(frozen=True, slots=True)
class UserListPayload:
    """Server -> All Clients: Broadcast active online users (R4)."""

    users: list[str]

    @classmethod
    def from_dict(cls, data: Any) -> Self:
        d = _ensure_dict(data)
        users_raw = d.get("users")
        if not isinstance(users_raw, list):
            raise ProtocolValidationError("UserList 'users' must be a list", field="users")
        validated_users = [
            validate_username(u, field_name=f"users[{i}]") for i, u in enumerate(users_raw)
        ]
        return cls(users=validated_users)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class PrivateMessagePayload:
    """Client -> Server: Direct message to a specific peer (R5, R6, R7)."""

    target: str
    message: str
    meta: MessageMeta

    @property
    def message_id(self) -> str:
        return self.meta.message_id

    @property
    def timestamp(self) -> str:
        return self.meta.timestamp

    @classmethod
    def from_dict(cls, data: Any) -> Self:
        d = _ensure_dict(data)
        target = validate_username(d.get("target"), field_name="target")
        message = validate_message_text(d.get("message"), field_name="message")
        meta = MessageMeta.from_dict(d)
        return cls(target=target, message=message, meta=meta)

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "message": self.message,
            "message_id": self.meta.message_id,
            "timestamp": self.meta.timestamp,
        }


@dataclass(frozen=True, slots=True)
class PrivateMessageServerPayload:
    """Server -> Target & Sender: Forwarded direct message with verified sender (R7)."""

    sender: str
    target: str
    message: str
    meta: MessageMeta

    @property
    def message_id(self) -> str:
        return self.meta.message_id

    @property
    def timestamp(self) -> str:
        return self.meta.timestamp

    @classmethod
    def from_dict(cls, data: Any) -> Self:
        d = _ensure_dict(data)
        sender = validate_username(d.get("sender"), field_name="sender")
        target = validate_username(d.get("target"), field_name="target")
        message = validate_message_text(d.get("message"), field_name="message")
        meta = MessageMeta.from_dict(d)
        return cls(sender=sender, target=target, message=message, meta=meta)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sender": self.sender,
            "target": self.target,
            "message": self.message,
            "message_id": self.meta.message_id,
            "timestamp": self.meta.timestamp,
        }


@dataclass(frozen=True, slots=True)
class _GroupTargetPayload:
    """Base class for payloads targeting a group by name (R8, R10)."""

    group_name: str

    @classmethod
    def from_dict(cls, data: Any) -> Self:
        d = _ensure_dict(data)
        group_name = validate_group_name(d.get("group_name"))
        return cls(group_name=group_name)

    def to_dict(self) -> dict[str, Any]:
        return {"group_name": self.group_name}


@dataclass(frozen=True, slots=True)
class CreateGroupPayload(_GroupTargetPayload):
    """Client -> Server: Create a new chat group with creator as sole member (R8)."""


@dataclass(frozen=True, slots=True)
class JoinGroupPayload(_GroupTargetPayload):
    """Client -> Server: Voluntarily join an existing group (R10)."""


@dataclass(frozen=True, slots=True)
class GroupInfo:
    """Metadata describing a single group and its current member roster (R9)."""

    group_name: str
    members: list[str]

    @classmethod
    def from_dict(cls, data: Any) -> Self:
        d = _ensure_dict(data)
        group_name = validate_group_name(d.get("group_name"))
        members_raw = d.get("members")
        if not isinstance(members_raw, list):
            raise ProtocolValidationError("GroupInfo 'members' must be a list", field="members")
        members = [
            validate_username(m, field_name=f"members[{i}]") for i, m in enumerate(members_raw)
        ]
        return cls(group_name=group_name, members=members)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class GroupListPayload:
    """Server -> All Clients: Broadcast listing of all existing groups (R9)."""

    groups: list[GroupInfo]

    @classmethod
    def from_dict(cls, data: Any) -> Self:
        d = _ensure_dict(data)
        groups_raw = d.get("groups")
        if not isinstance(groups_raw, list):
            raise ProtocolValidationError("GroupList 'groups' must be a list", field="groups")
        groups = [GroupInfo.from_dict(g) for g in groups_raw]
        return cls(groups=groups)

    def to_dict(self) -> dict[str, Any]:
        return {"groups": [g.to_dict() for g in self.groups]}


@dataclass(frozen=True, slots=True)
class GroupMessagePayload:
    """Client -> Server: Post message to a joined group room (R11)."""

    group_name: str
    message: str
    meta: MessageMeta

    @property
    def message_id(self) -> str:
        return self.meta.message_id

    @property
    def timestamp(self) -> str:
        return self.meta.timestamp

    @classmethod
    def from_dict(cls, data: Any) -> Self:
        d = _ensure_dict(data)
        group_name = validate_group_name(d.get("group_name"), field_name="group_name")
        message = validate_message_text(d.get("message"), field_name="message")
        meta = MessageMeta.from_dict(d)
        return cls(group_name=group_name, message=message, meta=meta)

    def to_dict(self) -> dict[str, Any]:
        return {
            "group_name": self.group_name,
            "message": self.message,
            "message_id": self.meta.message_id,
            "timestamp": self.meta.timestamp,
        }


@dataclass(frozen=True, slots=True)
class GroupMessageServerPayload:
    """Server -> Group Room: Broadcast message exclusively to group members (R11)."""

    sender: str
    group_name: str
    message: str
    meta: MessageMeta

    @property
    def message_id(self) -> str:
        return self.meta.message_id

    @property
    def timestamp(self) -> str:
        return self.meta.timestamp

    @classmethod
    def from_dict(cls, data: Any) -> Self:
        d = _ensure_dict(data)
        sender = validate_username(d.get("sender"), field_name="sender")
        group_name = validate_group_name(d.get("group_name"), field_name="group_name")
        message = validate_message_text(d.get("message"), field_name="message")
        meta = MessageMeta.from_dict(d)
        return cls(sender=sender, group_name=group_name, message=message, meta=meta)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sender": self.sender,
            "group_name": self.group_name,
            "message": self.message,
            "message_id": self.meta.message_id,
            "timestamp": self.meta.timestamp,
        }


@dataclass(frozen=True, slots=True)
class SendStickerPayload:
    """Client -> Server: Transmit predefined sticker to private user or group (SP2)."""

    sticker_id: str
    meta: MessageMeta
    target: str | None = None
    group_name: str | None = None

    @property
    def message_id(self) -> str:
        return self.meta.message_id

    @property
    def timestamp(self) -> str:
        return self.meta.timestamp

    @classmethod
    def from_dict(cls, data: Any) -> Self:
        d = _ensure_dict(data)
        sticker_id = validate_sticker_id(d.get("sticker_id"), field_name="sticker_id")
        meta = MessageMeta.from_dict(d)
        target, group_name = validate_destination(d.get("target"), d.get("group_name"))

        return cls(
            sticker_id=sticker_id,
            meta=meta,
            target=target,
            group_name=group_name,
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "sticker_id": self.sticker_id,
            "message_id": self.meta.message_id,
            "timestamp": self.meta.timestamp,
        }
        if self.target is not None:
            result["target"] = self.target
            result["group_name"] = None
        else:
            result["target"] = None
            result["group_name"] = self.group_name
        return result


@dataclass(frozen=True, slots=True)
class StickerMessageServerPayload:
    """Server -> Recipients: Forwarded sticker packet with verified sender (SP2)."""

    sender: str
    sticker_id: str
    meta: MessageMeta
    target: str | None = None
    group_name: str | None = None

    @property
    def message_id(self) -> str:
        return self.meta.message_id

    @property
    def timestamp(self) -> str:
        return self.meta.timestamp

    @classmethod
    def from_dict(cls, data: Any) -> Self:
        d = _ensure_dict(data)
        sender = validate_username(d.get("sender"), field_name="sender")
        sticker_id = validate_sticker_id(d.get("sticker_id"), field_name="sticker_id")
        meta = MessageMeta.from_dict(d)
        target, group_name = validate_destination(d.get("target"), d.get("group_name"))

        return cls(
            sender=sender,
            sticker_id=sticker_id,
            meta=meta,
            target=target,
            group_name=group_name,
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "sender": self.sender,
            "sticker_id": self.sticker_id,
            "message_id": self.meta.message_id,
            "timestamp": self.meta.timestamp,
        }
        if self.target is not None:
            result["target"] = self.target
            result["group_name"] = None
        else:
            result["target"] = None
            result["group_name"] = self.group_name
        return result


@dataclass(frozen=True, slots=True)
class AnnouncementPayload:
    """Server -> All Clients: System-wide broadcast alert (SP3)."""

    message: str
    timestamp: str

    @classmethod
    def from_dict(cls, data: Any) -> Self:
        d = _ensure_dict(data)
        message = validate_message_text(d.get("message"), field_name="message")
        timestamp = validate_timestamp(d.get("timestamp"), field_name="timestamp")
        return cls(message=message, timestamp=timestamp)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ReadAckClientPayload:
    """Client -> Server: Report that recipient viewed a private message (SP4)."""

    message_id: str
    target: str

    @classmethod
    def from_dict(cls, data: Any) -> Self:
        d = _ensure_dict(data)
        message_id = validate_message_id(d.get("message_id"), field_name="message_id")
        target = validate_username(d.get("target"), field_name="target")
        return cls(message_id=message_id, target=target)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ReadAckServerPayload:
    """Server -> Original Sender: Forward read receipt with verified reader (SP4)."""

    message_id: str
    reader: str

    @classmethod
    def from_dict(cls, data: Any) -> Self:
        d = _ensure_dict(data)
        message_id = validate_message_id(d.get("message_id"), field_name="message_id")
        reader = validate_username(d.get("reader"), field_name="reader")
        return cls(message_id=message_id, reader=reader)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# -----------------------------------------------------------------------------
# Explicit Directional Dispatcher APIs (Server Trust Boundary Enforcement)
# -----------------------------------------------------------------------------

CLIENT_EVENT_PAYLOAD_MAP: dict[str, type] = {
    EVENT_REGISTER: RegisterPayload,
    EVENT_PRIVATE_MSG: PrivateMessagePayload,
    EVENT_CREATE_GROUP: CreateGroupPayload,
    EVENT_JOIN_GROUP: JoinGroupPayload,
    EVENT_GROUP_MSG: GroupMessagePayload,
    EVENT_SEND_STICKER: SendStickerPayload,
    EVENT_READ_ACK: ReadAckClientPayload,
}

SERVER_EVENT_PAYLOAD_MAP: dict[str, type] = {
    EVENT_REGISTER_ACK: RegisterAckPayload,
    EVENT_USER_LIST: UserListPayload,
    EVENT_PRIVATE_MSG: PrivateMessageServerPayload,
    EVENT_GROUP_LIST: GroupListPayload,
    EVENT_GROUP_MSG: GroupMessageServerPayload,
    EVENT_STICKER_MSG: StickerMessageServerPayload,
    EVENT_ANNOUNCEMENT: AnnouncementPayload,
    EVENT_READ_ACK: ReadAckServerPayload,
}


def parse_client_event(event: str, data: Any) -> Any:
    """Parse and validate an inbound event payload sent from an untrusted client to server."""
    if event not in CLIENT_EVENT_PAYLOAD_MAP:
        raise ProtocolValidationError(f"Unknown or unauthorized client event: '{event}'")
    schema_cls = CLIENT_EVENT_PAYLOAD_MAP[event]
    return schema_cls.from_dict(data)


def parse_server_event(event: str, data: Any) -> Any:
    """Parse and validate an inbound event payload received by a client from server."""
    if event not in SERVER_EVENT_PAYLOAD_MAP:
        raise ProtocolValidationError(f"Unknown or unauthorized server event: '{event}'")
    schema_cls = SERVER_EVENT_PAYLOAD_MAP[event]
    return schema_cls.from_dict(data)
