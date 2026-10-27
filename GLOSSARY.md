# Simple Chat Application

A multi-client desktop chat system over TCP/IPv4 using a custom newline-delimited JSON application protocol, supporting 1-to-1 private rooms, voluntary-join group rooms, client-side speech-to-text, stickers, announcements, and read receipts.

## Language

### Network & Protocol

**Frame**:
A single UTF-8 encoded JSON line terminated by a newline character (`\n`) transmitted over a TCP byte stream.
_Avoid_: Packet, chunk, buffer, datagram

**Packet**:
A decoded JSON dictionary representing one application-level message with a `command` field.
_Avoid_: Payload, event, request, DTO

**Command**:
The string identifier in a Packet's `"command"` field (`CONNECT_REQ`, `CONNECT_RES`, `UPDATE_CLIENT_LIST`, `SEND_PRIVATE`, `CREATE_GROUP`, `UPDATE_GROUP_LIST`, `JOIN_GROUP`, `SEND_GROUP`, `RECEIVE_GROUP`, `SEND_STICKER`, `ANNOUNCEMENT`, `READ_ACK`, `DISCONNECT`, `ERROR`) that determines how the receiver routes and handles the Packet.
_Avoid_: Type, action, method, route

**Payload**:
The primary content carried under a Packet's `"payload"` key, such as chat text, the list of online usernames, or the dictionary of groups and their members.
_Avoid_: Body, data, content, text

### Core Modules & Seams

**Chat Engine**:
The headless server-side state and routing module that validates incoming Packets, tracks connected users and groups, and returns outbound target-socket deliveries without directly performing socket I/O.
_Avoid_: Server service, controller, hub, manager

**Client Session**:
The headless client-side state module that tracks the current user, online client list, group list, and per-room message history, communicating with the desktop UI through a thread-safe queue.
_Avoid_: Client store, view model, state manager

### Chat Rooms & Features

**Chat Room**:
An isolated conversation view (`R5`, `R6`) containing a scrollable message window and a composition chat box, dedicated to either one Private Room or one Group Room.
_Avoid_: Channel, thread, dialog

**Private Room**:
A 1-to-1 Chat Room between the local user and one specific online peer, visible only to the sender and receiver (`R7`).
_Avoid_: Direct message, DM, whisper

**Group Room**:
A multi-user Chat Room created by a user (`R8`), discoverable by all connected clients with its member list (`R9`), and accessible only to users who voluntarily join (`R10`, `R11`).
_Avoid_: Channel, public room, lobby

**Sticker ID**:
A predefined string identifier (such as `STK_001`) sent in a `SEND_STICKER` Packet that resolves to a local `.png` image inside `assets/stickers/` on the receiving client.
_Avoid_: Emoji, attachment, image upload

**Announcement**:
A one-to-all system broadcast Packet (`ANNOUNCEMENT`) sent by the server to every connected client and displayed in a global banner across all Chat Rooms.
_Avoid_: System message, notification, alert

**Read Receipt**:
An automatic acknowledgement Packet (`READ_ACK`) referencing a specific `message_id`, sent when the recipient displays a Private Room message so the original sender's UI marks that message as `"Read"`.
_Avoid_: Seen status, delivery receipt
