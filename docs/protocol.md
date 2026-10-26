# Chat Protocol Contract

This document is the shared contract between the Python client and server. Update it in the same pull request as any protocol change.

## Protocol stack

```text
Custom length-prefixed JSON chat protocol
TCP
IPv4
Wi-Fi or Ethernet
```

The project uses raw Python TCP sockets. It does not use Socket.IO, WebSocket, HTTP, a shared database, or a shared file as the real-time communication channel.

## Deployment topology

The recommended demonstration uses two physical computers and three separate processes:

```text
Computer A                              Computer B
server.py                               client.py (Bob)
client.py (Alice)  <--- TCP/IPv4 --->   connects to Computer A
```

`server.py` and Alice's `client.py` are separate processes even though they run on the same computer. The second client runs on Computer B, satisfying the requirement that the clients run on different physical computers. A third server-only computer is supported but optional.

## Message framing

TCP is a byte stream and does not preserve application-message boundaries. Each protocol message therefore uses this frame:

```text
+-------------------------+--------------------------+
| Payload length: 4 bytes | UTF-8 JSON: N bytes      |
+-------------------------+--------------------------+
```

- The length is an unsigned 32-bit integer in network byte order.
- The length counts only the JSON payload bytes.
- Receivers must continue reading until all four header bytes and all declared payload bytes arrive.
- Both sides must reject frames larger than the agreed maximum size.

Do not assume that one `send()` corresponds to one `recv()`.

## General message shape

Client request:

```json
{
  "type": "SEND_PRIVATE_REQ",
  "request_id": "REQ_001",
  "target": "Bob",
  "payload": {
    "text": "Hello Bob"
  }
}
```

Server event:

```json
{
  "type": "PRIVATE_MESSAGE",
  "message_id": "MSG_001",
  "sender": "Alice",
  "receiver": "Bob",
  "text": "Hello Bob",
  "timestamp": "2026-10-26T14:30:00+07:00"
}
```

## Trust rules

- A client request must not be trusted to identify its own sender.
- The server derives the sender from the username registered to that TCP socket.
- The server validates message type, payload shape, size, target existence, and group membership.
- Private events are sent only to the sender and receiver.
- Group events are sent only to current members of that group.
- System announcements originate only from an authorized server-side action.

## Planned message types

### Connection and presence

| Type | Direction | Purpose |
| --- | --- | --- |
| `REGISTER_REQ` | Client to server | Request a unique username |
| `REGISTER_RES` | Server to client | Accept or reject registration |
| `USER_LIST_UPDATE` | Server to all clients | Publish the current online-user list |
| `ERROR` | Server to client | Report an invalid request |

### Private chat

| Type | Direction | Purpose |
| --- | --- | --- |
| `SEND_PRIVATE_REQ` | Client to server | Request delivery of private text |
| `PRIVATE_MESSAGE` | Server to sender and receiver | Deliver the validated private message |

### Group chat

| Type | Direction | Purpose |
| --- | --- | --- |
| `CREATE_GROUP_REQ` | Client to server | Create a group containing only its creator |
| `CREATE_GROUP_RES` | Server to requester | Report the creation result |
| `JOIN_GROUP_REQ` | Client to server | Voluntarily join an existing group |
| `JOIN_GROUP_RES` | Server to requester | Report the join result |
| `GROUP_LIST_UPDATE` | Server to all clients | Publish groups with their member lists |
| `SEND_GROUP_REQ` | Client to server | Request delivery of group text |
| `GROUP_MESSAGE` | Server to group members | Deliver the validated group message |

### Special features

| Type | Direction | Purpose |
| --- | --- | --- |
| `SEND_STICKER_REQ` | Client to server | Send a predefined sticker ID to a private or group room |
| `STICKER_MESSAGE` | Server to authorized recipients | Deliver the validated sticker event |
| `ANNOUNCEMENT` | Server to all online clients | Deliver a system-wide announcement |
| `MARK_READ_REQ` | Client to server | Mark a received private message as displayed |
| `READ_RECEIPT` | Server to original sender | Report that the receiver displayed the message |

Speech-to-text is processed before sending a chat message. The recognized text uses the existing private or group message request rather than a separate network message type.

## Lifecycle rules

1. A client establishes a TCP connection and sends `REGISTER_REQ`.
2. The server accepts a unique non-empty username and responds with `REGISTER_RES`.
3. The server broadcasts `USER_LIST_UPDATE` after registration or disconnection.
4. The server assigns message IDs and authoritative sender names.
5. On disconnect, the server removes the socket from the online-user mappings and notifies remaining clients.

Detailed request fields and error codes should be added feature by feature before implementation is merged.
