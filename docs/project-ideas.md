# Term Project Working Ideas & Part-by-Part Guide

> **Status:** The Socket.IO architecture and event contract are agreed. UI details and implementation order can be refined as the team works; see [protocol.md](protocol.md) for the authoritative event and payload definitions.

This guide summarizes the architectural concepts, protocol choices, UI stack options, and implementation steps for the **Computer Network Term Project (Socket Programming)** across **Part 1, Part 2, and Part 3**, aligned with both the grading criteria and the presentation requirements (`Implementation_Explanation.pdf`).

---

## Part 1: System Architecture Design (1.5 points)

> **Grading Prerequisite:** Part 1 must be fully completed; otherwise, Part 2 will not be graded (`R1`: present the system architecture with at least two clients running on different physical computers and explain the network protocols — What, Why, and How; `R2`: implement chat messages between the server and each client using Socket Programming only).

### 1.1 Protocol Stack Overview

| Layer | Protocol / Technology | What & Why Selected | How It Is Used in the Project |
| --- | --- | --- | --- |
| **Application Layer** | **Socket.IO / Engine.IO events with JSON payloads** | Named JSON events are easy to read and extend. `python-socketio` handles framing and event delivery, so the team does not write delimiter or line-buffer code. | Defines events such as `register`, `private_message`, `group_message`, and `read_ack`; server handlers derive identity from the Socket.IO session ID (`sid`). |
| **Transport Layer** | **TCP** | Chat requires reliable, error-checked, and in-order delivery of messages without packet loss. | Socket.IO uses TCP underneath its WebSocket or HTTP long-polling transport; the application does not call `accept()` or `recv()` itself. |
| **Network Layer** | **IP (IPv4)** | Addresses and routes packets across different physical host computers on the same network. | Allows Client B on a second physical computer to connect to the LAN IPv4 address of the machine running `server.py`. |
| **Network Access Layer** | **Wi-Fi / Ethernet** | Provides physical and data-link connectivity across machines on the local network. | Transfers frames between the physical computers participating in the demonstration. |

### 1.2 Core Lecture Theory Concepts for Presentation Slides (`R1`)

1. **Filling the Missing Layers in the Internet Protocol Stack (`Presentation` & `Session` Layers):**
   - The 5-layer Internet protocol stack omits dedicated **Presentation** and **Session** layers; when an application requires those services, they must be implemented within the **Application layer**.
   - **Presentation Layer in our app:** Serializing and deserializing JSON payloads. Socket.IO automatically handles framing and packet boundaries so both client and server interpret event data identically.
   - **Session Layer in our app:** Mapping Socket.IO session IDs (`sid`) to registered usernames and managing group membership through Socket.IO Rooms.
2. **Server Connection-Oriented Demultiplexing (TCP 4-Tuple):**
   - When multiple clients connect to the same server IP and port simultaneously, the operating system and server distinguish each client socket using the **TCP 4-tuple**: `(Source IP, Source Port, Destination IP, Destination Port)`.
   - The server maps each Socket.IO `sid` to its authenticated username and routes `private_message` to the intended user and `group_message` only to its Socket.IO Room.
3. **Socket.IO Interaction & GUI Thread Safety:**
   - **Socket as a "Door":** A socket still connects each application process to TCP, while Socket.IO supplies the higher-level event, session, and room APIs used by the program.
   - **Server Concurrency:** `socketio.Server()` manages connection lifecycle and dispatches event handlers for simultaneous clients; the application does not implement an `accept()` / `recv()` loop.
   - **Client Concurrency:** Socket.IO callbacks can run on a background network thread. They place incoming events in a thread-safe `queue.Queue`; the CustomTkinter main thread reads that queue through `.after()` so the GUI never freezes.

---

## Part 2: Fundamental Implementation (7.0 points)

### 2.1 Python Desktop UI Tech Stack Options

To keep the Socket.IO server and desktop client in Python (`server.py`, `client.py`, and `gui.py`) and remain compliant with the course rules, a **Python Desktop GUI** is recommended. The Socket.IO WSGI/ASGI wrapper runs inside the team's own `server.py`; it is not an external chat service.

