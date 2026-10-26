# Domain Docs

How the engineering skills should consume this repo's domain and architecture documentation when exploring the codebase.

## Before exploring, read these

- **`GLOSSARY.md`** at the repo root (when present) for canonical project terminology.
- **`docs/protocol.md`** for the client-server JSON message contract, framing rules, and field semantics.
- **`docs/project-ideas.md`** for the working design across Part 1 (System Architecture), Part 2 (Fundamental Implementation `R3`–`R11`), and Part 3 (Special Points `SP1`–`SP4`).
- **`docs/adr/`**: read ADRs that touch the area you're about to work in.

If `GLOSSARY.md` or `docs/adr/` don't exist yet, **proceed silently**. Don't flag their absence; don't suggest creating them upfront. The `/domain-modeling` skill (reached via `/grill-with-docs` and `/improve-codebase-architecture`) creates them lazily when terms or decisions actually get resolved.

## File structure

This repository uses a single-context layout at the root:

```text
/
├── AGENTS.md
├── GLOSSARY.md                (created lazily when domain terms are resolved)
├── docs/
│   ├── adr/                   (created lazily for architectural decision records)
│   ├── agents/                (agent skill and tracker configuration)
│   ├── project-ideas.md       (Part 1, Part 2, and Part 3 working design guide)
│   └── protocol.md            (client-server chat protocol contract)
├── assets/stickers/           (preinstalled sticker PNG assets)
├── tests/                     (automated unit and integration tests)
├── client.py
├── gui.py
├── protocol.py
├── server.py
└── speech.py
```

## Use the glossary's and protocol's vocabulary

When your output names a domain concept or message type (in an issue title, a refactor proposal, a hypothesis, or a test name), use the terms defined in `GLOSSARY.md` and `docs/protocol.md`. Don't drift to synonyms that the glossary or protocol contract avoids.

If the concept you need isn't in `GLOSSARY.md` yet, that's a signal: either you're inventing language the project doesn't use (reconsider) or there's a real gap (note it for `/domain-modeling`).

## Flag ADR conflicts

If your output contradicts an existing ADR or protocol rule in `docs/protocol.md`, surface it explicitly rather than silently overriding:

> _Contradicts ADR-0001 / `docs/protocol.md` (newline-delimited JSON framing), but worth reopening because…_
