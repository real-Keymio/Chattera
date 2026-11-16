# Privacy, compliance and security

## Chattera has no accounts and holds no third-party credentials

- Chattera provides **no sign-in service** of its own and has no server of its own. It runs on your computer.
- **API connections**: you paste a key that *you* obtained from a provider. It is stored only in the `keys` folder of
  your data directory (`%LOCALAPPDATA%\Chattera\keys`, access restricted to your Windows account), never sent to the
  browser after saving, never written to chat history or logs (keys are redacted from log output).
- **Local subscription CLIs** (Claude Code, Codex): if you choose to connect one, Chattera calls **the official CLI you
  installed yourself, signed in with your own account**. Chattera does not read, copy, store or print the CLI's
  credential files, does not log in on your behalf, and only asks the CLI's own status command whether you are signed in.

## Using subscription CLIs: your responsibility

- Detecting that a CLI can be called non-interactively **does not mean** the vendor allows a third-party application
  to use a consumer subscription that way. Before switching such a connection on, **check that vendor's terms of
  service yourself**. Discovered CLIs are added switched off for this reason.
- Chattera is **not affiliated with, endorsed by or officially supported by** Anthropic, OpenAI, Google or any other
  model provider. Product names are used only to identify the software you connect.
- Chattera is not, and must not be presented as, a way to "use a subscription instead of an API".

## Where your content goes

| What | Goes to |
|---|---|
| Messages and attachments for a reply through an **API connection** | That connection's provider (the `base_url` you configured). |
| The same through a **third-party relay / proxy** | The relay operator first, then whichever provider it forwards to. Choose relays you trust. |
| The same through a **subscription CLI** | The CLI's vendor, under your account with that vendor. |
| **Web search** queries (independent, shared or hybrid retrieval) | The search service in use: the provider's own search for its connection, or the configured web search (DuckDuckGo by default; Tavily or Brave if you add a key) and scholarly indexes (OpenAlex, Europe PMC, arXiv, Crossref). Pages that a model opens are fetched from their websites. |
| **Private** messages | Only the participants in the audience receive them. Private content is never sent to a search service automatically: independent search is not allowed in a private context, and shared search needs a query you type yourself. |
| Everything else (chats, settings, library, logs) | Stays in your data directory. Chattera sends no telemetry and makes no update checks. |

Each reply records which connection, model and search route it used, so you can see after the fact where it went.

## Security

- Chattera listens on **127.0.0.1 only** by default. It was not designed for multi-user access and has **no user
  authentication** beyond a per-session token injected into the page. **Do not expose it to a local network or the
  internet.** Chattera's entry point offers no option to listen on another address; doing so by modifying the code is
  unsupported and unsafe.
- Models in Chattera cannot run commands or write files: API replies have no such tools, and CLI connections run in an
  empty sandbox directory with file-writing and shell tools switched off (see [CLI_ADAPTER.md](CLI_ADAPTER.md)).
- Model output, web pages and documents are treated as untrusted text, never as instructions to Chattera.
- The portable package is not code-signed. Download it only from the official release page.

## Your data

All data is in one folder you control (see [INSTALL.md](INSTALL.md#3-where-your-data-lives)). Deleting that folder
deletes everything Chattera stored. Exports (设置 → 数据 → 导出) contain private messages; keep them private.

---

## 中文摘要

- Chattera 不提供任何登录服务，不接触、不保存第三方订阅凭据。API Key 只保存在本机数据文件夹的 `keys` 中。
- 使用本机订阅 CLI 时，使用的是你自己安装的官方 CLI 和你自己的账户；是否允许第三方软件这样使用，请自行确认各服务商条款。
  Chattera 不是任何模型厂商的官方产品，也不以“用订阅代替 API”作为用途。
- 数据去向：API 连接的内容发往对应服务商；经过第三方中转时会先经过中转方；联网检索的查询词会发往搜索服务；私聊内容不会自动发给搜索服务。
- 安全：默认只监听本机（127.0.0.1）。本项目没有为多人访问设计身份认证，不要暴露到局域网或公网。
