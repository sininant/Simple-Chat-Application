# Term Project Working Ideas & Part-by-Part Guide

> **Status:** Working Draft / Rough Ideas. This document outlines the initial design concepts for each part of the project. It is not a rigid final specification and can be adapted by the team as implementation progresses.

This guide summarizes the architectural concepts, protocol choices, UI stack options, and implementation steps for the **Computer Network Term Project (Socket Programming)** across **Part 1, Part 2, and Part 3**, aligned with both the grading criteria and the presentation requirements (`Implementation_Explanation.pdf`).

---

## Part 1: System Architecture Design (1.5 points)

> **Grading Prerequisite:** Part 1 must be fully completed; otherwise, Part 2 will not be graded (`R1`: present the system architecture with at least two clients running on different physical computers and explain the network protocols — What, Why, and How; `R2`: implement chat messages between the server and each client using Socket Programming only).

### 1.1 Protocol Stack Overview

| Layer | Protocol / Technology | What & Why Selected | How It Is Used in the Project |
| --- | --- | --- | --- |
| **Application Layer** | **Custom JSON Chat Protocol** (`\n` Newline-delimited JSON or `Socket.IO` events) | Easy to read, extend, and debug in Python (`json.dumps` / `json.loads`). Avoids delimiter-splitting bugs when users type characters such as `\|` inside chat messages. | Defines structured fields for commands (`command`), sender identity (`sender`), recipient or group (`target`), content (`payload`), and message tracking (`message_id`). |
| **Transport Layer** | **TCP** | Chat requires reliable, error-checked, and in-order delivery of messages without packet loss. | Maintains persistent bidirectional connections between each `client.py` process and `server.py` using Python `socket` (`AF_INET`, `SOCK_STREAM`). |
| **Network Layer** | **IP (IPv4)** | Addresses and routes packets across different physical host computers on the same network. | Allows Client B on a second physical computer to connect to the LAN IPv4 address of the machine running `server.py`. |
| **Network Access Layer** | **Wi-Fi / Ethernet** | Provides physical and data-link connectivity across machines on the local network. | Transfers frames between the physical computers participating in the demonstration. |

### 1.2 Core Lecture Theory Concepts for Presentation Slides (`R1`)

1. **Filling the Missing Layers in the Internet Protocol Stack (`Presentation` & `Session` Layers):**
   - The 5-layer Internet protocol stack omits dedicated **Presentation** and **Session** layers; when an application requires those services, they must be implemented within the **Application layer**.
   - **Presentation Layer in our app:** Serializing and deserializing UTF-8 JSON payloads with newline (`\n`) message framing so both client and server interpret data types and boundaries identically.
   - **Session Layer in our app:** Managing authenticated user sessions (`CONNECT_REQ` / `CONNECT_RES` and active client mappings) as well as group chat membership state (`CREATE_GROUP`, `JOIN_GROUP`).
2. **Server Connection-Oriented Demultiplexing (TCP 4-Tuple):**
   - When multiple clients connect to the same server IP and port simultaneously, the operating system and server distinguish each client socket using the **TCP 4-tuple**: `(Source IP, Source Port, Destination IP, Destination Port)`.
   - The server maps each accepted client socket object to its unique username (`clients = {"username": socket_obj}`), ensuring `SEND_PRIVATE` and `SEND_GROUP` messages are routed strictly to the intended recipient sockets.
3. **Socket Interaction & Multi-Threading Architecture:**
   - **Socket as a "Door":** A socket acts as the interface door between the application process and the end-to-end transport protocol (`TCP`).
   - **Server Concurrency:** Because `socket.recv()` is a blocking call, the server's welcoming socket loops on `server_socket.accept()` and spawns a dedicated `threading.Thread` for each connected client so multiple clients can chat concurrently.
   - **Client Concurrency:** Each client runs at least two threads: the **Main Thread** (running the desktop GUI event loop) and a **Background Listener Thread** (running the blocking `recv()` loop and pushing incoming events into a thread-safe queue) so the user interface never freezes.

