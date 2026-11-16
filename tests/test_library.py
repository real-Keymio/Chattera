"""Research library: identity, visibility, notes kept apart from sources, and a deduplicated, budgeted cache."""
import pytest
from chat_library import Library, source_key, run_tool


def test_identity_and_versions():
    assert source_key(doi='https://doi.org/10.1000/ABC.1') == 'doi:10.1000/abc.1' == source_key(url='https://doi.org/10.1000/abc.1')
    assert source_key(url='https://arxiv.org/abs/2401.12345v2') == 'arxiv:2401.12345v2' != source_key(url='https://arxiv.org/pdf/2401.12345v3')
    assert source_key(url='https://www.Example.org/a/?utm_source=x&id=3') == 'url:https://example.org/a?id=3'
    assert source_key(url='javascript:alert(1)') is None


def test_collections_visibility_and_explicit_sharing(tmp_path):
    lib, room = Library(tmp_path), 'room-1'
    mine = lib.upsert(room, dict(doi='10.1000/a', title='Paper A'), 'claude', origin=dict(task_id='t1'))
    again = lib.upsert(room, dict(url='https://doi.org/10.1000/A', abstract='text'), 'codex', origin=dict(task_id='t2'))
    assert again['id'] == mine['id'] and again['holders'] == ['claude', 'codex'] and again['level'] == 'abstract'   # one record, two finders
    only = lib.upsert(room, dict(url='https://example.org/x', title='Page'), 'claude')
    common = lib.upsert(room, dict(url='https://example.org/shared'), 'shared', shared=True)
    see = lambda who: {i['id'] for i in lib.view(room, who)}
    assert see('codex') == {mine['id'], common['id']} and see('claude') == {mine['id'], only['id'], common['id']} and see('mimo') == {common['id']}
    assert lib.view('room-2') == []                                                    # rooms are isolated
    lib.note(room, only['id'], 'claude', 'my critique')
    lib.share(room, only['id'], ['codex'])                                             # the source, not the finder's notes
    shared_view = next(i for i in lib.view(room, 'codex') if i['id'] == only['id'])
    assert shared_view['notes'] == [] and next(i for i in lib.view(room, 'claude') if i['id'] == only['id'])['notes'][0]['text'] == 'my critique'
    lib.share(room, only['id'], 'room', notes=True)
    assert next(i for i in lib.view(room, 'mimo') if i['id'] == only['id'])['notes'][0]['author'] == 'claude'
    lib.share(room, only['id'], 'none')
    lib.upsert(room, dict(url='https://example.org/x'), 'shared', shared=True)          # a later shared retrieval does not undo the user's choice
    assert only['id'] not in see('mimo')


def test_cache_is_content_addressed_budgeted_and_keeps_what_the_user_collects(tmp_path):
    lib = Library(tmp_path, lambda: dict(cache_limit_bytes=2500))
    first, second = lib._store(b'a' * 2000, 'txt', 'derived'), lib._store(b'a' * 2000, 'txt', 'derived')
    assert first == second and lib.stats()['files'] == 1                               # same bytes, one file
    item = lib.upsert('r', dict(url='https://example.org/p'), 'user')
    lib.update('r', lambda data: data['items'][item['id']].update(file=dict(text_sha=first), level='fulltext'))
    lib.keep('r', item['id'], True)
    other = lib._store(b'b' * 2000, 'txt', 'derived'); lib._store(b'c' * 2000, 'txt', 'derived')
    lib.cleanup()
    stats = lib.stats()
    assert stats['kept_bytes'] == 2000 and stats['cache_bytes'] <= 2500 and lib._path(first) and not lib._path(other)   # oldest unkept file went first
    assert lib.text('r', item['id'])['total_characters'] == 2000
    lib.cleanup(everything=True)
    assert lib.stats()['files'] == 1


def test_tools_respect_the_snapshot(tmp_path):
    lib = Library(tmp_path)
    hidden = lib.upsert('r', dict(url='https://example.org/secret', title='Other'), 'codex')
    snapshot = dict(model='claude', research_allowed=False, library=dict(root=str(tmp_path), room_id='r', task_tag='tag', visible_ids=[]))
    saved, audit = run_tool('library_save', dict(ref='10.1000/z', title='Z', note='n', quote='q', locator='p.3', claim='c'), snapshot, None)
    listed, _ = run_tool('library_list', {}, snapshot, None)
    assert [s['id'] for s in listed['sources']] == [saved['saved']] and audit['note'] and audit['citation']
    with pytest.raises(ValueError): run_tool('library_read', dict(item_id=hidden['id']), snapshot, None)
    with pytest.raises(ValueError): run_tool('library_read', dict(item_id=saved['saved']), snapshot, None)   # nothing cached, and a private context may not fetch
    item = lib.view('r', 'claude')[0]
    assert item['notes'][0]['author'] == 'claude' and item['citations'][0]['locator'] == 'p.3' and lib.view('r', 'codex')[0]['id'] == hidden['id']
