# Chattera

**A group chat for you and your AIs.**
Bring multiple AI models into one conversation. Talk naturally, explore different perspectives, and keep the discussion going.

![Chattera: four models answering in one chat](docs/user-guide/img/02-demo-chat.jpg)

Chattera runs on your own computer. You add the models you want — through their API (DeepSeek, OpenAI, Anthropic,
Google Gemini, Qwen, Kimi, GLM, OpenRouter, any OpenAI-compatible endpoint…) — and talk to them together: ask everyone,
ask one, let them respond to each other, compare independent answers, read side by side, and lay out a discussion on a
free canvas. It is a chat, not an agent platform: models answer; they do not run commands or edit files.

> **Cost.** Chattera is free and open source (MIT). When you use a real model, the model provider (or the relay you
> choose) bills you directly for API usage. Demo mode calls no model and costs nothing.

## Five-minute start (Windows, no programming)

1. Download **`Chattera-Windows-x64.zip`** from the [Releases page](https://github.com/real-Keymio/Chattera/releases) and unzip it anywhere.
2. Double-click **`Chattera.exe`**. The first start takes about ten seconds.
3. In the welcome window choose **进入演示** (demo, no key needed) — or **添加连接**: pick a provider, paste the API key
   from that provider's website, choose a model.
4. Start a new chat and ask something. That's it.

The ZIP contains two illustrated guides in Chinese: `01_先看这个.pdf` (two pages, first chat) and
`02_Chattera完整使用指南.pdf` (everything else). They are also under **帮助** inside the app.

## What it does

| | |
|---|---|
| **Group chat** | Several models in one conversation; address everyone or one by name; let a model respond to another's answer. |
| **Independent answers** | Everyone answers without seeing the others first — for comparing views honestly. |
| **Private messages** | Talk to a subset; the others never see it (enforced on the server, including for attachments and quotes). |
| **Web search** | Each model uses its provider's own search when it has one (independent retrieval, the default), or everyone gets the same retrieved material (shared), or both (hybrid). Every answer shows what was actually searched and read. |
| **Reading layouts** | 1–6 synchronized reading panes, and a free canvas for rounds, single messages, quoted passages, PDF pages and sources. |
| **Research Library** | Per-chat library of the papers and pages the models actually used, with DOI/arXiv identity, notes and on-demand full text. |
| **Documents** | PDF, Word, HTML, text and images as attachments; text recognition (OCR) for scanned PDFs, built into the portable package. |
| **Your data stays local** | Chats, settings, keys and the library live in `%LOCALAPPDATA%\Chattera` (or next to the program in portable mode). |

![Free canvas](docs/user-guide/img/13-canvas.jpg)

## Two ways to install

- **Regular users — Windows portable package.** Unzip and run; no Python needed. See [docs/INSTALL.md](docs/INSTALL.md#portable).
- **Developers — from source, optionally with your coding agent.** `scripts\setup.ps1` checks Python, creates a virtual
  environment, installs, runs the tests and starts Chattera. Coding agents should read [AGENTS.md](AGENTS.md).
  Local subscription CLIs (Claude Code, Codex) can be connected through adapters — see
  [docs/CLI_ADAPTER.md](docs/CLI_ADAPTER.md) and read [the compliance notes](docs/PRIVACY_AND_COMPLIANCE.md) first.

Both use the same code; the agent route is a shortcut, not a requirement.

## Documentation

- [docs/INSTALL.md](docs/INSTALL.md) — portable package, source install, data folder, portable mode, ports, OCR
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — how the pieces fit
- [docs/CLI_ADAPTER.md](docs/CLI_ADAPTER.md) — adding a subscription CLI
- [docs/PRIVACY_AND_COMPLIANCE.md](docs/PRIVACY_AND_COMPLIANCE.md) — where your data goes, terms of service, security
- [AGENTS.md](AGENTS.md) — instructions for coding agents
- [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) — third-party software and licenses
- [SECURITY.md](SECURITY.md) · [CONTRIBUTING.md](CONTRIBUTING.md) · [CHANGELOG.md](CHANGELOG.md)

## Known limitations

- Windows 10/11 x64 only for the portable package. macOS and Linux can run from source but are **untested**.
- Local only (127.0.0.1). There is no user authentication; do not expose it to a network.
- No account sync, no mobile app. Chats cannot be deleted yet (archive instead); history is append-only by design.
- Audio, video and image generation are out of scope.
- Subscription-CLI connections exist only for Claude Code and Codex, are off by default, and whether you may use them
  with a consumer subscription depends on each vendor's terms.
- The interface is Chinese-first.

## 中文简介

Chattera 是一个在你自己电脑上运行的「你和多个 AI 的群聊」：把几个 AI 模型请进同一个对话，可以一起问、单独点名、
让它们互相回应、比较各自独立的回答，也能多栏对照阅读、在自由画布上整理讨论，并为每个聊天保留一份资料库。

**普通用户**：从 [Releases](https://github.com/real-Keymio/Chattera/releases) 下载 Windows 便携版，解压后双击
`Chattera.exe`。先体验演示模式（不需要账号，不花钱），再在“设置 → 模型与服务”里添加自己的 API Key。
压缩包里有两份图文指南：`01_先看这个.pdf` 和 `02_Chattera完整使用指南.pdf`。

**费用**：Chattera 免费、开源。使用真实模型的费用由你选择的模型服务商按用量收取。

**数据**：聊天、设置、Key 和资料库只保存在本机（默认 `%LOCALAPPDATA%\Chattera`），只在 127.0.0.1 上运行。

## License

MIT — see [LICENSE](LICENSE). Chattera is built on [agentchattr](https://github.com/bcurts/agentchattr) by Ben Curtis (MIT).
