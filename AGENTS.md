# Agent Instructions

## Safety Rules (Issue Tracker & GitHub)

- **Disable Autonomous Issue Creation (`disable-model-invocation: true`)**: NEVER invoke `to-tickets`, `to-spec`, `triage`, or `wayfinder` autonomously, and NEVER run `gh issue create`, `gh issue edit`, `gh issue close`, or `gh issue comment` without explicit user instruction and prior approval of the draft content.

## Agent skills

### Issue tracker

Issues are tracked in GitHub Issues (`sininant/Simple-Chat-Application`) via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Default five canonical triage roles (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context layout (`GLOSSARY.md` at repo root and `docs/adr/`). See `docs/agents/domain.md`.

## Project context and architecture

- **Core design & protocol docs**: Read `docs/protocol.md` (client-server message contract and framing rules), `docs/project-ideas.md` (Part 1, Part 2, and Part 3 working design), and `CONTRIBUTING.md` (workflow and trust rules) before implementing features.
- **Headless testability & seams (`codebase-design` / `tdd`)**: Keep protocol framing (`protocol.py`), server state/routing (`server.py`), and client session state (`client.py`) decoupled from the desktop GUI (`gui.py`) and microphone hardware (`speech.py`) so automated tests in `tests/` can run headlessly in GitHub Actions without an active display (`$DISPLAY`) or audio device.
- **Server trust boundary**: Never trust the `sender` identity supplied inside a client payload; always derive or verify the sender from the authenticated server-side socket mapping.
- **Quality gate commands**: Run the repository checks before completing code changes:
  ```bash
  ruff check .
  ruff format --check .
  python -m compileall -q .
  pytest -q
  ```
