"""Chat folder for research-agent replies: tools stay inside the folder; files a reply wrote are listed; delete removes it."""
import json
from types import SimpleNamespace

import pytest

import chat_files
from chat_tools import FrozenTools, tool_schemas


def snap(tmp_path, research=False):
    return {'history': [], 'research_allowed': research, 'workspace': {'path': str(tmp_path / 'folder')}}


def test_tools_write_read_list_and_stay_inside(tmp_path):
    executor = FrozenTools(snap(tmp_path), tmp_path / 'tool-events.jsonl')
    names = {s['function']['name'] for s in tool_schemas(snap(tmp_path))}
    assert {'workspace_list', 'workspace_read', 'workspace_write'} <= names and 'workspace_download' not in names   # private: no downloads
    assert 'workspace_download' in {s['function']['name'] for s in tool_schemas(snap(tmp_path, research=True))}
    assert json.loads(executor.call('workspace_write', {'path': 'notes/plan.md', 'content': '# Plan'}))['saved'] == 'notes/plan.md'
    executor.call('workspace_write', {'path': 'notes/plan.md', 'content': '\nmore', 'append': True})
    assert json.loads(executor.call('workspace_read', {'path': 'notes/plan.md'}))['text'] == '# Plan\nmore'
    assert [f['path'] for f in json.loads(executor.call('workspace_list', {}))['files']] == ['notes/plan.md']
    for bad in ('../escape.md', 'C:/Windows/x.md', 'a/../../b.md', 'script.exe', '..\\up.md'):
        assert executor.call('workspace_write', {'path': bad, 'content': 'x'}).startswith('Tool unavailable')
    assert not (tmp_path / 'escape.md').exists()
    assert json.loads(executor.call('workspace_write', {'path': '/inside.md', 'content': 'x'}))['saved'] == 'inside.md'   # a leading slash stays inside
    assert chat_files.written(tmp_path) == ['notes/plan.md', 'inside.md']


def test_repeated_identical_calls_are_refused(tmp_path):
    executor = FrozenTools({'history': [{'id': 1, 'sender': 'user', 'text': 'needle'}], 'research_allowed': False}, None)
    first = [executor.call('chat_search', {'query': 'needle'}) for _ in range(3)]
    assert 'needle' in first[0] and first[0] == first[1] and 'already made this exact call' in first[2] and executor.repeats == 1


def test_folder_follows_library_and_is_deleted_with_the_chat(tmp_path):
    runtime = SimpleNamespace(registry=SimpleNamespace(data={'library': {'root': str(tmp_path / 'cloud')}}), data_dir=tmp_path / 'data')
    room = dict(id='r1', name='Chat')
    assert chat_files.folder_for(runtime, room) == tmp_path / 'data' / 'chat-files' / 'r1'   # library folder missing: local
    (tmp_path / 'cloud').mkdir()
    folder = chat_files.folder_for(runtime, room)
    assert folder == tmp_path / 'cloud' / 'chat-files' / 'r1' and (folder / '_chat.txt').read_text('utf-8').startswith('Chattera chat: Chat')
