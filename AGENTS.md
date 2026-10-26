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
