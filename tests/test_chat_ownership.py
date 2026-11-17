import json
import sys
import threading
import time
from pathlib import Path

import pytest
from store import MessageStore
from summaries import SummaryStore
from chat_rooms import RoomCatalog, ContextBuilder
from chat_tasks import TaskManager
from chat_backends import CLIBackend, ProcessTree


@pytest.fixture
def env(tmp_path):
    store = MessageStore(str(tmp_path / "history.jsonl"))
    summaries = SummaryStore(str(tmp_path / "summaries.json"))
    rooms = RoomCatalog(tmp_path / "rooms.json", ["general", "other"])
    builder = ContextBuilder(store, summaries, rooms, 4000)
    manager = TaskManager(tmp_path, builder)
    manager.switch("general", "claude", "mock")
    return store, summaries, rooms, builder, manager


def test_legacy_mapping_lifecycle_restart_preserves_bytes(env, tmp_path):
    store, summaries, rooms, builder, manager = env
    m = store.add("user", "old history")
    original = store._path.read_bytes()
    room = rooms.get("general")
    rooms.update(room["id"], name="研究 α", archived=True)
    rooms.update(room["id"], archived=False)
    loaded = RoomCatalog(tmp_path / "rooms.json", ["general"])
    assert loaded.get("general")["id"] == room["id"]
    assert loaded.get(room["id"])["name"] == "研究 α"
    assert store._path.read_bytes() == original
    assert MessageStore(str(store._path)).get_by_id(m["id"])["text"] == "old history"


def test_budget_and_scope_with_rendered_reference(env):
    store, summaries, rooms, builder, manager = env
    old = store.add("claude", "**rendered** old claim")
    foreign = store.add("user", "SECRET OTHER ROOM", channel="other")
    for i in range(30):
        store.add("user", "历史" * 100)
    current = store.add("user", "consider this", reply_to=old["id"],
                        metadata={"quote_text": "rendered", "action_instruction": "question the premise"})
    prompt, audit = builder.build("general", "claude", current)
    assert len(prompt.encode()) <= builder.budget
    assert "SECRET OTHER ROOM" not in prompt
    assert old["id"] in audit["message_ids"]
    assert len(audit["message_ids"]) < len(builder.history("general"))
    assert builder.search("general", "old claim")[0]["id"] == old["id"]
    with pytest.raises(ValueError):
        builder.read("general", foreign["id"])
    bad = store.add("user", "bad reference", reply_to=foreign["id"])
    with pytest.raises(ValueError):
        builder.build("general", "claude", bad)
    too_long = store.add("user", "x" * 10000)
    with pytest.raises(ValueError):
        builder.build("general", "claude", too_long)


def test_summary_draft_confirm_versions_restart(env, tmp_path):
    store, summaries, rooms, builder, manager = env
    old = store.add("user", "old evidence")
    sections = {"question": "Still open", "disagreements": "A versus B"}
    summaries.revision("general", sections, [old["id"]], store.get_by_id)
    assert summaries.confirmed("general") is None
    summaries.revision("general", sections, [old["id"]], store.get_by_id, confirm=True)
    current = store.add("user", "continue")
    prompt, audit = builder.build("general", "codex", current)
    assert audit["summary_version"] == 2
    assert old["id"] not in audit["message_ids"]
    assert SummaryStore(str(summaries._path)).confirmed("general")["version"] == 2
    assert summaries.get("general")["sections"] == sections
    assert "Still open" in prompt


