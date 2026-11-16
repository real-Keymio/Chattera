# Contributing

Thanks for your interest. Chattera is a small, personal project; issues and focused pull requests are welcome.

## Before you start

- Read [AGENTS.md](AGENTS.md). It is written for coding agents but applies to people too: what Chattera is (a chat,
  not an agent platform), the connection → model → participant layers, and the core mechanisms that must not change
  (permissions and visibility, frozen snapshots, append-only history, version chains, share attribution, library
  permissions, key storage).
- For anything larger than a fix, open an issue first so we can agree on the approach.

## Development

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -NoStart
.venv\Scripts\python.exe -m pytest -q tests
.venv\Scripts\python.exe src\chattera.py --home D:\some\test-folder     # keep test data away from your real chats
```

## Pull requests

- Tests must pass on a clean checkout (`pytest -q tests`); add tests for behaviour you change.
- **No personal data**: no API keys, tokens, chat content, absolute personal paths, user names or e-mail addresses in
  code, tests, docs, screenshots or commit messages.
- A new subscription CLI goes in as an adapter (`src/cli_adapters/`) following [docs/CLI_ADAPTER.md](docs/CLI_ADAPTER.md),
  with the listed tests; it must not touch core modules.
- New model providers need no code: they are added in Settings.
- Keep user-facing text honest: do not claim a capability that has not been verified, and keep uncertain values `UNKNOWN`.
- User-facing text is Chinese first; code, comments and developer docs are English.

By contributing you agree that your contribution is licensed under the MIT License of this repository.
