"""R8 security and append-only lifecycle tests: no paid/model traffic."""
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from store import MessageStore
from summaries import SummaryStore
from chat_runtime import ChatRuntime
from chat_versions import install, validate
from chat_visibility import PublicStore, PublicSummaries, effective


@pytest.fixture
def env(tmp_path):
    registry = SimpleNamespace(get_instance=lambda _: None,
        is_agent_family=lambda n: n in ('claude', 'codex', 'antigravity'))
    app = SimpleNamespace(app=FastAPI(), store=MessageStore(str(tmp_path/'history.jsonl')),
        summaries=SummaryStore(str(tmp_path/'summaries.json')), registry=registry,
        room_settings={'channels':['general','other'], 'username':'user'}, _save_settings=lambda: None,
        agents=SimpleNamespace(get_status=lambda: {}), rules=SimpleNamespace(active_list=lambda: {'rules':[]}),
        config={'agents':{'claude':{},'codex':{},'antigravity':{}}}, _event_loop=None)
    runtime = ChatRuntime(app, tmp_path, {'input_budget_bytes':4000})
    for model in ('claude','codex'): runtime.tasks.switch('general', model, 'mock')
    install(runtime)
    return runtime, app.store, app.summaries, TestClient(app.app)


def add(store, text, scope='public', **kw):
    return store.add('user', text, metadata={'visibility':scope, 'targets':[]}, **kw)


def call(client, msg, op, **body):
    return client.post(f'/api/chat/message/{msg["id"]}/{op}', json=body)


@pytest.mark.parametrize('model,other', [('claude','codex'),('codex','claude')])
def test_audience_input_legacy_reference_summary(env, model, other):
    r,s,sums,client = env
    public=add(s,'public needle')
    secret=add(s,'private needle', [other])
    own=add(s,'own needle', [model])
    foreign=add(s,'foreign needle', channel='other')
    current=add(s,'continue',[model])
    snap=r.builder.snapshot('general',model,current)
    assert {m['id'] for m in snap['history']} == {public['id'],own['id'],current['id']}
    assert not snap['research_allowed']
    prompt,audit=r.builder.build('general',model,current,snap)
    assert 'private needle' not in prompt and 'foreign needle' not in prompt
    legacy=PublicStore(s)
    assert legacy.get_by_id(secret['id']) is None
    assert legacy.get_by_id(own['id']) is None
    assert [m['id'] for m in legacy.get_recent(channel='general')] == [public['id']]
    with pytest.raises(ValueError): add(s,'leaking quote', reply_to=secret['id'])
    with pytest.raises(ValueError): add(s,'leaking quote',[model], reply_to=secret['id'])
    with pytest.raises(ValueError): sums.revision('general',{'question':'private'},[secret['id']],s.get_by_id,confirm=True)
    with pytest.raises(ValueError): validate(r,'general',{'visibility':[model],'targets':[other]})
    with pytest.raises(ValueError): validate(r,'general',{'visibility':['antigravity'],'targets':[]})
    r.tasks.switch('general', model, 'legacy')
    with pytest.raises(ValueError): add(s,'private after legacy switch',[model])


def test_independent_snapshot_cancel_failure_and_share(env):
    r,s,sums,c=env
    private=add(s,'Claude private',['claude'])
    q=add(s,'shared question')
    tasks=[r.tasks.start('general',model,q,independent_mode='strict', independent_group='group',mock_delay=.1)
           for model in ('claude','codex')]
    assert tasks[0]['snapshot_message_ids'] == tasks[1]['snapshot_message_ids'] == [q['id']]
    assert tasks[0]['snapshot_cutoff'] == tasks[1]['snapshot_cutoff'] == q['id']
    done=[r.tasks.wait(t['task_id']) for t in tasks]
    answers=[s.get_by_id(t['reply_id']) for t in done]
    assert all(t['state']=='completed' for t in done)
    for model, answer in zip(('claude','codex'),answers):
        assert answer['metadata']['visibility']==[model]
        snapshot=json.loads((r.tasks.directory/answer['metadata']['task_id']/'snapshot.json').read_text('utf-8'))
        assert snapshot['research_allowed'] and snapshot['history']==[q]
    assert call(c,answers[0],'share').status_code==400
    shared=call(c,answers[0],'share',confirm=True,text='reviewed public answer').json()
    assert shared['metadata']['shared_from']==answers[0]['id']
    later=add(s,'continue')
    assert shared['id'] in [m['id'] for m in r.builder.snapshot('general','codex',later)['history']]
    assert answers[0]['id'] not in [m['id'] for m in r.builder.snapshot('general','codex',later)['history']]
    private_followup=add(s,'continue privately',['claude'])
    ordinary=r.builder.snapshot('general','claude',private_followup)
    strict=r.builder.snapshot('general','claude',later,True)
    assert private['id'] in [m['id'] for m in ordinary['history']]
    assert private['id'] not in [m['id'] for m in strict['history']]
    a=r.tasks.start('general','claude',later,independent_mode='strict',independent_group='two',mock_delay=.01)
    b=r.tasks.start('general','codex',later,independent_mode='strict',independent_group='two',mock_delay=2)
    a=r.tasks.wait(a['task_id'])
    assert call(c,s.get_by_id(a['reply_id']),'share',confirm=True).status_code==400
    r.tasks.cancel('general','codex')
    assert r.tasks.wait(b['task_id'])['state']=='cancelled'
    assert s.get_by_id(a['reply_id'])
    assert call(c,s.get_by_id(a['reply_id']),'share',confirm=True).status_code==200
    class PartialFailure:
        def run(self,prompt,task,attach,cancel):
            if task['identity']=='codex': raise TimeoutError('mock timeout')
            return {'text':'survives peer failure','model':'mock'}
    r.tasks.backends['mock']=PartialFailure()
    a=r.tasks.start('general','claude',later,independent_mode='strict')
    b=r.tasks.start('general','codex',later,independent_mode='strict')
    assert r.tasks.wait(a['task_id'])['state']=='completed'
    assert r.tasks.wait(b['task_id'])['state']=='failed'


