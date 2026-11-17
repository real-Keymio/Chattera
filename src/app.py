"""Chattera server core (derived from agentchattr): message store, WebSocket, uploads, export."""

import asyncio
import os
import json
import re as _re
import uuid
import logging
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Form
from fastapi.requests import Request
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware

from store import MessageStore
from rules import RuleStore
from summaries import SummaryStore
from router import Router
from agents import AgentTrigger
from registry import RuntimeRegistry

log = logging.getLogger(__name__)

app = FastAPI(title="Chattera")

# --- globals (set by configure()) ---
store: MessageStore | None = None
rules: RuleStore | None = None
summaries: SummaryStore | None = None
router: Router | None = None
agents: AgentTrigger | None = None
registry: RuntimeRegistry | None = None
config: dict = {}
ws_clients: set[WebSocket] = set()

# --- Security: session token (set by configure()) ---
session_token: str = ""
chat_runtime = None

# Room settings (persisted to data/settings.json)
room_settings: dict = {
    "title": "Chattera",
    # Shown beside the title, so several servers can be told apart without
    # renaming the product itself. "" hides it.
    "subtitle": "",
    "username": "user",
    "font": "sans",
    "channels": ["general"],
    "history_limit": "all",
    "contrast": "normal",
    "custom_roles": [],
}

# Channel validation
_CHANNEL_NAME_RE = _re.compile(r'^[a-z0-9][a-z0-9\-]{0,19}$')

# Agent hats (persisted to data/hats.json)
agent_hats: dict[str, str] = {}  # { agent_name: svg_string }














def _settings_path() -> Path:
    data_dir = config.get("server", {}).get("data_dir", "./data")
    return Path(data_dir) / "settings.json"


ROOM_SUBTITLE_MAX = 40


def normalize_room_subtitle(value):
    """Clean a room subtitle, or return None if it is not usable.

    Collapses any internal whitespace so a pasted newline cannot break the
    header across two lines, and caps the length so a long name cannot crowd
    out the rest of the header.
    """
    if not isinstance(value, str):
        return None
    return " ".join(value.split())[:ROOM_SUBTITLE_MAX]


def apply_room_subtitle(new: dict):
    """Apply a subtitle from a settings payload, if present and usable."""
    if "subtitle" not in new:
        return
    cleaned = normalize_room_subtitle(new["subtitle"])
    if cleaned is not None:
        room_settings["subtitle"] = cleaned


def _load_settings():
    global room_settings
    p = _settings_path()
    if p.exists():
        try:
            saved = json.loads(p.read_text("utf-8"))
            room_settings.update(saved)
        except Exception:
            pass
    # settings.json is hand-editable, so the file bypasses every check the
    # live update path applies. Re-normalise on load rather than trust it.
    room_settings["subtitle"] = normalize_room_subtitle(room_settings.get("subtitle")) or ""
    # Ensure "general" always exists and is first
    if "channels" not in room_settings or not room_settings["channels"]:
        room_settings["channels"] = ["general"]
    elif "general" not in room_settings["channels"]:
        room_settings["channels"].insert(0, "general")


def _save_settings():
    p = _settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(room_settings, indent=2), "utf-8")


# --- Security middleware ---
# Paths that don't require the session token (public assets).
_PUBLIC_PREFIXES = ("/", "/static/")


