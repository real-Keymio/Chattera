"""Participant status for the interface.

In the original project this class woke up AI agents running in separate terminal windows. Chattera has no such
agents: every reply is started by the chat runtime (chat_tasks.py). What remains is the status the interface shows
and the channel check the runtime installs (`owner_guard`).
"""

import logging

log = logging.getLogger(__name__)


class AgentTrigger:
    def __init__(self, registry, data_dir: str = "./data"):
        self._registry = registry

    def is_available(self, name: str) -> bool:
        return self._registry.is_registered(name)

    def accepts_channel(self, name: str, channel: str) -> bool:
        if hasattr(self, "owner_guard") and not self.owner_guard(name, channel):
            return False
        instance = self._registry.get_instance(name)
        scope = instance.get("channel") if instance else None
        return not scope or scope == channel

    def get_status(self) -> dict:
        """Registered participants; the chat runtime replaces this with live connection status."""
        return {
            name: {"available": False, "busy": False, "label": info["label"], "color": info["color"], "role": "",
                   "channel": info.get("channel", "")}
            for name, info in self._registry.get_all().items()
        }

    async def trigger(self, agent_name: str, message: str = "", channel: str = "general", **kwargs):
        """No terminal agents exist in Chattera; kept so older call sites stay harmless."""
        log.debug("ignored terminal-agent trigger for %s", agent_name)

    def trigger_sync(self, agent_name: str, message: str = "", channel: str = "general", **kwargs):
        log.debug("ignored terminal-agent trigger for %s", agent_name)
