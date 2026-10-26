# Contributing

Use this workflow for every feature, fix, test, and documentation change.

## Before starting

Update your local `main` branch:

```bash
git switch main
git pull origin main
```

Create a branch from the updated `main` branch:

```bash
git switch -c feature/short-description
```

Use one of these prefixes:

- `feature/` for user-visible functionality;
- `bugfix/` for bug fixes;
- `test/` for test-only changes;
- `docs/` for documentation;
- `chore/` for tooling and repository maintenance.

Examples:

```text
feature/user-registration
feature/group-chat
bugfix/disconnect-cleanup
test/protocol-framing
docs/update-setup
```

## While working

- Keep each branch and pull request focused on one task.
- Follow the message contract in `docs/protocol.md`.
- Update the protocol document before merging a breaking message-format change.
- Never trust a sender name supplied by a client; derive it from the registered socket.
- Never commit API keys, credentials, `.env` files, recordings, or machine-specific configuration.
- Add or update tests for networking and protocol behavior whenever practical.
- Run the repository checks before committing.

```bash
ruff check .
ruff format --check .
python -m compileall -q .
pytest -q
```

## Commit messages

Use a short conventional commit message:

```text
feat: add username registration
fix: clean up disconnected clients
test: cover partial TCP reads
docs: describe group message contract
chore: configure Python CI
```

## Pull requests

1. Push the branch to GitHub.
2. Open a pull request into `main`.
3. Explain what changed and why.
4. List the requirement or feature affected, such as `R7 private messages`.
5. Explain how the change was verified, including multi-computer testing when relevant.
6. Ask at least one teammate to review it.
7. Resolve review comments and wait for CI to pass.
8. Prefer **Squash and merge** to keep the `main` history concise.
9. Delete the branch after it is merged.

Do not commit directly to `main` after the initial repository setup.

## Definition of done

A change is ready to merge when:

- its behavior matches the relevant requirement and protocol contract;
- error and disconnect cases are handled;
- affected checks and tests pass;
- no secrets or machine-specific files are included;
- documentation is updated when setup or message behavior changes;
- at least one teammate has reviewed the pull request.
