# Changelog

## 0.2.5 — Whole history, cached between turns, copy and export

- **Subscription CLIs get the whole history again.** Claude Code and Codex were given room for only about 31,000
  “tokens” of history: the budget reserved space for tool results these CLIs manage themselves, reserved the maximum
  output, and counted every byte as a token (a Chinese character as three). In a research chat that meant the first
  round was already left out by the second (“part of the history was not included”). Now CLIs keep no tool reserve,
  the output reserve follows the connection's output limit, and tokens are estimated per character (Chinese about one,
  English about four characters a token) with 15 % of the room kept free. With the 256K setting a CLI now gets about
  256,000 tokens of history. One very long message still takes at most a quarter of the room.
- **The input stays the same from turn to turn, so the provider's cache works across turns.** Each participant keeps
  the summary it read last time while the newer messages still fit, and the part of its input that did not change
  since its last turn is sent as a separate block marked for caching (Claude Code and Anthropic connections); new
  messages and the question follow in a second block. Subscription CLIs run in one fixed, empty working folder per
  chat and participant, because the folder is part of their own system prompt. OpenAI connections send a hashed
  `prompt_cache_key` per chat and participant. Measured: the second and third turns of a chat read 93–96 % of their
  input from the cache (Claude Code and the Anthropic API). Codex picks its own cache routing; its hit rate varies.
- **If a model still refuses the input as too long**, the reply is retried once with less history (60 % of the room).
- **Copy a round with who said what**: “Copy round” (on the round, or in a message's ⋯ menu) gives
  `User:` / `<participant>:` before each message, ready to paste anywhere.
- **Select several messages** (⋯ → *Select several…*): click messages, or a question to take its answers with it;
  then copy them together or **export them as a Markdown file** with the chat's name, the export time and the
  participants.
- The scholarly search tools the CLIs see now describe what they really search (OpenAlex, Europe PMC, Crossref,
  arXiv, with abstracts); they said “Crossref metadata only”.

**中文说明**
- **订阅版 CLI 重新带上完整历史。** 之前 Claude Code 和 Codex 的历史预算只有约 3.1 万：给 CLI 自己管理的工具结果预留了空间、按最大值预留输出，而且一个字节算一个 Token（一个汉字算三个），研究对话到第二轮就丢了第一轮。现在 CLI 不再预留工具空间，输出预留按连接的输出上限计算，Token 按字符估算（中文约 1 字 1 个，英文约 4 个字符 1 个），并留出 15% 余量。256K 档下约有 25.6 万 Token 的历史空间；单条很长的消息最多占四分之一。
- **每轮输入的开头保持不变，服务商的缓存可以跨轮命中。** 每位参与者在新消息还放得下时沿用上次读过的摘要；和上一轮相同的那部分输入单独作为一块并标记缓存（Claude Code 和 Anthropic 连接），新消息和问题放在第二块。订阅版 CLI 在每个聊天、每位参与者固定的空文件夹里运行（文件夹会写进 CLI 自己的系统提示）。OpenAI 连接按聊天和参与者发送哈希后的 `prompt_cache_key`。实测第二、三轮有 93–96% 的输入来自缓存（Claude Code 和 Anthropic API）；Codex 的缓存路由由它自己决定，命中率不稳定。
- **模型仍然报“输入过长”时**，自动用较少的历史（预算的 60%）重试一次。
- **复制本轮带角色**：每条消息前写“用户：”或参与者名称，可以直接粘贴。
- **多选复制和导出**（⋯ → 选择多条…）：点选消息，点提问会连同它的回答一起选中；可以一起复制，或导出为 Markdown 文件，文件开头写对话名称、导出时间和参与者。
- CLI 看到的文献检索工具说明改为实际使用的来源（OpenAlex、Europe PMC、Crossref、arXiv，含摘要），之前误写为“只查 Crossref 元数据”。

## 0.2.1 — Prompt caching for Anthropic

- **Anthropic connections now use prompt caching.** Each request marks the parts that do not change — the system
  prompt and the chat context — so the repeated requests of one reply (every tool call resends the context) are read
  from Anthropic's cache instead of at full price. Other providers cache on their own and are unchanged. Relays that
  speak the Anthropic protocol without caching are detected on the first refusal and used without it from then on.
- **Cache lifetime option** (connection → Compatibility): default (about 5 minutes) or longer (about 1 hour), or off.
  The longer lifetime costs more to write and pays off only when you keep coming back to the same chat within an hour.
- **See the hit rate**: a reply's details show its input tokens, how many came from the cache and how many were
  written to it; each connection in Settings → Models & services shows its average over recent replies. Providers
  that report no cache information show “unknown”, never 0 %; for subscription CLIs the figure is for reference only.

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
