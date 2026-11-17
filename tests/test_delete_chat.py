"""Deleting a chat removes its messages and everything recorded only for it, and nothing of any other chat."""
import hashlib
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI

import chattera_delete
from chat_library import Library
from chat_runtime import ChatRuntime
from store import MessageStore
from summaries import SummaryStore


@pytest.fixture
def env(tmp_path):
    registry = SimpleNamespace(get_instance=lambda _: None, is_agent_family=lambda n: n in ('claude', 'codex', 'antigravity'))
    app = SimpleNamespace(app=FastAPI(), store=MessageStore(str(tmp_path / 'agentchattr_log.jsonl')),
        summaries=SummaryStore(str(tmp_path / 'summaries.json')), registry=registry,
        room_settings={'channels': ['general', 'other'], 'username': 'user'}, _save_settings=lambda: None,
        agents=SimpleNamespace(get_status=lambda: {}), rules=SimpleNamespace(active_list=lambda: {'rules': []}),
        config={'agents': {'claude': {}, 'codex': {}, 'antigravity': {}}}, _event_loop=None)
    runtime = ChatRuntime(app, tmp_path, {'input_budget_bytes': 4000})
    for channel in ('general', 'other'):
        for model in ('claude', 'codex'): runtime.tasks.switch(channel, model, 'mock')
    runtime.library = Library(tmp_path / 'library')
    return runtime, app, tmp_path


def fill(runtime, app, tmp_path, channel, tag):
    """One chat with a message, an attachment, a finished reply, a summary, a canvas, a library source with a cached file."""
    store, room = app.store, runtime.rooms.get(channel)
    runtime.attachments.upload.mkdir(exist_ok=True)
    upload = runtime.attachments.upload / (hashlib.md5(tag.encode()).hexdigest() + '.txt')
    upload.write_text('attachment ' + tag, 'utf-8')
    question = store.add('user', 'question ' + tag, channel=channel, metadata={'visibility': 'public', 'targets': []},
                         attachments=[{'url': '/uploads/' + upload.name, 'name': upload.name}])
    task = runtime.tasks.start(channel, 'claude', question)
    done = runtime.tasks.wait(task['task_id'])
    assert done['state'] == 'completed'
    evidence = store.add('system', 'evidence ' + tag, msg_type='evidence', channel=channel, reply_to=question['id'],
                         metadata={'visibility': 'public', 'targets': [], 'evidence_id': 'ev' + tag})
    (tmp_path / 'evidence').mkdir(exist_ok=True)
    (tmp_path / 'evidence' / ('ev' + tag + '.json')).write_text('{}', 'utf-8')
    app.summaries.revision(channel, {'question': 'q #%d' % question['id']}, [question['id']], store.get_by_id, confirm=True)
    (tmp_path / 'workspaces').mkdir(exist_ok=True)
    (tmp_path / 'workspaces' / (room['id'] + '.json')).write_text('{"canvas": {}}', 'utf-8')
    cache = runtime.attachments.cache / hashlib.sha256(channel.encode()).hexdigest()
    cache.mkdir(parents=True, exist_ok=True); (cache / 'parsed.json').write_text('{}', 'utf-8')
    library = runtime.library
    item = library.upsert(room['id'], dict(url='https://example.org/' + tag, title='Source ' + tag), 'claude', shared=True,
                          origin=dict(message_id=question['id']))
    sha = library._store(('text of ' + tag).encode(), 'txt', 'derived', '')
    common = library._store(b'shared text', 'txt', 'derived', '')
    library.update(room['id'], lambda data: data['items'][item['id']].update(file=dict(text_sha=sha, original_sha=common)))
    return dict(room=room, question=question, reply=done['reply_id'], evidence=evidence, task=done['task_id'], upload=upload, sha=sha, common=common)