def test_edit_restore_regenerate_exact_snapshot_restart(env,tmp_path):
    r,s,sums,c=env
    old=add(s,'original question',['claude'])
    done=r.tasks.wait(r.tasks.start('general','claude',old)['task_id'])
    answer=s.get_by_id(done['reply_id'])
    original_bytes=s._path.read_bytes()
    edit=call(c,old,'edit',text='edited question').json()
    assert s._path.read_bytes().startswith(original_bytes)
    assert edit['metadata']['replaces_message_id']==old['id']
    assert [m['id'] for m in effective(s.get_since(-1))]==[answer['id'],edit['id']]
    future=add(s,'future information',['claude'])
    regen=call(c,answer,'regenerate').json()
    regenerated=r.tasks.wait(regen['task_id'])
    assert regenerated['state']=='completed'
    assert regenerated['input_sha256']==done['input_sha256']
    assert regenerated['snapshot_message_ids']==[old['id']]
    assert future['id'] not in regenerated['snapshot_message_ids']
    new=s.get_by_id(regenerated['reply_id'])
    assert new['metadata']['visibility']==['claude']
    assert new['metadata']['replaces_message_id']==answer['id']
    restored=call(c,answer,'restore').json()
    assert restored['text']==answer['text'] and restored['metadata']['replaces_message_id']==new['id']
    assert call(c,restored,'regenerate').status_code==200
    for t in r.tasks.list(): r.tasks.wait(t['task_id'])
    loaded=MessageStore(str(s._path))
    assert loaded.get_by_id(old['id'])['text']=='original question'
    assert loaded.get_by_id(edit['id'])['metadata']['visibility']==['claude']
    r.rooms.update('general', name='renamed', archived=True)
    assert call(c,old,'restore').status_code==400
    r.rooms.update('general',archived=False)
    assert call(c,old,'versions').json()[0]['text']=='original question'
    # An active candidate cannot overwrite a version restored while it ran.
    latest=effective([m for m in s.get_since(-1) if m['sender']=='claude'])[-1]
    r.tasks.backends['mock'].run=lambda *args: (__import__('time').sleep(.1) or {'text':'late candidate','model':'mock'})
    pending=call(c,latest,'regenerate').json()
    assert call(c,answer,'restore').status_code==200
    assert r.tasks.wait(pending['task_id'])['state']=='failed'
    assert not any(m['text']=='late candidate' for m in s.get_since(-1))


def test_summary_invalidates_on_edit_and_private_taint(env):
    r,s,sums,c=env
    original=add(s,'public evidence')
    sums.revision('general',{'question':'old summary'},[original['id']],s.get_by_id,confirm=True)
    current=add(s,'continue')
    assert r.builder.snapshot('general','claude',current)['summary']
    assert PublicSummaries(sums,s).get('general')
    assert call(c,original,'edit',text='replacement evidence').status_code==200
    now=add(s,'continue again')
    assert r.builder.snapshot('general','claude',now)['summary'] is None
    assert PublicSummaries(sums,s).get('general') is None
    add(s,'private evidence',['claude'])
    public_question=add(s,'public follow-up')
    done=r.tasks.wait(r.tasks.start('general','claude',public_question)['task_id'])
    assert s.get_by_id(done['reply_id'])['metadata']['visibility']=='public'


