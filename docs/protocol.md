# Chat Protocol Contract (Socket.IO Specification)

> **Note:** This document defines the **Socket.IO Event & Payload Protocol** agreed upon by the team for the Simple Chat Application. All client and server components must adhere to these event names and JSON payload structures.

## Protocol stack

```text
Application layer : Socket.IO / Engine.IO events & JSON payloads
Transport layer   : TCP
Network layer     : IP (IPv4)
Network access    : Wi-Fi
```

The application is implemented using **Socket.IO (`python-socketio`)** over TCP/IPv4, which is explicitly permitted and listed under course project materials (`https://socket.io/docs/v4/`).

### Alignment with Internet Protocol Stack theory (Part 1 — R1)

In the standard 5-layer Internet protocol stack, the **Presentation layer** and **Session layer** are not built into the raw transport layer and are implemented inside the **Application layer**:

- **Presentation-layer role:** Encoding and decoding application messages as structured JSON payloads. Socket.IO's framing engine automatically handles packet framing, boundary preservation, and serialization/deserialization.
- **Session-layer role:** Managing logged-in user sessions (mapping unique Socket.IO session IDs `sid` to registered usernames) and managing group chat membership sessions using Socket.IO's native Rooms mechanism (`sio.enter_room`, `sio.leave_room`).
- **Connection-oriented demultiplexing:** The server demultiplexes simultaneous client connections via the underlying TCP socket 4-tuple `(Source IP, Source Port, Destination IP, Destination Port)` associated with each client's session ID (`sid`).

---

## 1. Socket.IO Event Catalog

All communication between Client and Server occurs through named Socket.IO events with JSON dictionary payloads:

### Connection, Authentication, and Presence (`R3`, `R4`)

| Event Name | Direction | Payload Schema | Description |
| --- | --- | --- | --- |
| `connect` | Client -> Server | *(Built-in)* | Client establishes underlying socket connection; server assigns a unique `sid`. |
| `register` | Client -> Server | `{"username": "Alice"}` | Client requests registration with a unique username (`R3`). |
| `register_ack` | Server -> Client | `{"success": true, "username": "Alice", "message": "Registered successfully"}` or `{"success": false, "error": "Username already taken"}` | Server confirms or rejects registration. |
| `user_list` | Server -> All Clients | `{"users": ["Alice", "Bob"]}` | Broadcast containing all currently connected usernames, including the recipient's own name (`R4`). |
| `disconnect` | Client -> Server | *(Built-in)* | Client disconnects or connection drops; server cleans up session and broadcasts updated `user_list`. |

### Private Chat (`R5`, `R6`, `R7`)

| Event Name | Direction | Payload Schema | Description |
| --- | --- | --- | --- |
| `private_message` | Client -> Server | `{"target": "Bob", "message": "Hi Bob!", "message_id": "MSG_001", "timestamp": "14:31"}` | Client sends a direct message to a specific user. |
| `private_message` | Server -> Target & Sender | `{"sender": "Alice", "target": "Bob", "message": "Hi Bob!", "message_id": "MSG_001", "timestamp": "14:31"}` | Server forwards the message exclusively to the recipient and sender's rooms (`R7`). |

### Group Chat & Management (`R5`, `R6`, `R8`, `R9`, `R10`, `R11`)

| Event Name | Direction | Payload Schema | Description |
| --- | --- | --- | --- |
| `create_group` | Client -> Server | `{"group_name": "StudyGroup"}` | Creates a new chat group; server adds creator via `sio.enter_room(sid, group_name)` as sole initial member (`R8`), then broadcasts `group_list`. |
| `join_group` | Client -> Server | `{"group_name": "StudyGroup"}` | Client voluntarily joins an existing group (`R10`); server adds user to room via `sio.enter_room(sid, group_name)` and broadcasts updated `group_list`. |
| `group_list` | Server -> All Clients | `{"groups": [{"group_name": "StudyGroup", "members": ["Alice", "Bob"]}]}` | Broadcast listing all existing groups and their current member lists (`R9`). |
| `group_message` | Client -> Server | `{"group_name": "StudyGroup", "message": "Hello all!", "message_id": "MSG_002", "timestamp": "14:32"}` | Client sends a message to a joined group (`R11`). |
| `group_message` | Server -> Group Room | `{"sender": "Alice", "group_name": "StudyGroup", "message": "Hello all!", "message_id": "MSG_002", "timestamp": "14:32"}` | Server emits message exclusively to members in that room (`sio.emit(..., room=group_name)`) (`R11`). |

### Special Points (Part 3 — 4 Points)

| Event Name | Direction | Payload Schema | Description |
| --- | --- | --- | --- |
| *(Client-side Speech-to-Text)* | Client local -> `private_message` / `group_message` | *(Normal message payload)* | Microphone audio transcribed to text locally before emitting as text (**Special Point 1 — Mandatory AI**). |
| `send_sticker` | Client -> Server | `{"sticker_id": "STK_001", "target": "Bob", "group_name": null, "message_id": "MSG_003", "timestamp": "14:33"}` | Client sends a predefined sticker to a peer or group (**Special Point 2**). |
| `sticker_message` | Server -> Recipients | `{"sender": "Alice", "sticker_id": "STK_001", "target": "Bob", "group_name": null, "message_id": "MSG_003", "timestamp": "14:33"}` | Server forwards sticker event; client renders image from `assets/stickers/<sticker_id>.png`. |
| `announcement` | Server -> All Clients | `{"message": "Server will restart in 5 minutes!", "timestamp": "14:30"}` | Server broadcasts alert across all client instances, displayed in top banner (**Special Point 3**). |
| `read_ack` | Client -> Server | `{"message_id": "MSG_001", "target": "Alice"}` | Client notifies server that they viewed Alice's private message (**Special Point 4**). |
| `read_ack` | Server -> Original Sender | `{"message_id": "MSG_001", "reader": "Bob"}` | Server derives `reader` from `sessions[sid]`, validates target, and emits acknowledgment exclusively to Alice, updating message status to `"Read ✓✓"`. |

---

## 2. Why Socket.IO (Architectural Advantages)

1. **Automatic Message Framing & Packet Demarcation**:
   - In raw TCP socket programming, TCP is a byte stream that does not preserve message boundaries (packets can arrive fragmented or concatenated).
   - `python-socketio` natively manages application framing, buffering, and packet serialization. No custom `LineBuffer` or delimiter escaping is needed.
2. **Built-in Room Multiplexing**:
   - `sio.enter_room(sid, room)` and `sio.leave_room(sid, room)` provide native room isolation.
   - Emitting to a room (`sio.emit("group_message", data, room=group_name)`) guarantees that only joined group members receive the broadcast (`R11`), eliminating complex manual socket loops.
3. **Connection Lifecycle & Heartbeats**:
   - Built-in ping/pong heartbeats automatically detect abrupt client disconnects (crashes, Wi-Fi drops) and trigger `@sio.event def disconnect(sid)`, ensuring live presence (`R4`) stays synchronized.

---

## 3. Communication Rules & Invariants

1. **Identity Verification**:
   - Clients send only payload data. The server derives the sender's username from the authenticated session registry (`sessions[sid]`) to prevent identity spoofing.
2. **Room Access Control**:
   - When receiving `group_message`, the server verifies that the sending `sid` is currently in the requested room before emitting to the group.
3. **Thread Safety in Client GUI**:
   - `socketio.Client()` event callbacks run on background network threads.
   - Incoming events must be pushed into a thread-safe `queue.Queue` so the CustomTkinter GUI thread polls and renders updates via `.after()` without race conditions.