---

## Part 2: Fundamental Implementation (7.0 points)

### 2.1 Python Desktop UI Tech Stack Options

To keep socket programming in Python (`server.py` and `client.py`) and remain 100% compliant with the course rules (separate client and server processes without relying on external web servers), a **Python Desktop GUI** is recommended:

1. **Primary Recommendation: `CustomTkinter` + `Pillow` (or Standard `Tkinter`)**
   - Built on top of Python's standard `tkinter` event loop with a modern appearance and minimal setup (`pip install customtkinter pillow`).
   - **Separate Chat Rooms (`R5`):** Use `CTkTabview` (or `ttk.Notebook`) to give every Private peer and Group chat its own isolated tab.
   - **Stickers (`SP2`) & Read Receipts (`SP4`):** Use `CTkScrollableFrame` as the chat window (`R6`) where each message is rendered as a row widget. Sticker `.png` files render directly via `CTkImage`, and each sent message's status label can be stored by `message_id` and updated to `"Read"` in a single line when `READ_ACK` arrives.
2. **Alternative Option: `PySide6` / `PyQt5`**
   - Offers `QTabWidget` for separate rooms (`R5`) and `QListWidget` or `QTextBrowser` (with `<img src="...">` support) for rendering stickers and rich layouts.

### 2.2 Requirement-by-Requirement Flow (`R3`–`R11`)

1. **Unique Username Registration & Online User List (`R3`, `R4`):**
   - Immediately upon connecting, the client sends `{"command": "CONNECT_REQ", "sender": "MyName"}`.
   - The server verifies that `"MyName"` is not already present in `clients`. If unique, the server stores `clients["MyName"] = sock`, sends `CONNECT_RES`, and broadcasts `{"command": "UPDATE_CLIENT_LIST", "payload": ["Alice", "Bob", "MyName"]}` to every connected client so each user sees all online users including their own name (`R4`).
2. **Per-Conversation Chat Rooms & UI Components (`R5`, `R6`):**
   - The UI maintains separate tabs or windows for each private conversation and each joined group (`R5`) so messages never mix.
   - Every chat room includes a **Chat window** for displaying sent and incoming messages and a **Chat box** (with text input, Send button, Microphone button, and Sticker button) (`R6`).
3. **Private Messaging (`R7`):**
   - The sender transmits `{"command": "SEND_PRIVATE", "message_id": "MSG_001", "sender": "Alice", "target": "Bob", "payload": "Hi Bob!"}`.
   - The server looks up `clients["Bob"]` and forwards the packet exclusively to Bob's socket (while Alice renders her sent message in her `"Bob"` private room). No other client can see the message.
4. **Group Chat Lifecycle (`R8`, `R9`, `R10`, `R11`):**
   - **Create Group (`R8`):** The client sends `{"command": "CREATE_GROUP", "sender": "Alice", "target": "StudyGroup"}`. The server initializes `groups["StudyGroup"] = ["Alice"]` with only the creator as the initial member.
   - **List Groups & Members (`R9`):** The server broadcasts `{"command": "UPDATE_GROUP_LIST", "payload": {"StudyGroup": ["Alice"]}}` to all connected clients.
   - **Voluntary Join (`R10`):** Other clients must click **Join** themselves to send `{"command": "JOIN_GROUP", "sender": "Bob", "target": "StudyGroup"}`. Group creators cannot automatically add other users.
   - **Member-Only Group Messaging (`R11`):** When a client sends `SEND_GROUP`, the server checks that `sender in groups["StudyGroup"]`. If verified, the server forwards `RECEIVE_GROUP` (including `"group": "StudyGroup"`) only to the sockets of current members of that group so their clients display the message inside the `"StudyGroup"` tab.

---

## Part 3: Special Points (4.0 points — 4 Features)