def test_frozen_mcp_all_read_paths_no_private_exfiltration(env):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    r,s,sums,c=env
    own=add(s,'allowed needle',['claude'])
    denied=add(s,'hidden needle',['codex'])
    q=add(s,'question',['claude'])
    task=r.tasks.start('general','claude',q)
    r.tasks.wait(task['task_id'])
    add(s,'future needle')
    async def check():
        server=StdioServerParameters(command=sys.executable,args=[str((Path(__file__).parents[1] / 'src')/'chat_task_mcp.py'),str(r.tasks.directory/task['task_id']/'snapshot.json')])
        async with stdio_client(server) as (read,write):
            async with ClientSession(read,write) as client:
                await client.initialize()
                result=await client.call_tool('chat_search',{'query':'needle'})
                assert 'allowed needle' in result.content[0].text
                assert 'hidden needle' not in result.content[0].text and 'future needle' not in result.content[0].text
                result=await client.call_tool('chat_read',{'message_id':denied['id']})
                assert 'not found' in result.content[0].text
                result=await client.call_tool('chat_context',{})
                assert result.content[0].text=='null'
                result=await client.call_tool('research_search',{'query':'private attempted disclosure'})
                assert 'disabled for private context' in result.content[0].text
                result=await client.call_tool('research_read',{'url':'https://api.crossref.org/private'})
                assert 'disabled for private context' in result.content[0].text
    asyncio.run(check())




def test_public_summary_does_not_erase_authorized_private_history(env):
    r,s,sums,c=env
    private=add(s,'private early evidence',['claude'])
    public=add(s,'public later evidence')
    sums.revision('general',{'question':'public summary'},[public['id']],s.get_by_id,confirm=True)
    current=add(s,'continue',['claude'])
    prompt,audit=r.builder.build('general','claude',current)
    assert private['id'] in audit['message_ids'] and 'private early evidence' in prompt
    current=add(s,'public continue')
    prompt,audit=r.builder.build('general','codex',current)
    assert 'private early evidence' not in prompt


def test_private_public_private_research_cycle(env, monkeypatch):
    import runpy
    import research_tools
    r,s,sums,c=env
    private=add(s,'PRIVATE_CLAUDE_SENTINEL',['claude'])
    other=add(s,'OTHER_AUDIENCE_SENTINEL',['claude','codex'])
    denied=add(s,'CODEX_ONLY_SENTINEL',['codex'])
    requests=[]
    import chat_evidence
    monkeypatch.setattr(chat_evidence.Evidence,'scholar_search',lambda self,query,limit=6:
        (requests.append(query) or {'query':query,'sources':['mock'],'errors':[],'results':[{'title':'Mock scholarly result','doi':'mock'}]}))
    for scope in ('public',['claude'],'public'):
        current=add(s,'continue in selected scope',scope)
        done=r.tasks.wait(r.tasks.start('general','claude',current)['task_id'])
        assert done['state']=='completed'
        assert s.get_by_id(done['reply_id'])['metadata']['visibility']==scope
        directory=r.tasks.directory/done['task_id']
        snap=json.loads((directory/'snapshot.json').read_text('utf-8'))
        prompt=(directory/'input.json').read_text('utf-8')
        assert snap['research_allowed']==(scope=='public')
        assert ('PRIVATE_CLAUDE_SENTINEL' in prompt)==(scope!= 'public')
        assert 'OTHER_AUDIENCE_SENTINEL' not in prompt and 'CODEX_ONLY_SENTINEL' not in prompt
        monkeypatch.setattr(sys,'argv',['chat_task_mcp.py',str(directory/'snapshot.json')])
        tools=runpy.run_path(str((Path(__file__).parents[1] / 'src')/'chat_task_mcp.py'))
        read=tools['chat_read'](private['id']); search=tools['chat_search']('PRIVATE_CLAUDE_SENTINEL')
        assert ('PRIVATE_CLAUDE_SENTINEL' in read)==(scope!='public')
        assert ('PRIVATE_CLAUDE_SENTINEL' in search)==(scope!='public')
        assert 'not found' in tools['chat_read'](other['id'])
        before=len(requests)
        result=tools['research_search']('public scholarly query',1)
        assert len(requests)==before+(scope=='public')
        assert ('Mock scholarly result' in result)==(scope=='public')
    assert len(requests)==2
    # Explicitly narrowing a reference admits that one source, not its conversation.
    followup=add(s,'narrow explicit reference',['claude'],reply_to=other['id'])
    snap=r.builder.snapshot('general','claude',followup)
    assert other['id'] in [m['id'] for m in snap['history']]
    assert denied['id'] not in [m['id'] for m in snap['history']]
    assert not snap['research_allowed']
    with pytest.raises(ValueError): add(s,'widen private quote',['claude','codex'],reply_to=private['id'])
    # Normal public independent answers now also exclude all old private scopes.
    q=add(s,'independent public question')
    for model in ('claude','codex'):
        normal=r.builder.snapshot('general',model,q)
        strict=r.builder.snapshot('general',model,q,True)
        assert normal['history']==strict['history']
        assert normal['research_allowed'] and strict['research_allowed']
