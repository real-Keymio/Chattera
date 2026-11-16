import json
import socket
import subprocess
import sys
from types import SimpleNamespace

import pytest
from chat_rooms import ContextBuilder, RoomCatalog
from chat_backends import validate_cli, usage_fields, CLIBackend
from store import MessageStore
from summaries import SummaryStore


def test_chinese_budget_and_precise_omission_audit(tmp_path):
    store = MessageStore(str(tmp_path/'history.jsonl'))
    summaries = SummaryStore(str(tmp_path/'summaries.json'))
    rooms = RoomCatalog(tmp_path/'rooms.json',['a','b'])
    old = store.add('user','first evidence',channel='a')
    summaries.revision('a',{'question':'开放问题'},[old['id']],store.get_by_id,confirm=True)
    for _ in range(20):
        store.add('user','中'*1000,channel='a')
        store.add('user','foreign',channel='b')
    current = store.add('user','讨论仍保持开放',channel='a',reply_to=old['id'])
    small = ContextBuilder(store,summaries,rooms,24000)
    large = ContextBuilder(store,summaries,rooms,72000)
    p1,a1 = small.build('a','claude',current)
    p2,a2 = large.build('a','claude',current)
    assert len(p1.encode()) <= 24000 and len(p2.encode()) <= 72000
    assert len(a2['history_included_ids']) > len(a1['history_included_ids'])
    assert a1['trimmed_history_ranges'] and not a2['trimmed_history_ranges']
    assert a2['reference_ids'] == [old['id']] and a2['summary_version'] == 1
    trimmed = {i for lo,hi in a1['trimmed_history_ranges'] for i in range(lo,hi+1)}
    assert all(store.get_by_id(i)['channel'] == 'a' for i in trimmed)
    assert trimmed.isdisjoint(a1['message_ids'])
    assert a1['input_estimate'] != a1['input_bytes']
    with pytest.raises(ValueError): ContextBuilder(store,summaries,rooms,True)


def test_room_prompt_isolation_rename_priority_and_dedup(tmp_path):
    store = MessageStore(str(tmp_path/'history.jsonl'))
    summaries = SummaryStore(str(tmp_path/'summaries.json'))
    rooms = RoomCatalog(tmp_path/'rooms.json',['a','b','general'])
    fixed = rooms.get('a')['id']
    prompts = {'a':{'topic':'old-key'},fixed:{'topic':'A-only','language':'中文','background':'A background','rules':['shared','local','local']},
               'b':{'topic':'B-only'},'general':{'topic':'GENERAL-NO-FALLBACK'}}
    builder = ContextBuilder(store,summaries,rooms,room_prompts=prompts,rules=SimpleNamespace(active_list=lambda:{'rules':['shared']}))
    current = store.add('user','question',channel='a')
    rooms.update(fixed,name='renamed')
    prompt,audit = builder.build('a','claude',current)
    data=json.loads(prompt)
    assert data['room_context']['rules'] == ['local']
    assert data['global_rules']['rules'] == ['shared']
    assert audit['room_prompt_source'] == fixed
    assert 'B-only' not in prompt and 'old-key' not in prompt
    new=rooms.create('empty')
    current=store.add('user','question',channel=new['channel'])
    assert json.loads(builder.build(new['channel'],'codex',current)[0])['room_context'] == {}


def test_cache_fields_do_not_invent_missing_values():
    claude=usage_fields('claude',{'input_tokens':3,'cache_creation_input_tokens':12,'cache_read_input_tokens':34,'output_tokens':0})
    assert claude['cache_creation_input_tokens']==12 and claude['cache_read_input_tokens']==34
    codex=usage_fields('codex',{'input_tokens':50,'cached_input_tokens':34,'cache_write_input_tokens':0})
    assert codex['input_tokens']==50 and codex['cache_creation_input_tokens']==0
    assert usage_fields('codex',{})['cache_read_input_tokens']=='UNKNOWN'
    assert claude['subscription_debit']=='UNKNOWN' and codex['billed_amount']=='UNKNOWN'


def test_cli_path_failure_and_bounded_version_check(tmp_path,monkeypatch):
    with pytest.raises(ValueError,match='no automatic installation'):
        validate_cli('claude',{'executable':str(tmp_path/'missing.exe')})
    seen=[]
    def run(command,**kwargs):
        seen.append((command,kwargs))
        return subprocess.CompletedProcess(command,0,b'fixture 1.0',b'')
    monkeypatch.setattr(subprocess,'run',run)
    assert validate_cli('claude',{'executable':sys.executable})['version']=='fixture 1.0'
    assert seen[0][0][-1]=='--version' and seen[0][1]['timeout']==10
    monkeypatch.setattr(subprocess,'run',lambda *a,**k: subprocess.CompletedProcess(a,1,b'',b''))
    with pytest.raises(ValueError,match='not runnable'):
        validate_cli('claude',{'executable':sys.executable})
    def timeout(*a,**k):
        raise subprocess.TimeoutExpired('version check',10)
    monkeypatch.setattr(subprocess,'run',timeout)
    with pytest.raises(ValueError,match='TimeoutExpired'):
        validate_cli('claude',{'executable':sys.executable})


def test_network_timeout_is_distinguished(monkeypatch):
    import research_tools as r
    class Connection:
        def __init__(self,*a,**k): pass
        def request(self,*a,**k): raise socket.timeout('fixture')
        def close(self): pass
    monkeypatch.setattr(r,'_public_ip',lambda host:'1.1.1.1')
    monkeypatch.setattr(r.http.client,'HTTPSConnection',Connection)
    with pytest.raises(TimeoutError,match='network timeout at connect/request'):
        r._fetch('https://api.crossref.org/works',{'application/json'})
