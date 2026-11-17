# Changelog

## 0.1.3 — OpenAI Responses API

- **OpenAI Responses protocol.** Newer OpenAI reasoning models refuse function tools together with reasoning effort on
  `/chat/completions`; connections to OpenAI can now use `/v1/responses` instead, with tool calls, reasoning effort and
  image input. Requests are stateless (`store: false`); encrypted reasoning items are passed back unchanged between
  tool rounds. The OpenAI preset uses it for new connections.
- **OpenAI official web search** on Responses connections to `api.openai.com`, recorded like the other official
  searches (queries, pages opened, citations). Switch it off per connection under *Official web search*.
- A connection test that fails with OpenAI's "use /v1/responses" message offers the switch in one click.
- **Delete chats.** A chat can now be deleted permanently from its menu or the room settings, and the archive can be
  emptied in one step. Everything recorded only for that chat goes with it: messages and versions, attachments and their
  parse cache, reply records, summaries, automatic memory, shared-retrieval bundles, canvas and research library. Cached
  library files are removed only when no other chat uses them.
- **Room to work.** Limits sized for long research rather than quick chats:
  - tool calls per reply: 60 search/read calls (was 8) and 80 library calls; up to 20 request rounds on tool-enabled API connections;
  - one read returns 20,000 characters of a page or paper (was 5,200); searches return up to 20 results with abstracts up to 3,000 characters;
  - output limit 32,000 tokens by default, up to 128,000; API timeout 900 s by default, up to 3,600 s;
  - older long messages are shown in full up to 32 KB before being excerpted (scaled to the model's budget);
  - attachments: 12 per message, 50 MB each; OCR up to 300 pages;
  - automatic memory up to 40,000 bytes; 50 pins of up to 8,000 characters; longer library notes and quotes.
- **Nothing is thrown away for hitting a limit.** When the tool budget or the request rounds run out, the model gets one last request
  with tool calls switched off and answers from what it found. An answer that reaches the output limit is kept, with a note that it
  was cut off.
- **Caches live with the library.** Parsed attachments, rendered PDF pages and downloaded web pages and PDFs are kept in
  `<library folder>\cache`, so a library in a cloud-synced folder (e.g. Google Drive) takes them along; they fall back to the local
  data folder while that folder is unreachable. The library cache budget defaults to 20 GiB.
- **Branches are independent copies**: a branch now also gets its own copy of the research library, so deleting either
  chat never affects the other.
- **Fix:** replies from a participant whose id ends in a number (e.g. `claude-fable-5-2`) were drawn as your own
  messages; on a slow start, other added participants could look the same until the next change.
- **Fix:** menus opened inside Settings (the ⋯ menu on each model) were shown but could not be clicked.

## 0.1.2 — Easier model settings, clearer errors

- **Model settings in plain sight.** Each model in Settings → Models & services now shows switches for tool calls and
  image input, its reasoning-effort levels and its context window, instead of hiding them in an edit dialog.
- **Compatibility section** on every API connection, with the settings providers differ on: the output-limit
  parameter (`max_tokens` or `max_completion_tokens` for newer OpenAI models), the reasoning-effort parameter, the
  Anthropic workspace ID (`anthropic-workspace-id`, for keys not scoped to a workspace) and Bearer auth for Google.
- **Failed tests explain themselves.** A connection test that fails for a known reason says which setting fixes it,
  and offers the fix where it can (for example “Use max_completion_tokens and retry”).
- **Clearer errors.** A failed model listing shows the provider's own message; a subscription CLI that exits with an
  error shows what it said, and a sign-in problem is named as one.
- **Claude Code detection.** Standalone installs are preferred; the copy bundled with the Claude desktop app is the
  fallback, and a connection whose executable moved after an app update is found again at start-up.
- **Fix:** the reasoning-effort picker in model settings failed with “t is not a function” in 0.1.1.

## 0.1.1 — English interface

- **English and Chinese interface.** Chattera follows the Windows display language; switch any time in
  Settings → General → Language (stored in `config\settings.json`). Covers the whole web interface, server messages,
  the demo chat, and the launcher's start-up window and tray menu.
- **English quick start** (`01_Start_Here.pdf`) in the ZIP and under Help. The full guide is still Chinese only.
- Room names, chats and anything you typed stay as written; switching language never rewrites them.
- Quick prompts and “respond” presets follow the interface language; lens methods sent to models stay English.

## 0.1.0 — first public release (pre-release)

A group chat for you and your AIs, running on your own computer.

- **Group chat with several models**: address everyone or one by name, let a model respond to another, quote and
  follow up, private messages to a subset, independent answers that stay hidden from each other until you publish them.
- **Connections → models → participants**: add API connections in Settings (OpenAI-compatible, Anthropic Messages,
  Google generateContent; presets for common providers). Any model can become its own participant.
- **Web search**: independent (each model's own official search, or Chattera's), shared (one search for everyone) or
  hybrid; every answer records what was actually searched and read.
- **Reading**: 1–6 synchronised panes; a free canvas for rounds, messages, quoted passages, PDF pages and sources,
  saved per chat in the data folder.
- **Research library** per chat, with DOI / arXiv identity, notes, citations and on-demand full text.
- **Documents**: PDF, Word, HTML, text and images; text recognition (OCR) for scans in the Windows package.
- **Windows portable package**: unzip and run, no Python needed; demo mode without any key; two illustrated user
  guides (Chinese).
- **Your data stays local**: `%LOCALAPPDATA%\Chattera`, or next to the program in portable mode; upgrading the program
  never touches it.
- Adapters for locally installed official CLIs (off by default; see the compliance notes).

Built on [agentchattr](https://github.com/bcurts/agentchattr) (MIT). agentchattr's terminal agents, MCP bridge, jobs,
schedules, sessions and original web page are not included.
