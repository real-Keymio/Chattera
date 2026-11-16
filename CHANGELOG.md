# Changelog

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
