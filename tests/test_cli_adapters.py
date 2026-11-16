"""Contract tests for the subscription-CLI adapters (docs/CLI_ADAPTER.md). No real CLI is needed."""
import json
import sys
from pathlib import Path

import pytest

import cli_adapters
from cli_adapters import ROOM_TOOLS
from cli_adapters.base import CLIAdapter
from chat_backends import CLIBackend

ADAPTERS = [cli_adapters.get(n) for n in cli_adapters.names()]
MCP = {'command': 'server.exe', 'args': ['--worker', 'chat_task_mcp', 'snapshot.json']}
FORBIDDEN = ('--dangerously', 'bypass', 'workspace-write', 'danger-full-access', '"Bash', ',Bash', '"Edit', ',Edit', '"Write', ',Write')


def cfg(adapter, tmp_path):
    return dict(executable=str(tmp_path / 'cli.exe'), model='model-x', effort='medium', timeout=60)


def test_registry_has_reference_adapters():
    assert {'claude', 'codex'} <= set(cli_adapters.names())
    for adapter in ADAPTERS:
        assert isinstance(adapter, CLIAdapter) and adapter.label and adapter.commands
        assert set(adapter.capabilities) == {'images', 'files', 'search', 'context_window'}
    with pytest.raises(ValueError):
        cli_adapters.register(cli_adapters.get('claude'))


@pytest.mark.parametrize('adapter', ADAPTERS, ids=lambda a: a.name)
def test_discover_explicit_path_and_missing(adapter, tmp_path):
    folder = tmp_path / 'tools'; folder.mkdir()
    exe = folder / (adapter.commands[0] + '.exe'); exe.write_bytes(b'')
    assert adapter.discover([str(folder)]) == dict(found=True, executable=str(exe.resolve()), source='user search path')
    hit = adapter.discover([str(tmp_path / 'nowhere')])
    assert hit['source'] != 'user search path'


@pytest.mark.parametrize('adapter', ADAPTERS, ids=lambda a: a.name)
def test_command_is_non_interactive_and_tool_restricted(adapter, tmp_path):
    c = cfg(adapter, tmp_path)
    normal = adapter.command(c, tmp_path, MCP)
    text = json.dumps(normal)
    assert 'model-x' in text and not any(f in text for f in FORBIDDEN)
    if adapter.name == 'claude':
        assert '-p' in normal and '--tools=' in normal and normal[normal.index('--permission-mode') + 1] == 'dontAsk'
    else:
        assert 'exec' in normal and normal[normal.index('-s') + 1] == 'read-only'
        disabled = {normal[i + 1] for i, a in enumerate(normal) if a == '--disable'}
        assert {'shell_tool', 'apps', 'plugins'} <= disabled
    for tool in ROOM_TOOLS: assert tool in text
    baseline = json.dumps(adapter.command(c, tmp_path, MCP, baseline=True))
    assert 'chat_task_mcp' not in baseline
    search = json.dumps(adapter.command(c, tmp_path, MCP, search=True))
    assert ('WebSearch' in search) if adapter.name == 'claude' else ('web_search=\\"live\\"' in search)
    assert ('WebSearch' not in text) if adapter.name == 'claude' else ('web_search=\\"disabled\\"' in text)


def test_claude_mcp_file_is_written_into_sandbox(tmp_path):
    adapter = cli_adapters.get('claude')
    adapter.command(cfg(adapter, tmp_path), tmp_path, MCP)
    assert json.loads((tmp_path / 'mcp.json').read_text('utf-8')) == {'mcpServers': {'room': MCP}}
    adapter.command(cfg(adapter, tmp_path), tmp_path, MCP, baseline=True)
    assert json.loads((tmp_path / 'mcp.json').read_text('utf-8')) == {'mcpServers': {}}


@pytest.mark.parametrize('adapter', ADAPTERS, ids=lambda a: a.name)
def test_prepare_input_without_images_is_plain_stdin(adapter, tmp_path):
    command = adapter.command(cfg(adapter, tmp_path), tmp_path, MCP)
    new, data = adapter.prepare_input(cfg(adapter, tmp_path), command, '{"x":1}', {'attachment_evidence': []}, {})
    assert new == command and data == b'{"x":1}'


def test_codex_prepare_input_adds_images(tmp_path):
    adapter = cli_adapters.get('codex')
    command = adapter.command(cfg(adapter, tmp_path), tmp_path, MCP)
    snapshot = {'attachment_content': {'5:0': {'image_path': 'C:/img.png'}}}
    new, _ = adapter.prepare_input(cfg(adapter, tmp_path), command, '{}', {'attachment_evidence': [{'kind': 'image', 'message_id': 5, 'index': 0}]}, snapshot)
    assert new[-3:] == ['--image', 'C:/img.png', '-']


