# Chat Protocol Contract (Working Draft)

> **Note:** This document is a **working protocol draft** based on the course requirements and lecture theory. Field names, framing choices, and message structures can be refined by the team as implementation progresses. Update this document in the same pull request whenever the protocol changes.

## Protocol stack

```text
Application layer : Custom JSON chat protocol (Newline-delimited JSON or Socket.IO events)
Transport layer   : TCP
Network layer     : IP (IPv4)
Network access    : Wi-Fi or Ethernet
```

The project uses Python socket programming (`socket` over TCP/IPv4, with `python-socketio` also permitted by the course materials).

### Alignment with Internet Protocol Stack theory (Part 1 — R1)

In the standard 5-layer Internet protocol stack, the **Presentation layer** and **Session layer** are not built into the network stack and must be implemented inside the **Application layer** if needed:

- **Presentation-layer role in our application:** Encoding and decoding application messages as UTF-8 JSON with message framing so both client and server interpret structured chat fields consistently.
- **Session-layer role in our application:** Managing logged-in user sessions (`CONNECT_REQ` / `REGISTER_REQ` and active client mappings) as well as group chat membership sessions (`CREATE_GROUP`, `JOIN_GROUP`).
- **Connection-oriented demultiplexing:** The server distinguishes simultaneous clients using each TCP socket's 4-tuple `(Source IP, Source Port, Destination IP, Destination Port)` mapped to the registered username.

---

## 1. Types of messages exchanged

All requests and responses exchanged between Client and Server are grouped by feature area below. (Whether a message uses the `"command"` key, `"type"` key, or a Socket.IO event name, keep the naming consistent across `protocol.py`, `server.py`, and `client.py`.)

### Connection, authentication, and presence (`R3`, `R4`)

| Command / Type | Direction | Purpose |
| --- | --- | --- |
| `CONNECT_REQ` (or `REGISTER_REQ`) | Client -> Server | Request login with a unique username (`R3`) |
| `CONNECT_RES` (or `REGISTER_RES`) | Server -> Client | Confirm login acceptance or report duplicate/invalid username |
| `UPDATE_CLIENT_LIST` (or `USER_LIST_UPDATE`) | Server -> All Clients | Broadcast all currently connected usernames including the client's own name (`R4`) |
| `DISCONNECT` | Client -> Server (or TCP EOF) | Notify server of clean exit so the server removes the user and broadcasts an updated client list |
| `ERROR` | Server -> Client | Report an invalid command, duplicate name, or unauthorized group action |

### Private chat (`R5`, `R6`, `R7`)

| Command / Type | Direction | Purpose |
| --- | --- | --- |
| `SEND_PRIVATE` (or `SEND_PRIVATE_REQ`) | Client -> Server | Send a 1-to-1 text message to a specific online user |
| `PRIVATE_MESSAGE` | Server -> Receiver (& Sender) | Deliver the private message exclusively to the target user and sender (`R7`) |

### Group chat (`R5`, `R6`, `R8`, `R9`, `R10`, `R11`)

| Command / Type | Direction | Purpose |
| --- | --- | --- |
| `CREATE_GROUP` (or `CREATE_GROUP_REQ`) | Client -> Server | Create a new group whose initial member list contains only the creator (`R8`) |
| `UPDATE_GROUP_LIST` (or `GROUP_LIST_UPDATE`) | Server -> All Clients | Broadcast all existing groups along with their current member lists (`R9`) |
| `JOIN_GROUP` (or `JOIN_GROUP_REQ`) | Client -> Server | Voluntarily join an existing group (`R10`) |
| `SEND_GROUP` (or `SEND_GROUP_REQ`) | Client -> Server | Send a text message to a joined group (`R11`) |
| `RECEIVE_GROUP` (or `GROUP_MESSAGE`) | Server -> Group Members | Forward the group message (including the group name) only to members of that group (`R11`) |

### Special features (Part 3 — 4 points)

| Command / Type | Direction | Purpose |
| --- | --- | --- |
| *(Client-side Speech-to-Text)* | Client local -> `SEND_PRIVATE` / `SEND_GROUP` | Convert microphone audio to text on the client first, then send via normal chat commands (**Special Point 1 — AI Feature**) |
| `SEND_STICKER` / `STICKER_MESSAGE` | Client -> Server -> Recipients | Send a predefined `sticker_id` (`STK_001`, etc.) to a private peer or group (**Special Point 2**) |
| `ANNOUNCEMENT` | Server (or Admin) -> All Clients | Broadcast a system-wide alert message across all chat rooms (**Special Point 3**) |
| `READ_ACK` (or `MARK_READ_REQ` / `READ_RECEIPT`) | Receiver -> Server -> Original Sender | Acknowledge that a private message (`message_id`) has been displayed on the receiver's screen (**Special Point 4**) |

---

## 2. Message syntax and TCP framing

### Why JSON instead of delimiter-separated strings (`|`)

If a protocol separates fields with `|` (for example, `SEND_PRIVATE|Alice|Bob|Hello | world`), any user chat message containing `|` breaks string splitting. JSON (`json.dumps()` and `json.loads()`) automatically escapes special characters and nested structures safely.

### Solving TCP byte-stream concatenation (Message Framing)

TCP is a **byte-stream protocol** and does not preserve application message boundaries. If a client sends two JSON packets rapidly, the receiver may read them in a single `recv()` call as `{"command":"A"}{"command":"B"}`, causing `json.loads()` to fail, or a large packet may arrive split across multiple `recv()` calls.

