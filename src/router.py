"""Message routing based on @mentions with per-channel loop guard."""

import re


class Router:
    def __init__(self, agent_names: list[str], default_mention: str = "both",
                 max_hops: int = 4, online_checker=None, strict_mentions: bool = False):
        self.agent_names = set(n.lower() for n in agent_names)
        self.default_mention = default_mention
        self.max_hops = max_hops
        self.strict_mentions = strict_mentions
        self._online_checker = online_checker  # callable() -> set of online agent names
        # Per-channel state: { channel: { hop_count, paused, guard_emitted } }
        self._channels: dict[str, dict] = {}
        self._build_pattern()

    def _get_ch(self, channel: str) -> dict:
        if channel not in self._channels:
            self._channels[channel] = {
                "hop_count": 0,
                "paused": False,
                "guard_emitted": False,
            }
        return self._channels[channel]

    def _build_pattern(self):
        # Sort longest-first so "gemini-2" is tried before "gemini"
        names = [re.escape(n) for n in sorted(self.agent_names, key=len, reverse=True)]
        alternatives = "|".join(names + ["both", "all"])
        self._mention_re = re.compile(
            rf"@({alternatives})(?![\w-])", re.IGNORECASE
        )

    def parse_mentions(self, text: str) -> list[str]:
        mentions = set()
        for match in self._mention_re.finditer(text):
            name = match.group(1).lower()
            if name in ("both", "all"):
                # Only tag online agents when using @all
                if self._online_checker:
                    online = self._online_checker()
                    mentions.update(n for n in self.agent_names if n in online)
                else:
                    mentions.update(self.agent_names)
            else:
                mentions.add(name)
        return list(mentions)

    def _is_agent(self, sender: str) -> bool:
        return sender.lower() in self.agent_names

    def leading_mentions(self, text: str) -> list[str]:
        names = []
        for word in text.split():
            match = self._mention_re.fullmatch(word)
            if match is None:
                break
            names.append(match.group(1).lower())
        return names

    def get_targets(self, sender: str, text: str, channel: str = "general",
                    explicit_targets: list[str] | None = None) -> list[str]:
        """Determine which agents should receive this message."""
        ch = self._get_ch(channel)
        if explicit_targets is not None:
            valid = self.agent_names | {"both", "all"}
            mentions = self.parse_mentions(" ".join(
                f"@{name.lower()}" for name in explicit_targets if name.lower() in valid
            ))
        elif self.strict_mentions:
            # People retain the conventional leading address. Agent handoffs
            # use explicit targets, or a final line containing only addresses.
            if self._is_agent(sender):
                lines = text.strip().splitlines()
                last = lines[-1].split() if len(lines) > 1 else []
                names = self.leading_mentions(" ".join(last))
                if len(names) != len(last):
                    names = []
            else:
                names = self.leading_mentions(text)
            mentions = self.parse_mentions(" ".join(f"@{name}" for name in names))
        else:
            mentions = self.parse_mentions(text)

        if not self._is_agent(sender):
            # Human message resets hop counter and unpauses
            ch["hop_count"] = 0
            ch["paused"] = False
            ch["guard_emitted"] = False
            if not mentions:
                if explicit_targets is not None:
                    return []
                if self.default_mention in ("both", "all"):
                    return list(self.agent_names)
                elif self.default_mention == "none":
                    return []
                return [self.default_mention]
            return mentions
        else:
            # Agent message: blocked while loop guard is active
            if ch["paused"] or ch.get("human_paused", False):
                return []
            # Only route if explicit @mention
            if not mentions:
                return []
            ch["hop_count"] += 1
            if ch["hop_count"] > self.max_hops:
                ch["paused"] = True
                return []
            # Don't route back to self
            return [m for m in mentions if m != sender]

    def pause_routing(self, channel: str = "general"):
        self._get_ch(channel)["human_paused"] = True

    def continue_routing(self, channel: str = "general"):
        """Resume after loop guard pause."""
        ch = self._get_ch(channel)
        ch["human_paused"] = False
        ch["hop_count"] = 0
        ch["paused"] = False
        ch["guard_emitted"] = False

    def is_paused(self, channel: str = "general") -> bool:
        ch = self._get_ch(channel)
        return ch["paused"]

    def is_handoff_stopped(self, channel: str = "general") -> bool:
        return self._get_ch(channel).get("human_paused", False)

    def is_guard_emitted(self, channel: str = "general") -> bool:
        return self._get_ch(channel)["guard_emitted"]

    def set_guard_emitted(self, channel: str = "general"):
        self._get_ch(channel)["guard_emitted"] = True

    def update_agents(self, names: list[str]):
        """Replace the agent name set and rebuild the mention regex."""
        self.agent_names = set(n.lower() for n in names)
        self._build_pattern()