def _install_security_middleware(token: str, cfg: dict):
    """Add token validation and origin checking middleware to the app."""
    import app as _self
    _self.session_token = token
    port = cfg.get("server", {}).get("port", 8300)
    allowed_origins = {
        f"http://127.0.0.1:{port}",
        f"http://localhost:{port}",
    }

    class SecurityMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            path = request.url.path
            # Host check against DNS rebinding: only the local addresses Chattera itself serves on.
            if f"http://{request.headers.get('host', '')}" not in allowed_origins:
                return JSONResponse({"error": "invalid local host"}, status_code=403)

            # Static assets, index page, and uploaded images are public.
            # The index page injects the token client-side via same-origin script.
            # Images keep their public preview URLs. Other attachments require
            # the browser session token, like the rest of the API.
            public_image = path.startswith("/uploads/") and Path(path).suffix.lower() in IMAGE_UPLOAD_EXTS
            pages = ("/",)
            if path in pages or path.startswith("/static/") or (public_image and not _self.chat_runtime):
                response = await call_next(request)
                if path in pages:
                    response.set_cookie('room_session', token, httponly=True, samesite='strict')
                return response
            # Files opened in a new tab (uploads, chat-folder files) carry the page's session cookie instead of the header.
            files = path.startswith('/api/chat/rooms/') and path.endswith('/files/raw') and request.method == 'GET'
            if (path.startswith('/uploads/') or files) and request.cookies.get('room_session') == token:
                return await call_next(request)

            # --- Origin check (blocks cross-origin / DNS-rebinding attacks) ---
            origin = request.headers.get("origin")
            if origin and origin not in allowed_origins:
                return JSONResponse(
                    {"error": "forbidden: origin not allowed"},
                    status_code=403,
                )

            # --- Token check: every API call needs the session token of the page ---
            req_token = request.headers.get("x-session-token")
            if not req_token:
                req_token = request.query_params.get("token")
            if req_token != _self.session_token:
                return JSONResponse(
                    {"error": "forbidden: invalid or missing session token"},
                    status_code=403,
                )

            return await call_next(request)

    app.add_middleware(SecurityMiddleware)


def configure(cfg: dict, session_token: str = ""):
    global store, rules, summaries, router, agents, registry, config
    config = cfg
    # --- Security: store the session token and install middleware ---
    _install_security_middleware(session_token, cfg)

    data_dir = cfg.get("server", {}).get("data_dir", "./data")
    Path(data_dir).mkdir(parents=True, exist_ok=True)

    log_path = Path(data_dir) / "agentchattr_log.jsonl"
    legacy_log_path = Path(data_dir) / "room_log.jsonl"
    if not log_path.exists() and legacy_log_path.exists():
        # Backward compatibility for existing installs.
        log_path = legacy_log_path

    store = MessageStore(str(log_path))
    # Initialize store upload dir from config
    raw_upload_dir = cfg.get("images", {}).get("upload_dir", "./uploads")
    store.upload_dir = Path(raw_upload_dir)
    
    # Rules store — migrates from legacy decisions.json automatically
    rules_path = Path(data_dir) / "rules.json"
    legacy_decisions = Path(data_dir) / "decisions.json"
    if not rules_path.exists() and legacy_decisions.exists():
        legacy_decisions.rename(rules_path)
    rules = RuleStore(str(rules_path))

    summaries = SummaryStore(str(Path(data_dir) / "summaries.json"))

    max_hops = cfg.get("routing", {}).get("max_agent_hops", 4)

    # Registry: single source of truth for all live agent state
    registry = RuntimeRegistry(
        data_dir=data_dir, require_channel=os.environ.get("AGENTCHATTR_REQUIRE_CHANNEL") == "1",
    )
    registry.seed(cfg.get("agents", {}))
    registry.on_change(_on_registry_change)

    # Router starts with base agent names (backward compat for direct MCP users),
    # registry.on_change updates it dynamically when instances register/deregister
    agent_names = list(cfg.get("agents", {}).keys())
    router = Router(
        agent_names=agent_names,
        default_mention=cfg.get("routing", {}).get("default", "none"),
        max_hops=max_hops,
        strict_mentions=os.environ.get("AGENTCHATTR_REQUIRE_CHANNEL") == "1",
        online_checker=lambda: set(registry.get_active_names()) if registry else set(),
    )
    agents = AgentTrigger(registry, data_dir=data_dir)

    # Bridge: when ANY message is added to store (including via MCP),
    # broadcast to all WebSocket clients
    store.on_message(_on_store_message)

    _load_settings()
    global chat_runtime
    import sys
    from chat_runtime import install
    chat_runtime = install(sys.modules[__name__], data_dir)

    # Apply saved loop guard setting
    router.max_hops = min(room_settings.get("max_agent_hops", max_hops), 2)
    room_settings["max_agent_hops"] = router.max_hops

# --- Store → WebSocket bridge ---

