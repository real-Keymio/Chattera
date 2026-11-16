import json
import pytest
from context_budget import policy,requested_limit
from chat_rooms import RoomCatalog
from test_visibility_chat import env,add

def test_default_preservation_and_roundtrip(tmp_path):
    path=tmp_path/'rooms.json'
    path.write_text(json.dumps({'a':{'id':'a','channel':'general','name':'old','archived':False,'owners':{}},'b':{'id':'b','channel':'explicit','name':'explicit','archived':False,'owners':{},'context_mode':'standard'}}))
    rooms=RoomCatalog(path,['general','explicit'])
    assert rooms.get('general')['context_mode']=='256k'
    assert rooms.get('explicit')['context_mode']=='standard'
    assert rooms.create('new')['context_mode']=='256k'
    for mode,custom in [('100k',None),('256k',None),('1m',None),('custom',345678)]:
        rooms.update('general',context_mode=mode,context_limit_tokens=custom)
        restored=RoomCatalog(path,[]).get('general')
        assert restored['context_mode']==mode and restored['context_limit_tokens']==custom
    for value in (None,0,-1,True,1.5,'100000',1000001):
        with pytest.raises(ValueError):rooms.update('general',context_mode='custom',context_limit_tokens=value)
    assert RoomCatalog(path,[]).get('general')['context_limit_tokens']==345678

def test_four_model_caps_and_sparse_input(env):
    cfg={'model':'m','max_output_tokens':1024,'context':{'model':'m','window_tokens':32768,'backend_reserve':4096,'status':'UNKNOWN'}}
    for mode in ('100k','256k','1m','custom'):
        p=policy(cfg,False,mode,777777)
        assert p['initial_budget_tokens']==27648 and p['window_status']=='UNKNOWN'
    for window,expected in [(1000000,631488),(1050000,681488)]:
        cli={'model':'m','context':{'model':'m','window_tokens':window}}
        assert policy(cli,True,'1m')['initial_budget_tokens']==expected
        assert policy(cli,True,'256k')['initial_budget_tokens']==262144
    r,s,_,_=env
    r.tasks.config['mock']={'model':'m','context':{'model':'m','window_tokens':1000000}}
    q=add(s,'brief question');t=r.tasks.wait(r.tasks.start('general','claude',q)['task_id'])
    assert t['state']=='completed' and t['context_policy']['target_tokens']==262144 and t['input_estimate']<3000

def test_passive_trigger_and_success_restart_no_repeat(env):
    import time
    from test_rolling_memory import setup
    from rolling_memory import RollingMemory
    r,s,_,_=env;m,f=setup(r)
    for i in range(12):add(s,'public evidence '*20)
    m.cfg.update(threshold=1500,retry_seconds=300)
    m.start()
    try:
        deadline=time.monotonic()+8
        while time.monotonic()<deadline and m.jobs.get('general',{}).get('state')!='completed':time.sleep(.05)
        assert m.jobs['general']['state']=='completed'
    finally:m.stop()
    recovered=RollingMemory(r,dict(m.cfg,retry_seconds=0))
    assert recovered.tick('general') is None and f.calls==1
