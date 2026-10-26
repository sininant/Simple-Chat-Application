# Simple Chat Application

A desktop client-server chat application for the Computer Network term project. The application uses Python socket programming over TCP/IPv4 and a custom JSON application protocol (using newline-delimited JSON `\n` framing, with `Socket.IO` also supported as an option).

> **Note:** The architecture, protocol format, and UI stack described in this repository are **working ideas (draft baseline)** aligned with the course requirements and can be adjusted by the team as implementation progresses. See [docs/project-ideas.md](docs/project-ideas.md) for the part-by-part breakdown (Part 1, Part 2, and Part 3).

## Project scope

The system uses separate server and client programs. The final demonstration uses at least two clients running on different physical computers (`R1`). Every client communicates with one central server over Socket Programming (`R2`); clients never communicate directly with one another.

Required functionality (Part 2 — `R3` to `R11`):

- unique usernames (`R3`) and a live connected-user list including the user's own name (`R4`);
- separate private and group chat rooms (`R5`), each with a chat box and chat window (`R6`);
- private messages visible only to the sender and receiver (`R7`);
- group creation with the creator as the sole initial member (`R8`), group discovery with member lists (`R9`), voluntary joining (`R10`), and member-only group messages (`R11`).

Planned special features (Part 3 — 4 Special Points):

1. **Speech-to-text input (Mandatory AI feature):** client-side microphone transcription into text before sending over the chat socket;
2. **Send Sticker:** predefined stickers stored locally in `assets/stickers/` and sent by `sticker_id`;
3. **Announcement:** system-wide broadcast alert displayed across all chat rooms;
4. **Read Receipt:** automatic `READ_ACK` confirmation (inspired by `rdt` acknowledgements) when a recipient views a private message.

## Network architecture (Part 1)

The recommended setup uses **two physical computers**:

- Computer A runs `server.py` and a separate `client.py` process for Alice.
- Computer B runs another `client.py` process for Bob.
- Both computers connect to the same Wi-Fi or Ethernet network.
- A third computer dedicated to the server is optional and is not required.

```text
Physical computer A                         Physical computer B
+---------------------------+               +---------------------------+
| Process 1: server.py      |<--------------| Process: client.py (Bob)  |
| Process 2: client.py      |  TCP/IPv4     |                           |
|            (Alice)        |               |                           |
| 0.0.0.0:5000              |   Wi-Fi /     +---------------------------+
+---------------------------+   Ethernet
```

Although the server and Alice run on the same physical computer, `server.py` and `client.py` are separate programs and separate operating-system processes. This satisfies `R1` because Alice and Bob, the two clients, run on different physical computers.

| Layer | Choice | Responsibility |
| --- | --- | --- |
| Application | Custom JSON chat protocol (`\n` JSON or `Socket.IO`) | Fulfills Presentation-layer (JSON encoding/framing) and Session-layer (login & group membership state) responsibilities |
| Transport | TCP | Provides reliable, ordered delivery and 4-tuple connection-oriented demultiplexing between each client and the server |
| Network | IP (IPv4) | Addresses and routes traffic between physical computers |
| Network access | Wi-Fi or Ethernet | Connects the physical computers on the same local network |

See [docs/protocol.md](docs/protocol.md) for the working protocol draft and [docs/project-ideas.md](docs/project-ideas.md) for the lecture-theory points and part-by-part implementation guide.

## Technology (Working Stack)

- **Language:** Python 3.11 or newer
- **Networking:** Python standard-library `socket` + `threading` + `json` (or `python-socketio` if the team opts for Socket.IO events)
- **Desktop GUI (Client):**
  - **Recommended:** `CustomTkinter` + `Pillow` (or standard-library `tkinter` with `ttk.Notebook` tabs) for simple multi-tab chat rooms (`R5`), inline `.png` sticker rendering, and 1-line `"Read ✓✓"` label updates
  - **Alternative:** `PySide6` / `PyQt5` (`QTabWidget` / `QListWidget` / `QTextBrowser`)
- **AI Feature (Speech-to-Text):** Client-side `SpeechRecognition` (with Google Web Speech API or OpenAI Whisper)
- **Quality checks:** `pytest` and Ruff

## Getting started

### Prerequisites

- Python 3.11+
- Git
- Two computers on the same network for the final integration test

### Setup

1. Clone the repository:

   ```bash
   git clone https://github.com/sininant/Simple-Chat-Application.git
   cd Simple-Chat-Application
   ```

2. Create and activate a virtual environment:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

   On Windows PowerShell:

   ```powershell
   py -m venv .venv
   .venv\Scripts\Activate.ps1
   ```

3. On Computer A, start the server in one terminal:

   ```bash
   python server.py
   ```

4. On Computer A, start Alice in a second terminal:

   ```bash
   python client.py --host 127.0.0.1 --port 5000
   ```

5. On Computer B, start Bob using Computer A's LAN IPv4 address:

   ```bash
   python client.py --host 192.168.1.10 --port 5000
   ```

   Replace `192.168.1.10` with the actual LAN IPv4 address of Computer A. Do not use `127.0.0.1` on Computer B because that address refers to Computer B itself.

## Planned project structure

```text
Simple-Chat-Application/
|-- .github/
|   `-- workflows/pr-check.yml
|-- assets/stickers/         Preinstalled sticker images (.png)
|-- docs/
|   |-- project-ideas.md     Part 1, Part 2, and Part 3 working design guide
|   `-- protocol.md          Shared client-server protocol draft
|-- tests/                   Automated unit and integration tests
|-- client.py                Client networking and session entry point
|-- gui.py                   Desktop user interface (CustomTkinter / Tkinter / PySide6)
|-- protocol.py              Message framing, encoding, and decoding helpers
|-- server.py                Multi-client server and routing logic
`-- speech.py                Client-side speech-to-text integration
```

The implementation files will be added incrementally through focused pull requests and can be adapted as the team finalizes each part.

## Quality checks

Run the same checks used by GitHub Actions before opening a pull request:

```bash
ruff check .
ruff format --check .
python -m compileall -q .
pytest -q
```

`pytest` becomes mandatory once test files are added. CI currently skips the test command when the repository contains no `test_*.py` files.

## Team workflow

1. Update `main`.
2. Create a focused branch such as `feature/private-message` or `fix/disconnect-cleanup`.
3. Implement and test one coherent change.
4. Push the branch and open a pull request into `main`.
5. Ask at least one teammate to review it.
6. Squash and merge after CI passes and review comments are resolved.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full workflow and naming conventions.

## Security and repository rules

- Never commit API keys, credentials, microphone recordings, `.env` files, or private IP configuration.
- Treat the sender identity from a client packet as untrusted; the server must verify or derive the sender from the registered socket.
- Validate message command/type, target, group membership (`R10`, `R11`), and `sticker_id` on the server.
- System announcements must originate from an authorized server-side or admin action.
- Keep each pull request focused and do not commit directly to `main` after initial setup.

## License

This repository is an academic course project. No open-source license has been selected.