> **Note:** At least one special feature must be an AI feature, and the presentation slide must state the **AI Model name**, **reference**, and **Code Snapshot**.

### 1. Speech-to-Text Input (Mandatory AI Feature)
- **Architecture (Client-Side Processing):**
  - When the user clicks the Microphone button in the chat box, the client captures audio from the microphone and invokes `SpeechRecognition` (using **Google Web Speech API** or **OpenAI Whisper**) locally on the client machine to transcribe speech into a text string.
  - The transcribed string is placed into the chat input box (or sent directly) using standard `SEND_PRIVATE` or `SEND_GROUP` packets.
- **Network Benefit:** Raw audio streams/bytes never travel across the TCP chat socket, conserving bandwidth and keeping the application protocol lightweight.

### 2. Send Sticker (Predefined Stickers by ID)
- **Architecture:**
  - Preinstalled sticker image files (e.g., `assets/stickers/STK_001.png`) are bundled on every client machine.
  - Clicking a sticker sends a compact JSON packet containing only the identifier, such as `{"command": "SEND_STICKER", "sender": "Alice", "target": "Bob", "sticker_id": "STK_001"}`.
  - Upon receiving the packet, the recipient's UI loads `assets/stickers/STK_001.png` from local disk and renders the image inside the appropriate chat window (`R6`).

### 3. System-Wide Announcement (One-to-All Broadcast)
- **Architecture:**
  - Demonstrates **One-to-All Broadcast** across all connected sockets regardless of active chat rooms.
  - When an announcement is triggered on the server (or by an authorized admin command), the server iterates over all sockets in `clients` and sends `{"command": "ANNOUNCEMENT", "sender": "System_Admin", "payload": "Server will restart in 5 minutes!"}`.
  - Every connected client displays the announcement in a prominent top banner (with an optional alert sound) visible across all tabs.

### 4. Private-Message Read Receipt (`READ_ACK`)
- **Architecture (Inspired by Reliable Data Transfer `rdt2.0` ACK):**
  1. Every outgoing private message includes a unique `message_id` (e.g., `"MSG_001"` or `uuid4`).
  2. **Receiver Side (Bob):** When Bob opens Alice's private chat tab and displays message `MSG_001`, Bob's client automatically sends `{"command": "READ_ACK", "sender": "Bob", "target": "Alice", "message_id": "MSG_001"}` to the server.
  3. **Server Side:** The server checks `"target": "Alice"` and forwards the `READ_ACK` packet directly to Alice's socket.
  4. **Original Sender Side (Alice):** Alice's UI locates message `MSG_001` in her private chat window and updates its status indicator to `"Read ✓✓"`.

---

## Recommended Step-by-Step Implementation Roadmap

1. **Step 1 — Define JSON Framing & Helpers (`protocol.py`):**
   - Implement newline-delimited JSON (`\n`) encoding/decoding and a line buffer (or `Socket.IO` event wrappers) along with basic field validation.
2. **Step 2 — Build the Server Router & State (`server.py`):**
   - Maintain `clients = {"username": socket_obj}` and `groups = {"group_name": ["user1", "user2"]}`.
   - Implement per-client listener threads and route `CONNECT_REQ`, `SEND_PRIVATE`, `CREATE_GROUP`, `JOIN_GROUP`, `SEND_GROUP`, `SEND_STICKER`, `ANNOUNCEMENT`, and `READ_ACK`.
3. **Step 3 — Build the Client Session & Desktop GUI (`client.py`, `gui.py`):**
   - Implement the login screen, online user & group sidebars (`R4`, `R9`), and multi-tab chat rooms (`R5`, `R6`).
   - Connect the background socket listener thread to the GUI thread via a thread-safe `queue.Queue`.
4. **Step 4 — Integrate the 4 Special Features (`speech.py`, `assets/stickers/`):**
   - Wire the microphone button to `speech.py`, add the sticker picker popup, add the top announcement banner, and trigger `READ_ACK` when viewing private chat tabs.