def test_mock_single_reply_cancel_switch_and_restart(env, tmp_path):
    store, summaries, rooms, builder, manager = env
    current = store.add("user", "open question")
    task = manager.start("general", "claude", current, mock_delay=.1)
    with pytest.raises(ValueError):
        manager.start("general", "claude", current)
    done = manager.wait(task["task_id"])
    assert done["state"] == "completed"
    assert len([m for m in store.get_since(-1) if m["sender"] == "claude"]) == 1
    for operation in ("cancel", "switch"):
        task = manager.start("general", "claude", current, mock_delay=.1)
        if operation == "cancel":
            manager.cancel("general")
        else:
            manager.switch("general", "claude", "legacy")
        assert manager.wait(task["task_id"])["state"] == "cancelled"
    assert len([m for m in store.get_since(-1) if m["sender"] == "claude"]) == 1
    recovered = TaskManager(tmp_path, builder)
    assert recovered.tasks[done["task_id"]]["reply_id"] == done["reply_id"]


def test_late_backend_ignoring_cancel_cannot_write(env):
    store, summaries, rooms, builder, manager = env
    class Late:
        def run(self, *args):
            time.sleep(.1)
            return {"text": "late", "model": "mock"}
    manager.backends["mock"] = Late()
    current = store.add("user", "question")
    task = manager.start("general", "claude", current)
    manager.switch("general", "claude", "legacy")
    manager.switch("general", "claude", "mock")
    assert manager.wait(task["task_id"])["state"] == "cancelled"
    assert len(store.get_since(-1)) == 1


def test_rooms_alternate_no_context_leak(env):
    store, summaries, rooms, builder, manager = env
    for channel, text in [("general", "MARKER_A"), ("other", "MARKER_B"), ("general", "continue A"), ("other", "continue B")]:
        current = store.add("user", text, channel=channel)
        prompt, audit = builder.build(channel, "claude", current)
        assert ("MARKER_B" if channel == "general" else "MARKER_A") not in prompt
        assert all(store.get_by_id(i)["channel"] == channel for i in audit["message_ids"])


