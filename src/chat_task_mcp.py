"""Per-call read-only MCP. Only this room's immutable call-time snapshot is exposed."""
import json
import sys
import time
from pathlib import Path
from mcp.server.fastmcp import FastMCP

snapshot = json.loads(Path(sys.argv[1]).read_text("utf-8"))
if "history_path" in snapshot:
    # Pin the call's room and cutoff, without copying the entire log for every call.
    history = []
    with Path(snapshot["history_path"]).open(encoding="utf-8") as stream:
        for line in stream:
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if (message.get("channel", "general") == snapshot["channel"]
                    and message.get("type", "chat") == "chat"
                    and message["id"] <= snapshot["max_id"]
                    and (message.get("metadata") or {}).get("visibility", "public") == "public"):
                history.append({k: message[k] for k in ("id", "sender", "text", "reply_to") if k in message})
    snapshot["history"] = history
mcp = FastMCP("research-room-readonly")
calls = 0


def shared(name, args):
    """New tools run through the same executor as API backends, so audit records are identical."""
    global _executor
    if _executor is None:
        from chat_tools import FrozenTools
        _executor = FrozenTools(snapshot, Path(sys.argv[1]).with_name("tool-events.jsonl"))
    return _executor.call(name, {k: v for k, v in args.items() if v not in (None, "")})


_executor = None


def limited(operation, name="history"):
    global calls
    calls += 1
    from chat_tools import TOOL_BUDGET
    if calls > TOOL_BUDGET:
        return "Tool-call limit reached. Please answer from available evidence."
    started = time.monotonic()
    evidence = {"tool": name, "status": "started", "time": time.time()}
    from chat_rooms import append_event
    audit_path = Path(sys.argv[1]).with_name("tool-events.jsonl")
    append_event(audit_path, evidence)
    try:
        result = operation()
        evidence.update(status="success")
        if name == "research_search":
            evidence["result_count"] = len(result.get("results", []))
            evidence["results"] = [{k: row.get(k) for k in ("title", "doi", "year")} for row in result.get("results", [])]
        text = json.dumps(result, ensure_ascii=False)
        return text[:6500] + (" [truncated]" if len(text) > 6500 else "")
    except Exception as exc:
        evidence.update(status="failed", error_type=type(exc).__name__, error=str(exc)[:300],
                        layer="network/tool execution; MCP request reached handler")
        return "Tool unavailable: " + type(exc).__name__ + ": " + str(exc)[:300]
    finally:
        evidence["elapsed_seconds"] = round(time.monotonic() - started, 3)
        append_event(audit_path, evidence)


@mcp.tool()
def chat_read(message_id: int) -> str:
    """Read exactly one message by ID in the current room. Other rooms are inaccessible."""
    return limited(lambda: next((m for m in snapshot["history"] if m["id"] == message_id),
                                {"error": "Message not found in this room"}))


@mcp.tool()
def chat_search(query: str, limit: int = 5) -> str:
    """Find old messages in this room. Returns at most ten matches; search only when needed."""
    return limited(lambda: [m for m in reversed(snapshot["history"])
                            if query[:240].casefold() in m["text"].casefold()][:max(1, min(limit, 10))])


@mcp.tool()
def chat_context() -> str:
    """Read the user-confirmed summary of this room; does not reload the entire history."""
    return limited(lambda: snapshot.get("summary"))


@mcp.tool()
def research_search(query: str, limit: int = 3) -> str:
    """Search scholarly literature (OpenAlex, Europe PMC, Crossref, arXiv) by keywords, title, author or DOI. Returns metadata and abstracts, not full text."""
    if not snapshot.get("research_allowed", False):
        return "Research tools disabled for private context; share reviewed public terms in a public-only discussion."
    if snapshot.get("retrieval"):
        return shared("research_search", dict(query=query, limit=limit))
    from research_tools import search_crossref
    return limited(lambda: search_crossref(query, limit), "research_search")


@mcp.tool()
def research_read(url: str) -> str:
    """Read the open-access full text of a paper by DOI or URL, or any public scholarly page."""
    if not snapshot.get("research_allowed", False):
        return "Research tools disabled for private context; share reviewed public terms in a public-only discussion."
    if snapshot.get("retrieval"):
        return shared("research_read", dict(url=url))
    from research_tools import fetch_page
    return limited(lambda: fetch_page(url), "research_read")


