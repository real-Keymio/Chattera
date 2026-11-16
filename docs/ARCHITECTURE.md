# Architecture

Chattera is a local web application: a Python server (FastAPI + uvicorn) on `127.0.0.1` and a browser interface
written as plain ES modules (no build step). It grew out of [agentchattr](https://github.com/bcurts/agentchattr);
its message store, WebSocket server and participant registry are still the base (`app.py`, `store.py`, `registry.py`,
`router.py`), and the chat-first features are layered on top of it (`chat_*.py`). agentchattr's terminal agents, MCP
bridge, jobs, schedules, sessions and its original web page are not part of Chattera.

```
Chattera.exe (launcher, C#)                         browser window (Edge app / default browser)
   │ starts, finds port in run/server.json             │ HTTP + WebSocket, session token injected into the page
   ▼                                                    ▼
src/chattera.py ── data home, first-run config, CLI discovery, free ports ──► src/run.py ─► app.py (FastAPI)
                                                                                   │
            ┌──────────────────────────────────────────────────────────────────────┤ chat_runtime.install()
            ▼                          ▼                          ▼                ▼
   chat_registry (connections,   chat_rooms (rooms,        chat_tasks (one reply  chat_library_api, chattera_setup,
   models, participants, keys)   ContextBuilder: frozen    = one task; snapshot,  chattera_workspace, chat_versions,
                                 snapshot per reply)       cancel, record)        chat_discussion, rolling_memory
                                                                 │
                         ┌───────────────────────────────────────┼─────────────────────────┐
                         ▼                                       ▼                         ▼
               chat_api.APIBackend                  chat_backends.CLIBackend       DemoBackend / MockBackend
               (OpenAI-compatible, Anthropic,       + cli_adapters/<cli>.py         (no model call)
                Google; api_protocols.py)           (subprocess in a job object;
                                                    tool server chat_task_mcp.py)
```

## Layers

- **Connection → model → participant** (`chat_registry.py`). A connection is an API endpoint with its key file, or a
  subscription CLI. Each connection has a model catalogue. A participant (`claude`, `codex`, `antigravity`,
  `deepseek`, or one added by the user) is bound to a connection and model; per chat and per message the choice can
  be overridden (`model_settings.merge`). History is attributed by participant id.
- **Reply = task** (`chat_tasks.py`). When a message is sent, each addressed participant gets a task. The context
  is built once into a **frozen snapshot** (`chat_rooms.ContextBuilder`): visible history only, summaries, pinned
  items, attachments, search plan, library view. The backend receives only that snapshot. Regeneration reuses it.
- **Visibility** (`chat_visibility.py`, checks in `chat_runtime.py`). Private messages carry an audience; a
  participant outside it never receives the message, its attachments or quotes of it; tools read through the same filter.
- **History** (`store.py`, `chat_versions.py`). Append-only JSONL. Edits, regenerations and public shares are new
  events that point at their origin.
- **Search** (`chat_search.py`, `chat_evidence.py`). Independent (each model uses its connection's official search,
  or Chattera's tools), shared (retrieved once, same bundle to everyone) or hybrid. Every reply records what was
  searched and read.
- **Research library** (`chat_library.py`). Per-room index with holders, shared flags, notes and citations;
  content-addressed file cache shared by rooms.
- **Subscription CLIs** (`cli_adapters/`). See [CLI_ADAPTER.md](CLI_ADAPTER.md).

## Processes

- `chattera-server.exe` / `python src/chattera.py` — the server. Helper scripts run as child processes of the same
  executable (`--worker chat_task_mcp | attachment_worker | image_prepare`) so the packaged build needs no Python.
- `chat_task_mcp.py` — read-only room tool server offered to CLI replies over stdio (MCP).
- `attachment_worker.py` — document parsing in a separate, time-limited process.
- Chattera opens exactly one listening port (the web port, 127.0.0.1 only).

## Data

All user data lives in the data home (`chattera_paths.py`): `%LOCALAPPDATA%\Chattera` by default, `<program>\data`
in portable mode. Nothing is written to the program folder. See [INSTALL.md](INSTALL.md#3-where-your-data-lives).

## Interface

`src/static/mc/` — `main.js` (start), `state.js` (store and WebSocket), `panes.js` (1–6 reading panes), `canvas.js`
(free canvas), `composer.js` (who answers, privacy, search, lenses), `models.js` / `studio.js` (settings),
`panel.js` (room panel), `library.js`, `evidence.js`, `workspace.js` (per-room layouts saved on the server),
`setup.js` (welcome, demo, guides).

## Rename note

The product is named Chattera; Python module names (`chat_*.py`, `app.py`), participant ids, storage formats,
environment variables prefixed `AGENTCHATTR_` and API paths are unchanged on purpose: renaming them would touch
every stored record and every integration for no user-visible benefit. They may be renamed in a later, separate step.
