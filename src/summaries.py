"""Per-channel summary store — agents write, everyone reads."""

import json
import time
import threading
import uuid
from pathlib import Path

MAX_CHARS = 1000
PUBLIC_SECTIONS = ("question", "positions", "verified", "unverified", "disagreements", "changes")


def public_chat_message(message, channel):
    """A shared gate for summary evidence and continuation history."""
    return bool(
        message and message.get("channel", "general") == channel
        and message.get("type", "chat") == "chat"
        and message.get("visibility", "public") == "public"
        and (message.get("metadata") or {}).get("visibility", "public") == "public"
    )


def public_summary(entry, channel, get_message):
    if not isinstance(entry, dict):
        return None
    ids, sections, author = entry.get("source_ids"), entry.get("sections"), entry.get("author")
    if not isinstance(ids, list) or not 1 <= len(ids) <= 5000 or not all(
        type(i) is int and public_chat_message(get_message(i), channel) for i in ids
    ):
        return None
    if not isinstance(sections, dict) or not sections or not isinstance(author, str) or any(
        key not in PUBLIC_SECTIONS or not isinstance(value, str) or len(value) > 6000
        for key, value in sections.items()
    ):
        return None
    return {"sections": sections, "source_ids": ids, "author": author}


class SummaryStore:
    def __init__(self, path: str):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._summaries: dict[str, dict] = {}  # channel → summary dict
        self._lock = threading.RLock()
        self._load()
        self._versions_path = self._path.with_name("summary_versions.jsonl")
        self._versions = []
        if self._versions_path.exists():
            for line in self._versions_path.read_text("utf-8").splitlines():
                self._versions.append(json.loads(line))

    def versions(self, channel):
        with self._lock:
            return [dict(v) for v in self._versions if v["channel"] == channel]

    def confirmed(self, channel):
        versions = self.versions(channel)
        return next((v for v in reversed(versions) if v["status"] == "confirmed"), None)

    def active(self, channel):
        return next((v for v in reversed(self.versions(channel)) if v['status'] in ('confirmed','automatic')),None)

    def epoch(self, channel):
        return max((v['version'] for v in self.versions(channel)),default=0)

    def automatic(self, channel, sections, ids, lookup, expected, fingerprints):
        import re
        from rolling_memory import fingerprint
        if set(sections)!=set(PUBLIC_SECTIONS):raise ValueError('All memory sections required')
        if len(json.dumps(sections,ensure_ascii=False).encode())>6000:raise ValueError('Automatic summary exceeds 6000-byte memory layer')
        for value in sections.values():
            if not isinstance(value,str):raise ValueError('Memory sections must be text')
            citations={int(i) for i in re.findall(r'#(\d+)',value)}
            if not citations.issubset(ids):raise ValueError('Unknown summary citation')
            if value.strip() and not citations:raise ValueError('Nonempty claims require source IDs')
        with self._lock:
            if self.epoch(channel)!=expected:raise ValueError('Summary changed during generation')
            if any(fingerprint(lookup(i))!=fingerprints[str(i)] for i in ids):raise ValueError('Source changed')
            return self.revision(channel,sections,ids,lookup,author='automatic',automatic=True,fingerprints=fingerprints)

    def revision(self, channel, sections, source_ids, get_message, *, confirm=False, author="user", automatic=False, fingerprints=None):
        """Human draft/edit/confirmation. The legacy latest-summary projection is retained."""
        candidate = dict(sections=sections, source_ids=source_ids, author=author)
        if not public_summary(candidate, channel, get_message):
            raise ValueError("Summary evidence must belong to this room")
        text = "\n".join(f"{k}: {v}" for k, v in sections.items())
        if not text.strip() or len(text) > 6000:
            raise ValueError("Summary exceeds 6000 characters or is empty")
        from chat_rooms import append_event
        with self._lock:
            version = 1 + max((v["version"] for v in self._versions if v["channel"] == channel), default=0)
            entry = dict(candidate, channel=channel, version=version,
                         coverage_start=min(source_ids), coverage_end=max(source_ids),
                         status="automatic" if automatic else "confirmed" if confirm else "draft", updated_at=time.time(),
                         human_confirmed=confirm, fingerprints=fingerprints or {})
            append_event(self._versions_path, entry)
            self._versions.append(entry)
        if confirm:
            self.write(channel, text, author, max(source_ids), source_ids=source_ids, sections=sections)
        return dict(entry)

    def _load(self):
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text("utf-8"))
            if isinstance(raw, dict):
                self._summaries = raw
        except (json.JSONDecodeError, KeyError):
            self._summaries = {}

    def _save(self):
        temporary = self._path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self._summaries, indent=2, ensure_ascii=False) + "\n", "utf-8")
        temporary.replace(self._path)

    def get(self, channel: str) -> dict | None:
        with self._lock:
            entry = self._summaries.get(channel)
            return dict(entry) if entry else None

    def get_all(self) -> dict:
        with self._lock:
            return {ch: dict(s) for ch, s in self._summaries.items()}

    def write(self, channel: str, text: str, author: str, message_id: int = 0,
              uid: str | None = None, updated_at: float | None = None, *,
              source_ids: list[int] | None = None, sections: dict | None = None) -> dict | None:
        text = text.strip()
        if not text:
            return None
        if len(text) > MAX_CHARS:
            return None  # Caller should inform the agent
        with self._lock:
            entry = {
                "uid": uid or str(uuid.uuid4()),
                "text": text,
                "author": author,
                "updated_at": updated_at if updated_at is not None else time.time(),
                "message_id": message_id,
            }
            if source_ids is not None:
                entry["source_ids"] = source_ids
                entry["sections"] = sections
            self._summaries[channel] = entry
            self._save()
            return dict(entry)

    def delete(self, channel: str) -> bool:
        with self._lock:
            if channel in self._summaries:
                del self._summaries[channel]
                self._save()
                return True
            return False