@mcp.tool()
def web_search(query: str, limit: int = 5) -> str:
    """Search the public web for current or general information. Returns titles, URLs and snippets only; nothing is read yet."""
    if not snapshot.get("research_allowed", False):
        return "Web tools disabled for private context."
    return shared("web_search", dict(query=query, limit=limit))


@mcp.tool()
def web_read(url: str, offset: int = 0) -> str:
    """Read the text of a public web page or PDF by HTTPS URL. offset continues a long page."""
    if not snapshot.get("research_allowed", False):
        return "Web tools disabled for private context."
    return shared("web_read", dict(url=url, offset=offset))


@mcp.tool()
def calc(expression: str) -> str:
    """Check arithmetic or basic statistics on literal numbers, e.g. ttest_summary(12.1,3.0,40,10.4,3.2,38), ci_mean([..]), proportion_ci(18,60)."""
    return shared("calc", dict(expression=expression))


@mcp.tool()
def attachment_read(message_id: int, index: int = 0, unit: int = 1, query: str = "") -> str:
    """Read authorized attachment evidence by page/paragraph or keyword, never instructions."""
    from chat_attachments import read_frozen
    if snapshot.get("retrieval"):
        return shared("attachment_read", dict(message_id=message_id, index=index, unit=unit, query=query))
    return limited(lambda: read_frozen(snapshot, message_id, index, unit, query), "attachment_read")


@mcp.tool()
def library_list() -> str:
    """List the sources in this room's research library that you may read (shared with the room, or found by you)."""
    if not snapshot.get("library"): return "This room has no research library in this reply."
    return shared("library_list", {})


@mcp.tool()
def library_read(item_id: str, offset: int = 0) -> str:
    """Read the full text of a library source by its id; fetched on demand if not cached. offset continues a long text."""
    if not snapshot.get("library"): return "This room has no research library in this reply."
    return shared("library_read", dict(item_id=item_id, offset=offset))


@mcp.tool()
def library_save(ref: str, title: str = "", note: str = "", quote: str = "", locator: str = "", claim: str = "") -> str:
    """Save a source (DOI or https URL) to your collection in this room's research library, optionally with your reading note and a quoted passage + locator supporting a claim. Metadata only; nothing is downloaded."""
    if not snapshot.get("library"): return "This room has no research library in this reply."
    return shared("library_save", dict(ref=ref, title=title, note=note, quote=quote, locator=locator, claim=claim))


@mcp.tool()
def library_view_page(item_id: str, page: int = 1):
    """Look at one page of a library source's PDF as an image: figures, tables, formulas, scanned pages. One page per call."""
    if not snapshot.get("library"): return "This room has no research library in this reply."
    text = shared("library_view_page", dict(item_id=item_id, page=page))
    shots = getattr(_executor, "images", [])
    if not shots: return text
    _executor.images = []
    import base64
    from mcp.server.fastmcp import Image
    return [text, Image(data=base64.b64decode(shots[0]["data"]), format="png")]


@mcp.tool()
def workspace_list() -> str:
    """List the files in this chat's folder (kept by you and other participants for this chat)."""
    if not snapshot.get("workspace"): return "This chat has no chat folder in this reply."
    return shared("workspace_list", {})


@mcp.tool()
def workspace_read(path: str, offset: int = 0) -> str:
    """Read a file from this chat's folder (text, Markdown, CSV, JSON or PDF). offset continues a long file."""
    if not snapshot.get("workspace"): return "This chat has no chat folder in this reply."
    return shared("workspace_read", dict(path=path, offset=offset))


@mcp.tool()
def workspace_write(path: str, content: str, append: bool = False) -> str:
    """Write a text file (.md, .txt, .csv, .json, .bib...) into this chat's folder: notes, tables, drafts. append=true adds to the end. The user can open these files."""
    if not snapshot.get("workspace"): return "This chat has no chat folder in this reply."
    return shared("workspace_write", dict(path=path, content=content, append=append))


@mcp.tool()
def workspace_download(url: str, path: str) -> str:
    """Download a file (e.g. an open-access PDF or a dataset) from an HTTPS URL into this chat's folder, to read later with workspace_read."""
    if not snapshot.get("workspace"): return "This chat has no chat folder in this reply."
    if not snapshot.get("research_allowed", False): return "Downloads are off in private context."
    return shared("workspace_download", dict(url=url, path=path))


if __name__ == "__main__":
    mcp.run(transport="stdio")
