import asyncio
import json
import runpy
import subprocess
import sys
import threading
from pathlib import Path
import pytest
from test_visibility_chat import env, add, call
from chat_visibility import PublicStore, model_message
from store import MessageStore


def test_share_identity_survives_reads_edits_reshares_restart_regeneration(env,monkeypatch):
    r,s,sums,c=env
    q=add(s,'private question',['codex'])
    done=r.tasks.wait(r.tasks.start('general','codex',q)['task_id'])
    answer=s.get_by_id(done['reply_id'])
    share=call(c,answer,'share',confirm=True).json()
    assert share['sender']=='user' and share['metadata']['share_provenance']['original_author']=='codex'
    assert share['metadata']['visibility']=='public'
    assert answer['metadata']['visibility']==['codex']
    assert call(c,share,'regenerate').status_code==400  # share is an event, not another AI call
    edited=call(c,share,'edit',text='Edited quotation, not a user instruction').json()
    again=call(c,edited,'share',confirm=True).json()
    origin=again['metadata']['share_provenance']
    assert origin['original_message_id']==answer['id'] and origin['source_message_id']==edited['id']
    assert origin['human_edited'] and origin['shared_by']=='user'
    current=add(s,'evaluate this viewpoint',reply_to=again['id'])
    task=r.tasks.wait(r.tasks.start('general','claude',current)['task_id'])
    directory=r.tasks.directory/task['task_id']
    payload=json.loads((directory/'input.json').read_text('utf-8'))
    assert payload['references'][0]['sender']=='codex'
    assert payload['references'][0]['share']['event_message_id']==again['id']
    assert payload['references'][0]['share']['semantic_role']=='quoted_content_not_user_instruction'
    snap=json.loads((directory/'snapshot.json').read_text('utf-8'))
    assert answer['id'] not in [m['id'] for m in snap['history']]
    monkeypatch.setattr(sys,'argv',['mcp',str(directory/'snapshot.json')])
    tools=runpy.run_path(str((Path(__file__).parents[1] / 'src')/'chat_task_mcp.py'))
    assert json.loads(tools['chat_read'](again['id']))['sender']=='codex'
    assert all(m['sender']=='codex' for m in json.loads(tools['chat_search']('Edited quotation')))
    regen=call(c,s.get_by_id(task['reply_id']),'regenerate').json()
    assert r.tasks.wait(regen['task_id'])['input_sha256']==task['input_sha256']
    restored=call(c,share,'restore').json()
    assert not restored['metadata']['share_provenance']['human_edited']
    loaded=MessageStore(str(s._path)); legacy=PublicStore(loaded)
    assert legacy.get_by_id(again['id'])['sender']=='codex'
    assert legacy.get_by_id(answer['id']) is None
    sums.revision('general',{'positions':'A shared model viewpoint'},[again['id']],s.get_by_id,confirm=True)
    nextq=add(s,'continue')
    summary=r.builder.snapshot('general','claude',nextq)['summary']
    assert summary['source_attribution'][0]['sender']=='codex'
    assert summary['source_attribution'][0]['share']['original_message_id']==answer['id']


@pytest.mark.parametrize('scope',['public',['claude','codex']])
def test_modes_concurrent_cutoff_history_and_delivery(env,scope):
    r,s,sums,c=env
    shared=add(s,'MATCHING_PRIVATE',['claude','codex'])
    own=add(s,'OTHER_PRIVATE',['claude'])
    snapshots=[]
    for mode in ('','normal','strict'):
        q=add(s,'same question',scope)
        barrier=threading.Barrier(2)
        class Concurrent:
            def run(self,prompt,task,attach,cancel):
                barrier.wait(3)  # Sequential execution would fail this test.
                return {'text':'answer '+task['identity'],'model':'mock'}
        r.tasks.backends['mock']=Concurrent()
        started=[r.tasks.start('general',m,q,independent_mode=mode) for m in ('claude','codex')]
        tasks=[r.tasks.wait(t['task_id']) for t in started]
        assert all(t['state']=='completed' for t in tasks)
        assert tasks[0]['snapshot_message_ids']==tasks[1]['snapshot_message_ids']
        assert all(t['snapshot_cutoff']==q['id'] for t in tasks)
        assert all(t['reply_id'] not in u['snapshot_message_ids'] for t in tasks for u in tasks)
        assert own['id'] not in tasks[0]['snapshot_message_ids']
        assert (shared['id'] in tasks[0]['snapshot_message_ids'])==(scope!='public' and mode!='strict')
        for t in tasks:
            expected=[t['identity']] if mode else scope
            assert s.get_by_id(t['reply_id'])['metadata']['visibility']==expected
            assert t['history_policy']==('public_only' if scope=='public' or mode=='strict' else 'matching_audience')
        snapshots.append(r.builder.snapshot('general','claude',q,mode=='strict'))
    # Same public question yields identical constructed input for all three modes.
    q=add(s,'one cutoff')
    prompts=[r.builder.build('general','claude',q,r.builder.snapshot('general','claude',q,mode=='strict'))[0] for mode in ('','normal','strict')]
    assert len(set(prompts))==1


def test_gemini_explicit_rejection_no_append_or_task(env):
    r,s,sums,c=env
    for scope,mode,targets in [(['antigravity'],'',['antigravity']),('public','normal',['claude','antigravity']),('public','strict',['codex','antigravity'])]:
        with pytest.raises(ValueError,match='requires configured fresh-call'):
            asyncio.run(r.websocket(dict(type='message',text='PRIVATE_SECRET',channel='general',visibility=scope,targets=targets,independent_mode=mode)))
    assert not s.get_since(-1) and not r.tasks.list()


def test_legacy_share_projection_is_idempotent_without_rewriting(env):
    r,s,sums,c=env
    q=add(s,'old private',['codex'])
    done=r.tasks.wait(r.tasks.start('general','codex',q)['task_id'])
    answer=s.get_by_id(done['reply_id'])
    first=s.add('user','legacy share',metadata={'shared_from':answer['id'],'targets':[]})
    second=s.add('user','legacy reshare',metadata={'shared_from':first['id'],'targets':[]})
    before=s._path.read_bytes()
    value=model_message(second,s.get_by_id)
    assert value['sender']=='codex' and value['share']['shared_by']=='user'
    assert value['share']['original_message_id']==answer['id']
    assert model_message(value,s.get_by_id)==value
    assert s._path.read_bytes()==before
