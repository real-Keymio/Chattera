# Changelog

## 0.2.0 — Ready for real research

The first version meant for everyday use: all three API families work out of the box, replies take the time a
research question needs, and nothing a model found is lost because it hit a limit.

**Connections that work**
- **OpenAI Responses API.** Newer OpenAI reasoning models refuse function tools together with reasoning effort on
  `/chat/completions`; OpenAI connections now use `/v1/responses` (tool calls, reasoning effort, images; stateless,
  `store: false`). The OpenAI preset uses it, and a connection test that fails with OpenAI's “use /v1/responses”
  message offers the switch in one click. OpenAI's own web search is available on these connections.
- Anthropic keys that are not scoped to a workspace, and slow relays, are handled (workspace ID in *Compatibility*,
  longer timeouts).

**Replies that take the time they need**
- **Self-paced, as in a web chat.** No fixed number of searches or reads: a model works until it has enough, then
  answers. A reply is bounded by its time limit (Settings → Search & tools → *Longest time per reply*: 10 min to 2 h,
  or no limit; 30 min by default) and by your stop button. A running reply shows what it is doing and how many steps
  it has taken.
- **Nothing is thrown away for hitting a limit.** Near the time limit, or if a model keeps repeating the same call, it
  is asked to answer from what it has. An answer that reaches the output limit is kept, marked as cut off.
- **Research agent** (on by default): every chat has a **chat folder** where participants keep notes, tables and
  downloaded open-access papers; files a reply saved are listed under it and open in a new tab (Room settings →
  *Open chat folder*). Models cannot run commands or reach anything outside that folder. Locally installed official
  CLIs, if you enabled them, may use their own web search for messages everyone can see.
- **Room to work**: one read returns 20,000 characters; searches return up to 20 results; output limit 32,000 tokens by
  default (up to 128,000); 12 attachments per message, 50 MB each; OCR up to 300 pages; longer history excerpts,
  automatic memory, pins and library notes.

**Your data**
- **Delete chats** permanently from the chat menu or the room settings, or empty the archive in one step. Everything
  recorded only for that chat goes with it (messages and versions, attachments, reply records, summaries, canvas,
  research library, chat folder); shared cached files go only when no other chat uses them.
- **Branches are independent copies**, research library included: deleting either chat never affects the other.
- **Caches live with the library**: parsed attachments, rendered PDF pages and downloaded pages and PDFs are kept in
  `<library folder>\cache`, so a library in a cloud-synced folder takes them along (local fallback while it is
  unreachable).
- **Automatic summaries** can be switched on from the room panel (Room settings → Summary): for all chats, paused per
  chat, with a choice of which installed CLI writes them. They stay off in a new install.

**Clearer interface**
- Message box: *Analysis framework* (was “lens”), *Everyone can see / Only X can see* (was “public”), *Answers visible /
  Blind answers* (was “discussion / independent”), each with a short explanation.
- **Fixes:** replies from participants whose id ends in a number (e.g. `claude-fable-5-2`) were drawn as your own
  messages; menus inside Settings (the ⋯ menu on each model) could not be clicked; the automatic-summary button did
  nothing in a new install.

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