CLAUDE_OK = '\n'.join(json.dumps(e) for e in [
    {'type': 'system', 'subtype': 'init', 'model': 'claude-x', 'tools': [], 'mcp_servers': []},
    {'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'name': 'mcp__room__calc'}]}},
    {'type': 'result', 'result': 'answer', 'usage': {'input_tokens': 3, 'output_tokens': 5, 'cache_read_input_tokens': 7}}])
CODEX_OK = '\n'.join(json.dumps(e) for e in [
    {'type': 'item.completed', 'item': {'type': 'mcp_tool_call', 'tool': 'calc'}},
    {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'answer'}},
    {'type': 'turn.completed', 'usage': {'input_tokens': 50, 'cached_input_tokens': 34, 'output_tokens': 9}}])


@pytest.mark.parametrize('name,output', [('claude', CLAUDE_OK), ('codex', CODEX_OK)])
def test_parse_success_and_usage(name, output, tmp_path):
    adapter = cli_adapters.get(name)
    result = adapter.parse(cfg(adapter, tmp_path), output)
    assert result['text'] == 'answer' and result['requested_model'] == 'model-x' and result['tool_calls']
    usage = result['usage_fields']
    assert usage['output_tokens'] in (5, 9) and usage['subscription_debit'] == 'UNKNOWN' and usage['billed_amount'] == 'UNKNOWN'
    assert adapter.usage({})['input_tokens'] == 'UNKNOWN'


@pytest.mark.parametrize('name,bad', [
    ('claude', json.dumps({'type': 'result', 'is_error': True, 'result': 'x'})), ('claude', ''),
    ('codex', json.dumps({'type': 'turn.failed'})), ('codex', json.dumps({'type': 'turn.completed'})), ('codex', '')])
def test_parse_refuses_without_final_answer(name, bad, tmp_path):
    adapter = cli_adapters.get(name)
    with pytest.raises(RuntimeError):
        adapter.parse(cfg(adapter, tmp_path), bad)


def fake_exchange(reply):
    return lambda *args, **kwargs: reply


def test_claude_model_list(monkeypatch, tmp_path):
    from cli_adapters import claude_code
    reply = {'type': 'control_response', 'response': {'response': {'models': [
        {'value': 'default', 'resolvedModel': 'claude-a', 'displayName': 'Default (recommended)', 'description': 'A', 'supportsEffort': True, 'supportedEffortLevels': ['low', 'high']},
        {'value': 'claude-b', 'displayName': 'B', 'description': 'B model'}]}}}
    monkeypatch.setattr(claude_code, 'exchange', fake_exchange(reply))
    models = cli_adapters.get('claude').list_models(cfg(None, tmp_path))
    assert [m['id'] for m in models] == ['claude-a', 'claude-b'] and models[0]['is_default'] and models[0]['effort_values'] == ['low', 'high']
    monkeypatch.setattr(claude_code, 'exchange', fake_exchange({'type': 'control_response'}))
    with pytest.raises(ValueError):
        cli_adapters.get('claude').list_models(cfg(None, tmp_path))


def test_codex_model_list_follows_pages(monkeypatch, tmp_path):
    from cli_adapters import codex
    pages = iter([{'id': 1, 'result': {'data': [{'model': 'm1', 'displayName': 'M1', 'isDefault': True}], 'nextCursor': 'c2'}},
                  {'id': 1, 'result': {'data': [{'model': 'm2', 'hidden': True}, {'model': 'm3', 'supportedReasoningEfforts': [{'reasoningEffort': 'low'}]}]}}])
    monkeypatch.setattr(codex, 'exchange', lambda *a, **k: next(pages))
    models = cli_adapters.get('codex').list_models(cfg(None, tmp_path))
    assert [m['id'] for m in models] == ['m1', 'm3'] and models[0]['is_default'] and models[1]['effort_values'] == ['low']


@pytest.mark.parametrize('name,code,out,status', [
    ('claude', 0, '{"loggedIn": true, "authMethod": "claude.ai", "email": "someone@example.com"}', 'signed-in'),
    ('claude', 1, '{"loggedIn": false}', 'signed-out'),
    ('claude', 1, 'garbage', 'UNKNOWN'),
    ('codex', 0, 'Logged in using ChatGPT', 'signed-in'),
    ('codex', 1, 'Not logged in', 'signed-out'),
    ('codex', 2, 'error: something', 'UNKNOWN')])
def test_auth_status_from_official_command_only(monkeypatch, tmp_path, name, code, out, status):
    module = __import__('cli_adapters.' + ('claude_code' if name == 'claude' else 'codex'), fromlist=['x'])
    seen = []
    monkeypatch.setattr(module, 'status_command', lambda command, **k: (seen.append(command), (code, out))[1])
    result = cli_adapters.get(name).auth_status(cfg(None, tmp_path))
    assert result['status'] == status and '@' not in json.dumps(result)
    assert seen[0][1:] == (['auth', 'status'] if name == 'claude' else ['login', 'status'])


def test_backend_builds_for_every_adapter_and_rejects_unknown(tmp_path):
    for name in cli_adapters.names():
        backend = CLIBackend(name, cfg(None, tmp_path))
        assert backend.adapter is cli_adapters.get(name)
        command = json.dumps(backend.command(tmp_path))
        where = (tmp_path / 'mcp.json').read_text('utf-8') if name == 'claude' else command
        assert 'chat_task_mcp' in where and 'snapshot.json' in where
    with pytest.raises(ValueError):
        CLIBackend('not-a-cli', {})
