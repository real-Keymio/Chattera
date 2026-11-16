"""Registry of subscription-CLI adapters. Adding a CLI = one module here + one `register()` line; no core changes.

The chat core (permissions, visibility, frozen snapshots, append-only history, summaries, library) never imports an
adapter directly; it goes through `get(name)` and the `CLIAdapter` contract in base.py.
"""
from .base import CLIAdapter, clean_environment
from .claude_code import ClaudeCode
from .codex import Codex

ADAPTERS = {}

# Room tools the tool server offers to CLI replies (read-only: chat history of this room, research, library, calculator).
ROOM_TOOLS = ("chat_read", "chat_search", "chat_context", "research_search", "research_read", "attachment_read", "web_search",
              "web_read", "calc", "library_list", "library_read", "library_save", "library_view_page")


def register(adapter):
    if not adapter.name or adapter.name in ADAPTERS: raise ValueError('adapter needs a new, unique name')
    adapter.room_tools = ROOM_TOOLS
    ADAPTERS[adapter.name] = adapter
    return adapter


def get(name):
    return ADAPTERS.get(name)


def names():
    return list(ADAPTERS)


register(ClaudeCode())
register(Codex())

# CLIs that may be installed but have no adapter. Found ≠ adapted ≠ able to chat.
KNOWN_UNADAPTED = [
    ('gemini', 'Gemini CLI', ('gemini',), 'Gemini CLI 有非交互模式，但 Chattera 还没有它的适配器；Gemini 目前可以通过 API 连接使用。'),
    ('antigravity', 'Antigravity', ('agy', 'antigravity'), 'Antigravity 没有公开的、适合聊天后端使用的非交互接口。'),
    ('qwen', 'Qwen Code', ('qwen',), ''),
    ('opencode', 'OpenCode', ('opencode',), ''),
]