#### Recommended Option A: Newline-delimited JSON (`\n` JSON)

- Append a single newline character `\n` to every serialized JSON string before sending over the TCP socket:

  ```python
  raw_bytes = (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
  sock.sendall(raw_bytes)
  ```

- Because `json.dumps()` escapes any line breaks inside chat text as two characters (`\\n`), a literal `\n` byte only appears at the end of a frame.
- The receiver buffers incoming bytes and splits on `\n` (or reads line-by-line via `sock.makefile("r", encoding="utf-8")`), decoding one complete JSON object per line.

#### Alternative Option B: `Socket.IO` framing or 4-byte length prefix

- If the team uses `Socket.IO`, framing and JSON serialization are handled automatically by the library's event transport.
- If binary payloads are ever introduced on raw TCP sockets, a 4-byte unsigned big-endian length header (`struct.pack("!I", len(payload))`) can be used instead.

---

## 3. Message semantics

Each JSON message uses clear fields so both client and server can route and render events unambiguously:

- `command` (or `type`): Action identifier used to route the packet to the appropriate handler.
- `sender`: Unique username (`R3`) of the user who originated the message. On the server, always verify or populate `sender` from the authenticated socket mapping rather than trusting raw client input.
- `target`: Destination username for private messages (`SEND_PRIVATE`, `READ_ACK`) or target group name for group operations (`CREATE_GROUP`, `JOIN_GROUP`, `SEND_GROUP`).
- `group` (or `group_name`): Included when the server forwards a group message (`RECEIVE_GROUP`) so the receiving client routes the message to the right group tab (`R5`, `R11`).
- `payload` (or `text` / `content`): Message body or structured data (such as the online client list or group membership dictionary).
- `message_id`: Unique message identifier (e.g., `MSG_001` or `uuid4`) used by **Read Receipt** (`READ_ACK`) to mark a specific message as read.
- `sticker_id`: Identifier of a preinstalled sticker image in `assets/stickers/` (e.g., `"STK_001"`).
- `timestamp`: ISO-8601 or human-readable time string displayed in the chat window (`R6`).

### Example payloads (Draft — adaptable during implementation)

```json
{"command": "CONNECT_REQ", "sender": "Alice"}
```

```json
{"command": "UPDATE_CLIENT_LIST", "payload": ["Alice", "Bob"]}
```

```json
{"command": "SEND_PRIVATE", "message_id": "MSG_001", "sender": "Alice", "target": "Bob", "payload": "Hi Bob!", "timestamp": "14:30"}
```

```json
{"command": "CREATE_GROUP", "sender": "Alice", "target": "StudyGroup"}
```

```json
{"command": "UPDATE_GROUP_LIST", "payload": {"StudyGroup": ["Alice", "Bob"]}}
```

```json
{"command": "RECEIVE_GROUP", "message_id": "MSG_002", "group": "StudyGroup", "sender": "Bob", "payload": "Hello everyone!", "timestamp": "14:32"}
```

```json
{"command": "SEND_STICKER", "message_id": "MSG_003", "sender": "Alice", "target": "Bob", "sticker_id": "STK_001", "timestamp": "14:33"}
```

```json
{"command": "ANNOUNCEMENT", "sender": "System_Admin", "payload": "Server will restart in 5 minutes!", "timestamp": "14:35"}
```

```json
{"command": "READ_ACK", "sender": "Bob", "target": "Alice", "message_id": "MSG_001", "timestamp": "14:36"}
```

---

## 4. Communication rules

1. **Login and online presence (`R3`, `R4`):**
   - Immediately after establishing a connection, the client sends `CONNECT_REQ` with its requested username.
   - The server checks whether the username is already in `clients`. If unique, the server stores `clients[username] = socket`, replies with a success `CONNECT_RES`, and broadcasts `UPDATE_CLIENT_LIST` (and current `UPDATE_GROUP_LIST`) to all connected clients.
2. **Private message routing (`R7`):**
   - When the server receives `SEND_PRIVATE`, it looks up `target` in `clients` and forwards the packet only to the target's socket (and confirms/displays on the sender's chat room). No other client receives the packet.
3. **Group creation, discovery, and voluntary join (`R8`, `R9`, `R10`):**
   - `CREATE_GROUP` creates `groups[group_name] = [sender]` containing only the creator (`R8`) and broadcasts `UPDATE_GROUP_LIST` (`R9`).
   - Clients are never added to a group by the creator; another user must explicitly send `JOIN_GROUP` (`R10`), which appends that user to `groups[group_name]` and broadcasts `UPDATE_GROUP_LIST`.
4. **Group message authorization and routing (`R11`):**
   - When the server receives `SEND_GROUP`, it first checks whether `sender in groups.get(target, [])`.
   - If the sender has not joined the group, the server rejects the request with `ERROR`.
   - Otherwise, the server forwards `RECEIVE_GROUP` (with `"group": target`) only to sockets of users currently listed in `groups[target]`.
5. **Special features (Part 3):**
   - **Speech-to-Text:** Recorded and transcribed on the client before sending as normal chat text so raw audio bytes never congest the socket.
   - **Send Sticker:** Sent as a lightweight `sticker_id`; the receiver loads `assets/stickers/<sticker_id>.png` locally.
   - **Announcement:** Broadcast by the server to every socket in `clients` and shown in a top-level banner across all tabs.
   - **Read Receipt:** Triggered automatically when a receiver views an unread private message, forwarding `READ_ACK` back to the original sender to update the message status to `"Read"`.
