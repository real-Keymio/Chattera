"""Finding a usable Claude Code, and reporting why a CLI or a model listing failed (instead of a bare exit code / HTTP status)."""
import asyncio
import json
import os
import sys
import threading
from pathlib import Path

import httpx
import pytest

import chattera
import chat_api
import cli_adapters
from chat_backends import CLIBackend
from chattera_paths import Home

CLAUDE = cli_adapters.get('claude')


def fake_windows_profile(tmp_path, monkeypatch):
    for var, sub in (('USERPROFILE', 'user'), ('LOCALAPPDATA', 'user/AppData/Local'), ('APPDATA', 'user/AppData/Roaming')):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(var, str(tmp_path / sub))
    monkeypatch.setattr('shutil.which', lambda *a, **k: None)
    bundled = tmp_path / 'user/AppData/Local/Claude-3p/claude-code/2.1.325/abc/claude.exe'
    bundled.parent.mkdir(parents=True); bundled.write_bytes(b'')
    return bundled


def test_standalone_claude_code_wins_over_the_desktop_bundled_copy(tmp_path, monkeypatch):
    bundled = fake_windows_profile(tmp_path, monkeypatch)
    assert CLAUDE.discover() == dict(found=True, executable=str(bundled.resolve()), source='default install location')
    standalone = tmp_path / 'user/.local/bin/claude.exe'
    standalone.parent.mkdir(parents=True); standalone.write_bytes(b'')
    assert CLAUDE.discover() == dict(found=True, executable=str(standalone.resolve()), source='default install location')


def test_connection_is_looked_up_again_when_the_bundled_copy_moved(tmp_path, monkeypatch):
    """The desktop app replaces its bundled copy on update; the old version folder disappears."""
    import logging
    bundled = fake_windows_profile(tmp_path, monkeypatch)
    home = Home(tmp_path / 'h').ensure()
    cfg = chattera.first_config(home)
    cfg['backends']['claude'] = dict(executable=str(bundled.parent.parent.parent / '2.1.317' / 'old' / 'claude.exe'), model='m', enabled=True)
    home.chat_config.write_text(json.dumps(cfg), 'utf-8')
    monkeypatch.setattr(cli_adapters.get('codex'), 'discover', lambda extra=(): dict(found=False, executable=None, source=None))
    entry = chattera.attach_clis(cfg, home, {}, logging.getLogger('t'))['backends']['claude']
    assert entry['executable'] == str(bundled.resolve()) and entry['enabled'] is True and entry['model'] == 'm'


def test_claude_failure_reason_reads_the_cli_result():
    failed = '\n'.join(json.dumps(e) for e in [{'type': 'system', 'subtype': 'init'},
        {'type': 'result', 'subtype': 'success', 'is_error': True, 'result': 'Failed to authenticate: OAuth session expired'}])
    assert CLAUDE.failure_reason(failed) == 'Failed to authenticate: OAuth session expired'
    assert CLAUDE.failure_reason('{"type": "result", "is_error": false, "result": "fine"}') is None
    assert CLAUDE.failure_reason('not json') is None
    assert cli_adapters.get('codex').failure_reason('anything') is None


def test_cli_exit_keeps_what_the_cli_said(tmp_path):
    line = json.dumps({'type': 'result', 'is_error': True, 'result': 'Failed to authenticate: OAuth session expired'})
    script = f"import sys; sys.stdin.read(); print({line!r}); sys.exit(1)"
    class Failing(CLIBackend):
        def command(self, directory, baseline=False, search=False):
            return [sys.executable, '-c', script]
    backend = Failing('claude', dict(executable=sys.executable, model='m', timeout=30))
    with pytest.raises(RuntimeError) as err:
        backend.run(json.dumps({'messages': []}), dict(directory=str(tmp_path)), lambda tree: None, threading.Event())
    assert str(err.value) == 'claude CLI exit 1; no reply committed; CLI said: Failed to authenticate: OAuth session expired'


@pytest.mark.parametrize('body,said', [
    ({'type': 'error', 'error': {'type': 'invalid_request_error', 'message': 'anthropic-version: header is required'}},
     ': anthropic-version: header is required'),
    ({'error': 'plain text error'}, ': plain text error'),
    (None, '')])
def test_model_listing_failure_keeps_the_provider_message(monkeypatch, body, said):
    def handler(request):
        return httpx.Response(400, content=json.dumps(body).encode() if body is not None else b'<html>')
    real = httpx.AsyncClient
    monkeypatch.setattr(chat_api.httpx, 'AsyncClient', lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    cfg = dict(type='api', protocol='anthropic-messages', base_url='https://api.example.test/v1', timeout=5)
    result = asyncio.run(chat_api.fetch_models(cfg, 'sk-test-key-0000'))
    assert result['status'] == 'UNKNOWN' and result['source'] == 'models endpoint HTTP 400' + said
