"""Participant ids. The four built-in ones are fixed; others are added from the interface.

IDENTITIES is a live list of every id that is or has been a participant (retired ones stay, so old
messages, audiences and snapshots keep resolving). The registry owns its contents.
"""
BUILTIN = ("claude", "codex", "antigravity", "deepseek")
LEGACY_CAPABLE = BUILTIN[:3]
IDENTITIES = list(BUILTIN)