def test_process_tree_children_die(tmp_path):
    import os
    import subprocess
    if os.name != "nt":
        pytest.skip("Windows process-tree verification")
    marker = tmp_path / "child.txt"
    child = "import time,pathlib; time.sleep(1); pathlib.Path(" + repr(str(marker)) + ").write_text('orphan')"
    parent = "import sys,subprocess,time; sys.stdin.read(); subprocess.Popen([sys.executable,'-c'," + repr(child) + "]); print('spawned',flush=True); time.sleep(30)"
    proc = subprocess.Popen([sys.executable, "-c", parent], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    tree = ProcessTree(proc)
    proc.stdin.close()
    assert proc.stdout.readline().strip() == b"spawned"
    tree.kill()
    proc.wait(timeout=5)
    tree.close()
    time.sleep(1.2)
    assert not marker.exists()


def test_cli_parsing_and_no_write_tools(tmp_path):
    claude = CLIBackend("claude", {"executable": "claude", "model": "opus"})
    args = claude.command(tmp_path)
    assert "--restricted" in args and "--tools=" in args
    assert "chat_send" not in " ".join(args)
    output = '\n'.join(json.dumps(e) for e in [
        {"type": "system", "subtype": "init", "model": "actual"},
        {"type": "result", "result": "answer", "usage": {"input_tokens": 42}}])
    assert claude.parse(output)["usage"]["input_tokens"] == 42
    codex = CLIBackend("codex", {"executable": "codex", "model": "gpt-6-astra"})
    assert "--ignore-user-config" in codex.command(tmp_path)
    with pytest.raises(RuntimeError):
        codex.parse('{"type":"turn.failed"}')


def test_runtime_append_only_and_owner_guard(env, tmp_path):
    from types import SimpleNamespace
    from chat_runtime import ChatRuntime
    store, summaries, rooms, builder, manager = env
    registry = SimpleNamespace(get_instance=lambda name: None)
    app = SimpleNamespace(store=store, summaries=summaries, registry=registry,
        room_settings={"channels": ["general", "other"]}, _save_settings=lambda: None,
        agents=SimpleNamespace(get_status=lambda: {}), rules=SimpleNamespace(active_list=lambda: {"rules": []}),
        config={"agents": {"claude": {}, "codex": {}}}, _event_loop=None)
    runtime = ChatRuntime(app, tmp_path, {})
    before = store.add("user", "keep this")["id"]
    original = store._path.read_bytes()
    for operation in (lambda: store.delete([before]), lambda: store.clear(),
                      lambda: store.rename_channel("general", "new"),
                      lambda: store.update_message(before, {"text": "overwrite"})):
        with pytest.raises(ValueError): operation()
    assert store._path.read_bytes() == original
    runtime.tasks.switch("general", "claude", "mock")
    with pytest.raises(ValueError): store.add("claude", "unauthorized old reply")
    current = store.add("user", "new question")
    task = runtime.tasks.start("general", "claude", current)
    assert runtime.tasks.wait(task["task_id"])["state"] == "completed"
    runtime.tasks.switch("general", "claude", "legacy")
    registry.get_instance = lambda name: {"base": "claude", "registered_at": 1} if name == "claude" else None
    assert not runtime.legacy_allowed("claude", "general")
    with pytest.raises(ValueError): store.add("claude", "stale session after rollback")


def test_real_process_cancel_and_timeout_without_model(env, tmp_path):
    store, summaries, rooms, builder, manager = env
    import os
    if os.name != "nt": pytest.skip("Windows owned-process integration")
    class DummyCLI(CLIBackend):
        def command(self, directory, baseline=False):
            return [sys.executable, "-c", "import sys,time; sys.stdin.read(); time.sleep(30)"]
    manager.backends["mock"] = DummyCLI("claude", {"timeout": .15})
    current = store.add("user", "no model test")
    task = manager.start("general", "claude", current)
    assert manager.wait(task["task_id"])["state"] == "failed"
    assert manager.tasks[task["task_id"]]["tree"].process.poll() is not None
    manager.backends["mock"].config["timeout"] = 30
    task = manager.start("general", "claude", current)
    for _ in range(100):
        if manager.tasks[task["task_id"]].get("tree"): break
        time.sleep(.01)
    manager.cancel("general")
    assert manager.wait(task["task_id"])["state"] == "cancelled"
    assert manager.tasks[task["task_id"]]["tree"].process.poll() is not None
    assert not [m for m in store.get_since(-1) if m["sender"] == "claude"]


def test_task_mcp_room_cutoff_and_limits(env, tmp_path):
    import asyncio
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    store, summaries, rooms, builder, manager = env
    old = store.add("user", "old needle")
    foreign = store.add("user", "foreign needle", channel="other")
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(json.dumps(dict(history_path=str(store._path), channel="general",
                                       max_id=foreign["id"], summary=None)), "utf-8")
    future = store.add("user", "future needle")
    async def check():
        server = StdioServerParameters(command=sys.executable, args=[str(Path(__file__).parents[1] / "src" / "chat_task_mcp.py"), str(snapshot)])
        async with stdio_client(server) as (read, write):
            async with ClientSession(read, write) as client:
                await client.initialize()
                names = [t.name for t in (await client.list_tools()).tools]
                assert set(names) == {"chat_read", "chat_search", "chat_context", "research_search", "research_read", "attachment_read", "web_search", "web_read", "calc", "library_list", "library_read", "library_save", "library_view_page"}
                result = await client.call_tool("chat_search", {"query": "needle"})
                assert "old needle" in result.content[0].text
                assert "foreign needle" not in result.content[0].text
                assert "future needle" not in result.content[0].text
                for target in (foreign["id"], future["id"]):
                    result = await client.call_tool("chat_read", {"message_id": target})
                    assert "not found" in result.content[0].text
                from chat_tools import TOOL_BUDGET  # 3 calls above; the budget is then spent and the next one is refused
                for _ in range(TOOL_BUDGET - 2): result = await client.call_tool("chat_context", {})
                assert "limit reached" in result.content[0].text
    asyncio.run(check())
