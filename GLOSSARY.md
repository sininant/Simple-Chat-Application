# Simple Chat Application

A multi-client desktop chat system using Socket.IO events with JSON payloads over TCP/IPv4. It supports private rooms, voluntary-join group rooms, client-side speech-to-text, stickers, announcements, and read receipts.

## Language

### Network & Protocol

**Socket.IO Event**:
A named application-level interaction exchanged between a client and the server, such as `register`, `private_message`, or `group_message`.
_Avoid_: Packet, command, request

**Event Payload**:
The structured JSON data accompanying a Socket.IO Event. It contains only the fields required by that Event.
_Avoid_: Packet, body, DTO

**Session**:
The authenticated association between one connected client and its unique username. A Session also determines that client's current group memberships.
_Avoid_: Socket, connection record

**Session ID (`sid`)**:
The unique identifier that Socket.IO assigns to a connected client Session. The server uses it to derive the authenticated sender identity.
_Avoid_: Username token, client ID

**Socket.IO Room**:
A server-managed collection of Sessions used to deliver a group message only to current group members.
_Avoid_: Channel, lobby, public room

**Message ID**:
A unique identifier for a private, group, or sticker message. It lets a Read Receipt refer to the exact message that was viewed.
_Avoid_: Packet ID, sequence number

### Core Modules & Seams

**Chat Engine**:
The headless server-side state and routing module that governs registration, presence, group membership, and message delivery without depending on the desktop UI.
_Avoid_: Server service, controller, hub, manager

**Client Session**:
The headless client-side state module that tracks the current user, presence, group list, and per-room message history, communicating with the desktop UI through a thread-safe queue.
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
A predefined string identifier, such as `STK_001`, included in a `send_sticker` Event and resolved to a local `.png` image on receiving clients.
_Avoid_: Emoji, attachment, image upload

**Announcement**:
A one-to-all server broadcast sent through the `announcement` Event and displayed in a global banner across all Chat Rooms.
_Avoid_: System message, notification, alert

**Read Receipt**:
An automatic `read_ack` Event that references a Message ID when a recipient displays a Private Room message, so the original sender's UI marks that message as read.
_Avoid_: Seen status, delivery receipt