def test_delete_removes_one_chat_and_keeps_the_other(env):
    runtime, app, tmp_path = env
    gone = fill(runtime, app, tmp_path, 'general', 'a')
    kept = fill(runtime, app, tmp_path, 'other', 'b')
    before_ids = {m['id'] for m in app.store.get_since(-1, channel='other')}

    result = chattera_delete.delete_room(runtime, app, tmp_path, gone['room']['id'])
    assert result['messages'] == 3 and result['attachments'] == 1 and result['calls'] == 1
    assert result['library'] == dict(sources=1, files_removed=1)  # the file the other chat also uses stays

    # This chat: nothing left anywhere.
    with pytest.raises(ValueError): runtime.rooms.get(gone['room']['id'])
    assert not app.store.get_since(-1, channel='general')
    lines = (tmp_path / 'agentchattr_log.jsonl').read_text('utf-8')
    assert 'question a' not in lines and 'question b' in lines
    assert not gone['upload'].exists() and not (tmp_path / 'evidence' / 'eva.json').exists()
    assert not (runtime.tasks.directory / gone['task']).exists() and gone['task'] not in runtime.tasks.tasks
    assert gone['task'] not in runtime.tasks.log.read_text('utf-8')
    assert app.summaries.versions('general') == [] and app.summaries.get('general') is None
    assert '"general"' not in (tmp_path / 'summary_versions.jsonl').read_text('utf-8')
    assert not (tmp_path / 'workspaces' / (gone['room']['id'] + '.json')).exists()
    assert not (runtime.attachments.cache / hashlib.sha256(b'general').hexdigest()).exists()
    assert not (tmp_path / 'library' / 'rooms' / (gone['room']['id'] + '.json')).exists()
    assert gone['sha'] not in runtime.library.files() and gone['common'] in runtime.library.files()
    with pytest.raises(ValueError):  # nothing can be added to a deleted chat
        app.store.add('user', 'late', channel='general', metadata={'visibility': 'public', 'targets': []})

    # The other chat: untouched, same message ids.
    assert {m['id'] for m in app.store.get_since(-1, channel='other')} == before_ids
    assert kept['upload'].exists() and (tmp_path / 'evidence' / 'evb.json').exists()
    assert (runtime.tasks.directory / kept['task']).exists() and app.summaries.versions('other')
    assert (tmp_path / 'workspaces' / (kept['room']['id'] + '.json')).exists()
    assert runtime.library.load(kept['room']['id'])['items'] and kept['sha'] in runtime.library.files()

    # It survives a restart, and the backup copy of the log is removed at the next start.
    assert (tmp_path / ('agentchattr_log.jsonl' + chattera_delete.BACKUP_SUFFIX)).exists()
    chattera_delete.clean_backups(tmp_path)
    assert not (tmp_path / ('agentchattr_log.jsonl' + chattera_delete.BACKUP_SUFFIX)).exists()
    again = MessageStore(str(tmp_path / 'agentchattr_log.jsonl'))
    assert {m['id'] for m in again.get_since(-1)} == before_ids
    later = app.store.add('user', 'next', channel='other', metadata={'visibility': 'public', 'targets': []})
    assert later['id'] > max(before_ids | {gone['reply']})  # ids are never reused


def test_branch_gets_its_own_library_copy(tmp_path):
    library = Library(tmp_path / 'library')
    item = library.upsert('src', dict(url='https://example.org/p', title='P'), 'claude', shared=True, origin=dict(message_id=5, task_id='t'))
    library.note('src', item['id'], 'user', 'my note', origin=dict(message_id=9))
    sha = library._store(b'body', 'txt', 'derived', '')
    library.update('src', lambda data: data['items'][item['id']].update(file=dict(text_sha=sha)))
    library.keep('src', item['id'], True)

    assert library.copy_room('src', 'branch', {5: 105}) == 1
    copy = library.load('branch')
    assert copy['items'][item['id']]['finders'][0]['message_id'] == 105
    assert 'message_id' not in copy['notes'][0]  # its message was not copied into the branch
    assert sorted(library.files()[sha]['kept']) == ['branch:' + item['id'], 'src:' + item['id']]

    library.forget_room('src')   # deleting the source leaves the branch's library and file in place
    assert library.load('branch')['items'] and sha in library.files()
    assert library.files()[sha]['kept'] == ['branch:' + item['id']]
    assert library.forget_room('branch') == dict(sources=1, files_removed=1) and sha not in library.files()