_event_loop = None  # set by run.py after starting the event loop
_last_active_channel: str = "general"  # last channel any message was sent in
# Per-agent last channel: where each sender was most recently active. Used to
# route leave/disconnect messages to the channel that agent was talking in,
# instead of the global last-active channel (which is usually #general and made
# leave spam land in the wrong place).
_agent_last_channel: dict[str, str] = {}


def _migrate_agent_last_channel(old_name: str, new_name: str):
    """Re-key _agent_last_channel across an agent rename.

    Without this, a disconnect for the post-rename name misses the map and
    falls back to the global last-active channel — putting the leave message
    back in the wrong channel. Call this beside every migrate_identity /
    rename_sender path (slot-1 rename, renamed-back, websocket rename_agent,
    /api/label)."""
    if old_name == new_name:
        return
    ch = _agent_last_channel.pop(old_name, None)
    if ch is not None:
        _agent_last_channel[new_name] = ch


def set_event_loop(loop):
    global _event_loop
    _event_loop = loop


def _on_store_message(msg: dict):
    """Called from any thread when a message is added to the store."""
    if _event_loop is None:
        return
    try:
        # If called from the event loop thread (e.g. WebSocket handler),
        # schedule directly as a task
        loop = asyncio.get_running_loop()
        if loop is _event_loop:
            asyncio.ensure_future(_handle_new_message(msg))
            return
    except RuntimeError:
        pass  # No running loop — we're in a different thread (MCP)
    asyncio.run_coroutine_threadsafe(_handle_new_message(msg), _event_loop)










_draft_ref_re = _re.compile(r'\[([a-f0-9]{8})\]')

def _resolve_draft_lineage(text: str, channel: str) -> tuple[str, int]:
    """Check if a session draft block is a revision of an existing draft.

    Looks at the agent's own message text for a [draft_id] reference, and also
    scans recent channel messages for "revise session draft [XXXX]" requests.
    Returns (draft_id, revision). New drafts get a fresh id and revision=1.
    """
    # Check the message text itself for a draft_id reference
    ref_match = _draft_ref_re.search(text)
    ref_id = ref_match.group(1) if ref_match else None

    if not ref_id:
        # Also check recent messages for a "revise session draft [XXXX]" request
        recent = store.get_recent(count=20, channel=channel)
        for m in reversed(recent):
            m_text = m.get("text", "")
            if "revise session draft" in m_text.lower():
                ref_match = _draft_ref_re.search(m_text)
                if ref_match:
                    ref_id = ref_match.group(1)
                    break

    if ref_id:
        # Find the highest revision for this draft_id in existing messages
        max_rev = 0
        recent = store.get_recent(count=100, channel=channel)
        for m in recent:
            meta = m.get("metadata") or {}
            if meta.get("draft_id") == ref_id:
                max_rev = max(max_rev, meta.get("revision", 1))
        if max_rev > 0:
            return ref_id, max_rev + 1

    return str(uuid.uuid4())[:8], 1


async def _handle_new_message(msg: dict):
    """Broadcast a new message to the web clients, then hand it to the chat runtime, which starts the replies.
    (The original project's slash commands, session drafts and terminal-agent triggers are not part of Chattera.)"""
    global _last_active_channel
    if msg.get("type", "chat") not in ("system", "leave", "join"):
        _last_active_channel = msg.get("channel", "general")
    await broadcast(msg)
    if chat_runtime:
        await chat_runtime.route(msg)


# --- broadcasting ---

async def _broadcast(raw_json: str):
    """Send a pre-serialized JSON string to all WebSocket clients."""
    dead = set()
    for client in list(ws_clients):
        try:
            await client.send_text(raw_json)
        except Exception:
            dead.add(client)
    ws_clients.difference_update(dead)


async def broadcast(msg: dict):
    data = json.dumps({"type": "message", "data": msg})
    dead = set()
    for client in list(ws_clients):
        try:
            await client.send_text(data)
        except Exception:
            dead.add(client)
    ws_clients.difference_update(dead)


async def broadcast_status():
    status = agents.get_status()
    status["paused"] = any(router.is_paused(ch) or router.is_handoff_stopped(ch) for ch in room_settings.get("channels", ["general"]))
    data = json.dumps({"type": "status", "data": status})
    dead = set()
    for client in list(ws_clients):
        try:
            await client.send_text(data)
        except Exception:
            dead.add(client)
    ws_clients.difference_update(dead)