1. **Primary Recommendation: `CustomTkinter` + `Pillow` (or Standard `Tkinter`)**
   - Built on top of Python's standard `tkinter` event loop with a modern appearance and minimal setup (`pip install customtkinter pillow`).
   - **Separate Chat Rooms (`R5`):** Use `CTkTabview` (or `ttk.Notebook`) to give every Private peer and Group chat its own isolated tab.
   - **Stickers (`SP2`) & Read Receipts (`SP4`):** Use `CTkScrollableFrame` as the chat window (`R6`) where each message is rendered as a row widget. Sticker `.png` files render directly via `CTkImage`, and each sent message's status label can be stored by `message_id` and updated to `"Read"` in a single line when `READ_ACK` arrives.
2. **Alternative Option: `PySide6` / `PyQt5`**
   - Offers `QTabWidget` for separate rooms (`R5`) and `QListWidget` or `QTextBrowser` (with `<img src="...">` support) for rendering stickers and rich layouts.

### 2.2 Baseline 2-Screen UI Layout (`R3`–`R11`, `SP1`–`SP4`)

```text
+-----------------------------------------------------------------------------------+
| [Screen 1: Login View (R3)]                                                       |
|                                                                                   |
|                     +---------------------------------------+                     |
|                     |       Simple Chat Application         |                     |
|                     |  Server IP : [ 127.0.0.1           ]  |                     |
|                     |  Port      : [ 5000                ]  |                     |
|                     |  Username  : [ Alice               ]  |                     |
|                     |  [       Connect to Server         ]  |                     |
|                     |  (Error label if username is taken)   |                     |
|                     +---------------------------------------+                     |
+-----------------------------------------------------------------------------------+

+-----------------------------------------------------------------------------------+
| [Screen 2: Main Chat Window]                                                      |
| Logged in as: Alice (Online)                                                      |
+-----------------------------------------------------------------------------------+
| 📢 ANNOUNCEMENT BANNER (SP3): "Server will restart in 5 minutes!"          [Hide] |
+---------------------------+-------------------------------------------------------+
| LEFT SIDEBAR              | RIGHT PANEL: CHAT ROOM TABS (CTkTabview - R5)         |
|                           | [ 👤 Bob ] [ 👥 StudyGroup * ]                        |
| 🟢 Online Users (R4)      +-------------------------------------------------------+
| - Alice (You)             | CHAT WINDOW (CTkScrollableFrame - R6)                 |
| - Bob          [Chat]     |                                                       |
| - Charlie      [Chat]     |  [Bob - 14:30]                                        |
|                           |  Hello Alice!                                         |
| ------------------------- |                                                       |
| 👥 Chat Groups (R8-R10)   |                                      [You - 14:31]    |
| [+ New Group Name] [Add]  |                                      Hi Bob!          |
|                           |                                      Read ✓✓ (SP4)    |
| • StudyGroup              |                                                       |
|   Members: Alice, Bob     |                                      [You - 14:32]    |
|   [Open Room]             |                                      [🖼️ STK_001.png] |
|                           |                                      Read ✓✓ (SP4)    |
| • Gamers                  +-------------------------------------------------------+
|   Members: Charlie        | CHAT BOX (R6)                                         |
|   [Join Group] (R10)      | [😊 Sticker (SP2)] [🎤 Mic (SP1)] [ Type msg... ] [Send]|
+---------------------------+-------------------------------------------------------+
```

### 2.3 Requirement-by-Requirement Flow (`R3`–`R11`)

1. **Unique Username Registration & Online User List (`R3`, `R4`):**
   - After the Socket.IO connection is established, the client emits `register` with `{"username": "MyName"}`.
   - The server verifies that the name is unique, maps its `sid` to `"MyName"`, emits `register_ack`, and broadcasts `user_list` with `{"users": ["Alice", "Bob", "MyName"]}` so each registered user sees themself (`R4`).
2. **Per-Conversation Chat Rooms & UI Components (`R5`, `R6`):**
   - The UI maintains separate tabs or windows for each private conversation and each joined group (`R5`) so messages never mix.
   - Every chat room includes a **Chat window** for displaying sent and incoming messages and a **Chat box** (with text input, Send button, Microphone button, and Sticker button) (`R6`).
3. **Private Messaging (`R7`):**
   - The sender emits `private_message` with `{"message_id": "MSG_001", "target": "Bob", "message": "Hi Bob!"}`.
   - The server derives Alice from `sessions[sid]` and emits the completed event only to Alice and Bob. No other client receives it.
