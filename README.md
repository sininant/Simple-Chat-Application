# Simple Chat Application

A desktop client-server chat application for the Computer Network term project. The application uses Python TCP sockets over IPv4 and a custom length-prefixed JSON application protocol.

## Project scope

The system uses separate server and client programs. The final demonstration uses two clients running on different physical computers. Every client maintains a TCP connection to one central server; clients never communicate directly with one another.

Required functionality:

- unique usernames and a live connected-user list;
- separate private and group chat rooms;
- private messages visible only to the sender and receiver;
- group creation, group discovery, voluntary joining, and member-only messages.

Planned special features:

- speech-to-text input (AI feature);
- predefined stickers sent by sticker ID;
- system-wide announcements;
- private-message read receipts.

## Network architecture

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

Although the server and Alice run on the same physical computer, `server.py` and `client.py` are separate programs and separate operating-system processes. This satisfies the requirement because Alice and Bob, the two clients, run on different physical computers.

| Layer | Choice | Responsibility |
| --- | --- | --- |
| Application | Custom length-prefixed JSON protocol | Defines registration, chat, group, sticker, announcement, and read-receipt messages |
| Transport | TCP | Provides reliable, ordered delivery between each client and the server |
| Network | IPv4 | Addresses and routes traffic between computers |
| Network access | Wi-Fi or Ethernet | Connects the physical computers |

See [docs/protocol.md](docs/protocol.md) for the protocol contract.

## Technology

- Python 3.11 or newer
- Python standard-library `socket`, `threading`, `json`, and `tkinter` modules
- Speech-recognition dependency to be finalized before the AI feature is implemented
- `pytest` and Ruff for automated checks

This project uses raw TCP sockets, not Socket.IO or WebSocket.

## Getting started

### Prerequisites

- Python 3.11+
- Git
- Two computers on the same network for the final integration test

Tkinter is included with the standard Python installer on Windows and macOS. Some Linux distributions require a separate `python3-tk` package.

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
|-- assets/stickers/       Preinstalled sticker images
|-- docs/protocol.md       Shared client-server protocol contract
|-- tests/                 Automated unit and integration tests
|-- client.py              Client networking entry point
|-- gui.py                 Tkinter user interface
|-- protocol.py            Framing, encoding, and decoding helpers
|-- server.py              Multi-client TCP server
`-- speech.py              Speech-to-text integration
```

The implementation files will be added incrementally through focused pull requests. Dependency management will be added when the team selects a speech-to-text library; the core socket application currently uses Python's standard library only.

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
- Treat the sender identity from a client packet as untrusted; the server must derive the sender from the registered socket.
- Validate message type, payload size, target, group membership, and sticker ID on the server.
- System announcements must originate from an authorized server-side action.
- Keep each pull request focused and do not commit directly to `main` after initial setup.

## License

This repository is an academic course project. No open-source license has been selected.