async def broadcast_typing(agent_name: str, is_typing: bool):
    data = json.dumps({"type": "typing", "agent": agent_name, "active": is_typing})
    dead = set()
    for client in list(ws_clients):
        try:
            await client.send_text(data)
        except Exception:
            dead.add(client)
    ws_clients.difference_update(dead)


async def broadcast_clear(channel: str | None = None):
    payload = {"type": "clear"}
    if channel:
        payload["channel"] = channel
    data = json.dumps(payload)
    dead = set()
    for client in list(ws_clients):
        try:
            await client.send_text(data)
        except Exception:
            dead.add(client)
    ws_clients.difference_update(dead)


async def broadcast_todo_update(msg_id: int, status: str | None):
    data = json.dumps({"type": "todo_update", "data": {"id": msg_id, "status": status}})
    dead = set()
    for client in list(ws_clients):
        try:
            await client.send_text(data)
        except Exception:
            dead.add(client)
    ws_clients.difference_update(dead)


async def broadcast_settings():
    data = json.dumps({"type": "settings", "data": room_settings})
    dead = set()
    for client in list(ws_clients):
        try:
            await client.send_text(data)
        except Exception:
            dead.add(client)
    ws_clients.difference_update(dead)












async def broadcast_agents():
    """Send updated agent config (from registry) to all WebSocket clients."""
    agent_cfg = registry.get_agent_config() if registry else {}
    if chat_runtime:
        agent_cfg = chat_runtime.agent_config(agent_cfg)
    data = json.dumps({"type": "agents", "data": agent_cfg})
    dead = set()
    for client in list(ws_clients):
        try:
            await client.send_text(data)
        except Exception:
            dead.add(client)
    ws_clients.difference_update(dead)


def _on_registry_change():
    """Called from registry (any thread) when instances register/deregister/claim/rename."""
    # Update router with current agent names (base names + registered instances)
    if router and registry:
        base_names = list(registry.get_bases().keys())
        # Only include active instances in routing (pending ones are inert)
        instance_names = registry.get_active_names()
        all_names = list(set(base_names + instance_names))
        router.update_agents(all_names)
    # Broadcast to WebSocket clients
    if _event_loop:
        asyncio.run_coroutine_threadsafe(broadcast_agents(), _event_loop)
        asyncio.run_coroutine_threadsafe(broadcast_status(), _event_loop)