4. **Group Chat Lifecycle (`R8`, `R9`, `R10`, `R11`):**
   - **Create Group (`R8`):** The client emits `create_group` with `{"group_name": "StudyGroup"}`. The server adds only the creator's `sid` to the Socket.IO Room and broadcasts `group_list`.
   - **List Groups & Members (`R9`):** The server emits `group_list` with each group name and current member usernames to all registered clients.
   - **Voluntary Join (`R10`):** Other clients must emit `join_group` for themselves. The server then adds that client's `sid` to the requested Room; group creators cannot add people directly.
   - **Member-Only Group Messaging (`R11`):** Before relaying `group_message`, the server checks that the sending `sid` belongs to the requested Room, then emits only to that Room.

---

## Part 3: Special Points (4.0 points — 4 Features)

> **Note:** At least one special feature must be an AI feature, and the presentation slide must state the **AI Model name**, **reference**, and **Code Snapshot**.

### 1. Speech-to-Text Input (Mandatory AI Feature)
- **Architecture (Client-Side Processing):**
  - When the user clicks the Microphone button in the chat box, the client captures audio from the microphone and invokes `SpeechRecognition` (using **Google Web Speech API** or **OpenAI Whisper**) locally on the client machine to transcribe speech into a text string.
  - The transcribed string is placed into the chat input box (or sent directly) using the normal `private_message` or `group_message` Socket.IO event.
- **Network Benefit:** Raw audio streams/bytes never travel through the Socket.IO connection, conserving bandwidth and keeping event payloads lightweight.

### 2. Send Sticker (Predefined Stickers by ID)
- **Architecture:**
  - Preinstalled sticker image files (e.g., `assets/stickers/STK_001.png`) are bundled on every client machine.
  - Clicking a sticker emits `send_sticker` with a compact JSON payload containing only the identifier and destination, such as `{"sticker_id": "STK_001", "target": "Bob", "group_name": null}`.
  - Upon receiving the packet, the recipient's UI loads `assets/stickers/STK_001.png` from local disk and renders the image inside the appropriate chat window (`R6`).

### 3. System-Wide Announcement (One-to-All Broadcast)
- **Architecture:**
  - Demonstrates **One-to-All Broadcast** across all connected Socket.IO clients regardless of active chat rooms.
  - When an announcement is triggered on the server (or by an authorized admin command), the server emits `announcement` to all clients with `{"message": "Server will restart in 5 minutes!", "timestamp": "14:35"}`.
  - Every connected client displays the announcement in a prominent top banner (with an optional alert sound) visible across all tabs.

### 4. Private-Message Read Receipt (`READ_ACK`)
- **Architecture (Inspired by Reliable Data Transfer `rdt2.0` ACK):**
  1. Every outgoing private message includes a unique `message_id` (e.g., `"MSG_001"` or `uuid4`).
  2. **Receiver Side (Bob):** When Bob opens Alice's private chat tab and displays message `MSG_001`, Bob's client emits `read_ack` with `{"message_id": "MSG_001", "target": "Alice"}`.
  3. **Server Side:** The server derives Bob from `sessions[sid]`, validates that Alice sent the message, and emits `read_ack` with `{"message_id": "MSG_001", "reader": "Bob"}` only to Alice.
  4. **Original Sender Side (Alice):** Alice's UI locates message `MSG_001` in her private chat window and updates its status indicator to `"Read ✓✓"`.

---

## Recommended Step-by-Step Implementation Roadmap

1. **Step 1 — Define Socket.IO Events & Validators (`protocol.py`):**
   - Define event-name constants, JSON payload validation, and the schemas in `docs/protocol.md`. Socket.IO handles framing; do not implement a newline buffer.
2. **Step 2 — Build the Socket.IO Server & State (`server.py`):**
   - Maintain session mappings from `sid` to username and group metadata for the `group_list` event.
   - Register handlers for `register`, `private_message`, `create_group`, `join_group`, `group_message`, `send_sticker`, `announcement`, and `read_ack`; use Socket.IO Rooms for member-only delivery.
3. **Step 3 — Build the Client Session & Desktop GUI (`client.py`, `gui.py`):**
   - Implement the login screen, online user & group sidebars (`R4`, `R9`), and multi-tab chat rooms (`R5`, `R6`).
   - Connect Socket.IO callbacks to the GUI thread through a thread-safe `queue.Queue`.
4. **Step 4 — Integrate the 4 Special Features (`speech.py`, `assets/stickers/`):**
   - Wire the microphone button to `speech.py`, add the sticker picker popup, add the top announcement banner, and trigger `READ_ACK` when viewing private chat tabs.
