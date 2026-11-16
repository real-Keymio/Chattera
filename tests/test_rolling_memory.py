import json,time,threading
import pytest
from test_visibility_chat import env,add
from rolling_memory import RollingMemory
from summaries import PUBLIC_SECTIONS
from context_budget import policy

class MemoryMock:
    def __init__(self):self.release=threading.Event();self.release.set();self.calls=0
    def run(self,prompt,task,attach,cancel):
        self.calls+=1;self.release.wait(3)
        value=json.loads(prompt);rows=value['evidence']
        sections={key:'' for key in PUBLIC_SECTIONS}
        sections['positions']='; '.join(f"{m['sender']} #{m['id']}: {m['text'][:30]}" for m in rows[:8])
        sections['disagreements']=f"Not resolved #{rows[0]['id']}"
        return dict(text=json.dumps(sections),usage={'input_tokens':5},model='mock-memory')

def setup(r):
    fake=MemoryMock();r.tasks.backends['memory_mock']=fake
    m=RollingMemory(r,dict(backend='memory_mock',threshold=200,keep_recent=3,retry_seconds=0))
    return m,fake

def done(m):
    job=m.jobs['general'];job['_thread'].join(5);return job

def test_rolling_public_snapshot_and_shared_memory(env):
    r,s,_,_=env;m,f=setup(r)
    for i in range(250):add(s,('supports' if i%2 else 'opposes')+' theory '+str(i))
    secret=add(s,'PRIVATE SECRET',['claude'])
    f.release.clear();job=m.tick('general');assert job and m.tick('general') is None
    newest=add(s,'late public')
    assert secret['id'] not in job['source_ids'] and newest['id'] not in job['source_ids']
    f.release.set();assert done(m)['state']=='completed';assert f.calls==1
    summary=r.app.summaries.active('general');assert summary['status']=='automatic' and not summary['human_confirmed']
    q=add(s,'continue')
    for identity in ('claude','codex','antigravity','deepseek'):
        snapshot=r.builder.snapshot('general',identity,q)
        assert snapshot['summary']['version']==summary['version']
        assert 'HUMAN-UNCONFIRMED' in snapshot['summary']['content_semantics']
    assert len(s.get_since(-1))==253

def test_manual_fence_cancel_restart(env):
    r,s,sums,_=env;m,f=setup(r)
    rows=[add(s,'long evidence '*10) for _ in range(10)]
    f.release.clear();m.tick('general')
    sums.revision('general',{'question':'manual'},[rows[0]['id']],s.get_by_id,confirm=True)
    f.release.set();assert done(m)['state']=='failed';assert sums.active('general')['author']=='user'
    m.jobs['general']['updated_at']=0;f.release.clear();m.tick('general');m.cancel('general');f.release.set()
    assert done(m)['state']=='cancelled'
    m.enable('general',False);assert RollingMemory(r).disabled==['general']
    assert m.tick('general') is None

def test_strategies_and_long_excerpt(env):
    cfg={'model':'m','max_output_tokens':1024,'context':{'model':'m','window_tokens':32768,'backend_reserve':4096}}
    assert [policy(cfg,False,x)['initial_budget_tokens'] for x in ('standard','long','maximum')]==[20000,27648,27648]
    r,s,_,_=env
    huge=add(s,'x'*40000);q=add(s,'current')
    snapshot=r.builder.snapshot('general','claude',q);snapshot['context_policy']=policy(cfg,mode='standard')
    prompt,audit=r.builder.build('general','claude',q,snapshot)
    item=json.loads(prompt)['history'][0]
    assert item['id']==huge['id'] and item['excerpt']['total_characters']==40000
    assert len(s.get_by_id(huge['id'])['text'])==40000

def test_recovery_source_validation_and_retry_cap(env):
    from chat_rooms import append_event
    r,s,sums,_=env;m,f=setup(r)
    rows=[add(s,'evidence '*40) for _ in range(12)]
    bad={key:'' for key in PUBLIC_SECTIONS};bad['positions']='claim #999999'
    with pytest.raises(ValueError):sums.automatic('general',bad,[0],s.get_by_id,0,{'0':'bad'})
    f.run=lambda *a:(_ for _ in ()).throw(TimeoutError('test timeout'))
    m.tick('general');assert done(m)['state']=='failed'
    m.tick('general');assert done(m)['state']=='failed'
    assert m.tick('general') is None and not sums.active('general')
    frozen=m.public(m.jobs['general']);frozen['state']='running';append_event(m.log,frozen)
    restored=RollingMemory(r)
    assert restored.jobs['general']['state']=='failed' and 'restart' in restored.jobs['general']['reason']