# --- WebSocket ---

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    # --- Security: validate session token on WebSocket connect ---
    token = websocket.query_params.get("token", "")
    origin = websocket.headers.get("origin")
    port = config.get("server", {}).get("port", 8300)
    if token != session_token or (origin and origin not in (
        f"http://127.0.0.1:{port}", f"http://localhost:{port}"
    )):
        # Must accept before closing so the browser receives the close frame.
        # Code 4003 triggers an auto-reload in the client to pick up the new token.
        await websocket.accept()
        await websocket.close(code=4003, reason="forbidden: invalid session token")
        return

    await websocket.accept()
    ws_clients.add(websocket)

    # Send settings
    await websocket.send_text(json.dumps({"type": "settings", "data": room_settings}))

    # Send registered instances (used for pills/mentions)
    agent_cfg = registry.get_agent_config() if registry else {}
    if chat_runtime:
        agent_cfg = chat_runtime.agent_config(agent_cfg)
    await websocket.send_text(json.dumps({"type": "agents", "data": agent_cfg}))

    # Send base agent colors (used for message coloring, no pills)
    base_colors = {}
    for name, cfg in config.get("agents", {}).items():
        base_colors[name] = {"color": cfg.get("color", "#888"), "label": cfg.get("label", name)}
    await websocket.send_text(json.dumps({"type": "base_colors", "data": base_colors}))

    # Send history (per channel based on history_limit)
    limit_val = room_settings.get("history_limit", "all")
    count = 10000 if limit_val == "all" else int(limit_val)
    
    history = []
    for ch in room_settings["channels"]:
        history.extend(store.get_recent(count, channel=ch))
    
    # Sort history by timestamp to interleave messages from different channels correctly
    history.sort(key=lambda m: m.get("timestamp", 0))
    
    # Saved messages have their own boundary; presence updates can arrive at
    # any time and must never make old history look like new chat.
    for offset in range(0, len(history), 100):
        await websocket.send_text(json.dumps({
            "type": "history", "messages": history[offset:offset + 100],
        }))
        await asyncio.sleep(0)
    await websocket.send_text(json.dumps({"type": "history_complete"}))

    # Send status
    await broadcast_status()

    try:
        while True:
            raw = await websocket.receive_text()
            event = json.loads(raw)
            if chat_runtime:
                try:
                    if await chat_runtime.websocket(event):
                        continue
                except ValueError as exc:
                    await websocket.send_json({"type": "error", "text": str(exc)})
                    continue

            if event.get("type") == "message":
                text = event.get("text", "").strip()
                attachments = event.get("attachments", [])
                sender = event.get("sender") or room_settings.get("username", "user")
                channel = event.get("channel", "general")

                if os.environ.get("AGENTCHATTR_REQUIRE_CHANNEL") == "1" and (
                    channel not in room_settings.get("channels", ["general"])
                    or not isinstance(sender, str)
                    or sender.lower() == "system"
                    or (registry and registry.is_agent_family(sender.lower()))
                ):
                    continue
                if not text and not attachments:
                    continue

                # Command handling
                if text.startswith("/"):
                    cmd_parts = text.split()
                    cmd = cmd_parts[0].lower()
                    if cmd == "/clear":
                        if chat_runtime:
                            await websocket.send_json({"type": "error", "text": "History is preserved; archive this room instead."})
                            continue
                        store.clear(channel=channel)
                        await broadcast_clear(channel=channel)
                        continue
                    if cmd == "/stop":
                        if chat_runtime:
                            chat_runtime.tasks.cancel(channel)
                        router.pause_routing(channel)
                        store.add("system", "Automatic handoffs paused. Direct mentions still work.", msg_type="system", channel=channel)
                        await broadcast_status()
                        continue
                    if cmd == "/continue":
                        router.continue_routing(channel)
                        store.add("system", "Resuming agent conversation...", msg_type="system", channel=channel)
                        await broadcast_status()
                        continue
                    # Broadcast slash commands — expand without storing the raw command.
                    # _handle_new_message will store the expanded version.
                    if cmd in ("/hatmaking", "/artchallenge", "/roastreview", "/poetry"):
                        await _handle_new_message({"sender": sender, "text": text, "channel": channel})
                        continue

                # Store message — the on_message callback handles broadcast + triggers
                reply_to = event.get("reply_to")
                if reply_to is not None:
                    if type(reply_to) is not int:
                        continue
                    parent = store.get_by_id(reply_to)
                    if not parent or parent.get("channel", "general") != channel:
                        continue
                metadata = {k: event[k] for k in ('visibility','independent_mode','independent_group','model_overrides','evidence','lens') if k in event}
                if "targets" in event:
                    targets = event["targets"]
                    if not isinstance(targets, list) or len(targets) > 60 or any(
                        not isinstance(target, str) or not _re.fullmatch(r"[\w-]{1,64}", target)
                        for target in targets
                    ):
                        continue
                    metadata["targets"] = targets
                if "action_instruction" in event:
                    instruction = event["action_instruction"]
                    if not isinstance(instruction, str) or not instruction.strip() or len(instruction) > 4000:
                        continue
                    if reply_to is None or not metadata.get("targets"):
                        continue
                    metadata["action_instruction"] = instruction.strip()
                if "quote_text" in event:
                    # The human selects rendered text, which can differ from Markdown source.
                    # Treat it as a user-supplied excerpt tied to a validated same-channel reply.
                    quote = event["quote_text"]
                    if reply_to is None or not isinstance(quote, str) or not quote.strip() or len(quote) > 8000:
                        continue
                    metadata["quote_text"] = quote
                store.add(sender, text, attachments=attachments, reply_to=reply_to,
                          channel=channel, metadata=metadata)

            elif event.get("type") == "delete":
                ids = event.get("ids", [])
                if ids:
                    deleted = store.delete([int(i) for i in ids])
                    if deleted:
                        data = json.dumps({"type": "delete", "ids": deleted})
                        dead = set()
                        for client in list(ws_clients):
                            try:
                                await client.send_text(data)
                            except Exception:
                                dead.add(client)
                        ws_clients.difference_update(dead)
                continue

            elif event.get("type") == "update_settings":
                new = event.get("data", {})
                if "title" in new and isinstance(new["title"], str):
                    room_settings["title"] = new["title"].strip() or "agentchattr"
                apply_room_subtitle(new)
                if "username" in new and isinstance(new["username"], str):
                    room_settings["username"] = new["username"].strip() or "user"
                if "font" in new and new["font"] in ("mono", "serif", "sans"):
                    room_settings["font"] = new["font"]
                if "max_agent_hops" in new:
                    try:
                        hops = int(new["max_agent_hops"])
                        hops = max(1, min(hops, 2))
                        room_settings["max_agent_hops"] = hops
                        router.max_hops = hops
                    except (ValueError, TypeError):
                        pass
                if "contrast" in new and new["contrast"] in ("normal", "high"):
                    room_settings["contrast"] = new["contrast"]
                if "rules_refresh_interval" in new:
                    try:
                        ri = int(new["rules_refresh_interval"])
                        room_settings["rules_refresh_interval"] = max(0, min(ri, 100))
                    except (ValueError, TypeError):
                        pass
                if "history_limit" in new:
                    val = str(new["history_limit"]).strip().lower()
                    if val == "all":
                        room_settings["history_limit"] = "all"
                    else:
                        try:
                            val_int = int(val)
                            room_settings["history_limit"] = max(1, min(val_int, 10000))
                        except (ValueError, TypeError):
                            pass
                if "custom_roles" in new and isinstance(new["custom_roles"], list):
                    room_settings["custom_roles"] = [
                        str(r).strip()[:20] for r in new["custom_roles"]
                        if isinstance(r, str) and r.strip()
                    ][:20]
                _save_settings()
                await broadcast_settings()

            elif event.get("type") == "channel_create":
                name = (event.get("name") or "").strip().lower()
                if not name or not _CHANNEL_NAME_RE.match(name):
                    continue
                if name in room_settings["channels"]:
                    continue
                room_settings["channels"].append(name)
                _save_settings()
                await broadcast_settings()

            elif event.get("type") == "channel_rename":
                old_name = (event.get("old_name") or "").strip().lower()
                new_name = (event.get("new_name") or "").strip().lower()
                if old_name == "general":
                    continue
                if not new_name or not _CHANNEL_NAME_RE.match(new_name):
                    continue
                if old_name not in room_settings["channels"]:
                    continue
                if new_name in room_settings["channels"]:
                    continue
                idx = room_settings["channels"].index(old_name)
                room_settings["channels"][idx] = new_name
                store.rename_channel(old_name, new_name)
                _save_settings()
                await broadcast_settings()
                # Tell clients to migrate DOM elements
                rename_event = json.dumps({
                    "type": "channel_renamed",
                    "old_name": old_name,
                    "new_name": new_name,
                })
                for c in list(ws_clients):
                    try:
                        await c.send_text(rename_event)
                    except Exception:
                        pass

            elif event.get("type") == "channel_delete":
                name = (event.get("name") or "").strip().lower()
                if name == "general":
                    continue
                if name not in room_settings["channels"]:
                    continue
                room_settings["channels"].remove(name)
                store.delete_channel(name)
                _save_settings()
                await broadcast_settings()

    except WebSocketDisconnect:
        ws_clients.discard(websocket)
    except Exception:
        ws_clients.discard(websocket)
        log.exception("WebSocket error")


