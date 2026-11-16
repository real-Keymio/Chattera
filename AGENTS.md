# AGENTS.md — instructions for coding agents working on Chattera

## What this project is

Chattera is a **chat-first group chat between one person and several AI models**, running locally on the user's
computer. It is *not* an agent platform: models answer messages; they do not run commands, edit files or carry out
tasks. Keep it that way. Do not add autonomous loops, shell tools, file-writing tools or "agent mode" features.

## Install, test, run

Windows (PowerShell):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -NoStart   # Python check, .venv, dependencies, tests
.venv\Scripts\python.exe src\chattera.py                               # start; prints the local address
.venv\Scripts\python.exe -m pytest -q tests                            # tests only
```

macOS / Linux (untested): `scripts/setup.sh --no-start`, then `.venv/bin/python src/chattera.py`.

- Python 3.11+. OCR is optional from source (`-WithOcr` or `pip install -r requirements-ocr.txt`) and required to build the
  portable package, which ships with it.
- Data goes to `%LOCALAPPDATA%\Chattera` (or `--home <folder>`); never into the repository.
- Tests must pass in a clean checkout with no personal configuration, no installed CLIs and no network keys.
- Portable build: `scripts\build_portable.ps1` (see docs/INSTALL.md).

## Three layers: connection → model → participant

| Layer | What it is | Where it is managed |
|---|---|---|
| Connection | How to reach a provider: an API endpoint + key, or a local subscription CLI | Settings → 模型与服务 |
| Model | A model offered by a connection (its own catalogue entry, capabilities, context window) | same place, per connection |
| Participant | A named voice in the chat (`claude`, `codex`, `antigravity`, `deepseek`, or one the user adds) bound to a connection and model | Settings, and per chat in the room panel |

Rules:

- **A new model is added as a new participant.** Never reuse or rename an existing participant id to point it at a
  different model: message history is attributed by participant id, and borrowing an id rewrites who said what.
- Adding an API connection needs **no code change**. Use the settings screen (OpenAI-compatible, Anthropic Messages,
  Google generateContent protocols are supported).
- Participant ids (`claude`, `codex`, `antigravity`, `deepseek`, …), message and storage formats, and API semantics are
  stable. The product rename to Chattera did not change them; do not change them either.

## Core mechanisms you must not change

These modules hold the guarantees users rely on. Changes here need an explicit request from the maintainer:

- **Permissions and visibility** — private messages, audiences, who may see what (`chat_visibility.py`, the audience
  checks in `chat_runtime.py`, `chat_attachments.validate`).
- **Frozen snapshots** — each reply is built from a snapshot frozen when it starts (`chat_rooms.ContextBuilder`,
  `chat_tasks.py`); regenerations reuse it.
- **Append-only history** — messages, versions and shares are appended, never rewritten (`store.py`, `chat_versions.py`).
- **Version chains and share attribution** — replacements and public shares keep their origin (`chat_versions.py`).
- **Research library permissions** — per-room collections, holders and shared flags (`chat_library.py`).
- **API key storage** — keys only in `<data>/keys/*.key`, never returned to the browser, logs or history (`chat_secrets.py`).

## Adding a subscription CLI

Write an adapter in `src/cli_adapters/` and register it; nothing in the core changes. Read
**[docs/CLI_ADAPTER.md](docs/CLI_ADAPTER.md)** first: it defines the contract (discover, model list, authentication,
generate, cancel, tools, capabilities, usage), the tests an adapter must pass, and the found / adapted / can-chat
states. Detecting a non-interactive flag does not mean the vendor allows third-party apps to use a consumer
subscription — say so in the adapter's docs and leave the decision to the user.

## Layout

```
src/                 application (flat modules; entry point src/chattera.py)
src/cli_adapters/    one module per subscription CLI
src/static/mc/       web interface (ES modules, no build step)
src/defaults/        first-run configuration template
launcher/            Chattera.exe source (C#, built with the .NET Framework compiler)
scripts/             setup, portable build, license collection, set_api_key.py
docs/                INSTALL, CLI_ADAPTER, ARCHITECTURE, PRIVACY_AND_COMPLIANCE, user-guide sources
tests/               pytest suite (adds src/ to sys.path via conftest.py)
```

## Conventions

- No personal paths, user names, keys or chat data in the repository — ever. `git grep` before committing.
- Keep claims honest in code and UI: a capability is "UNKNOWN" until a real call verified it; a model being listed is
  not the same as it having been called successfully; search results are only cited when they were actually retrieved.
- User-facing text is Chinese first; code comments and docs for developers are English.