# --- REST endpoints ---

IMAGE_UPLOAD_EXTS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.svg'}
ALLOWED_UPLOAD_EXTS = IMAGE_UPLOAD_EXTS  # backwards-compatible image policy
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB default


@app.post("/api/upload")
async def upload_image(file: UploadFile = File(...), display_name: str | None = Form(None)):
    upload_dir = Path(config.get("images", {}).get("upload_dir", "./uploads"))
    upload_dir.mkdir(parents=True, exist_ok=True)

    # Uploaded names are display labels only; never use them as disk paths.
    name = (display_name or file.filename or "file").replace("\\", "/").rsplit("/", 1)[-1]
    ext = Path(name).suffix.lower()
    file_cfg = config.get("attachments", {})
    allowed_files = {str(value).lower() for value in file_cfg.get("allowed_extensions", ["*"])}
    is_image = ext in IMAGE_UPLOAD_EXTS
    if chat_runtime:
        from chat_attachments import EXTENSIONS
        if ext not in EXTENSIONS:
            return JSONResponse({'error':'Unsupported file type. Supported: PNG, JPEG, WebP images; PDF, DOCX, TXT, Markdown, RTF, HTML, CSV, JSON, XML and LaTeX documents. Legacy .doc is not supported: save it as DOCX or PDF.'}, status_code=400)
    if not is_image and "*" not in allowed_files and ext not in allowed_files:
        return JSONResponse({"error": f"unsupported file type: {ext or '(no extension)'}"}, status_code=400)

    policy = config.get("images", {}) if is_image else file_cfg
    max_mb = policy.get("max_size_mb", 10 if is_image else 50)
    max_bytes = min(int(max_mb * 1024 * 1024), 50 * 1024 * 1024) if chat_runtime else int(max_mb * 1024 * 1024)
    # Keep storage URLs simple even for names with unusual suffixes (e.g. .c++).
    storage_ext = ext if not ext or _re.fullmatch(r"\.[a-z0-9_-]{1,32}", ext) else ".bin"
    filename = f"{uuid.uuid4().hex}{storage_ext}"
    filepath = upload_dir / filename
    size = 0
    try:
        with filepath.open("xb") as destination:
            while chunk := await file.read(64 * 1024):
                size += len(chunk)
                if size > max_bytes:
                    raise ValueError(f"file too large (max {max_mb} MB)")
                destination.write(chunk)
    except ValueError as exc:
        filepath.unlink(missing_ok=True)
        return JSONResponse({"error": str(exc)}, status_code=413)
    except BaseException:
        filepath.unlink(missing_ok=True)
        raise

    import mimetypes

    return JSONResponse({
        "name": name,
        "url": f"/uploads/{filename}",
        "size": size,
        "media_type": mimetypes.guess_type(name)[0] or "application/octet-stream",
        "kind": "image" if is_image else "file",
    })


# --- Export / Import ---

@app.get("/api/export")
async def export_history():
    """Download a zip archive of project history."""
    import archive as _archive
    import time as _time
    try:
        zip_bytes = _archive.build_export(
            store, None, rules, summaries,
            app_version=config.get("server", {}).get("version", ""),
        )
    except Exception as exc:
        return JSONResponse({"error": f"export failed: {exc}"}, status_code=500)
    filename = f"chattera-export-{_time.strftime('%Y%m%d-%H%M%S')}.zip"
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


















# --- Jobs API ---









































# --- Rules API ---



















# --- Open agent session in terminal ---





# Serve uploaded images
# --- Sessions API ---



















# --- Version check (GitHub release notifier) ---

_version_cache: dict = {"data": None, "fetched_at": 0.0}
_VERSION_CACHE_TTL = 1800  # 30 minutes


def _read_local_version() -> str:
    """Read version from the VERSION file (repository root, or next to the modules in a packaged build)."""
    for vfile in (Path(__file__).parent.parent / "VERSION", Path(__file__).parent / "VERSION"):
        try:
            return vfile.read_text().strip()
        except Exception:
            continue
    return ""










@app.get("/uploads/{filename}")
async def serve_upload(filename: str):
    upload_dir = Path(config.get("images", {}).get("upload_dir", "./uploads"))
    filepath = (upload_dir / filename).resolve()
    if not filepath.is_relative_to(upload_dir.resolve()):
        return JSONResponse({"error": "invalid path"}, status_code=400)
    if filepath.is_file():
        if filepath.suffix.lower() in IMAGE_UPLOAD_EXTS:
            return FileResponse(filepath, headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})
        # Documents are downloads, never executable same-origin pages.
        return FileResponse(filepath, media_type="application/octet-stream",
                            filename=filepath.name,
                            headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})
    return JSONResponse({"error": "not found"}, status_code=404)
