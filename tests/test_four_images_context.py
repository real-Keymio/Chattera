import json, hashlib
from pathlib import Path
import pytest
from test_visibility_chat import env,add,call
from test_r9_models_attachments import docs
from chat_identities import BUILTIN
MODELS=set(BUILTIN)
from context_budget import policy,check

def four(r):
    r.app.registry.is_agent_family=lambda n:n in MODELS
    for model in MODELS:r.tasks.switch('general',model,'mock')

def test_four_identity_private_independent_versions(env):
    r,s,_,client=env;four(r)
    private=add(s,'deepseek private',['deepseek'])
    gemini=add(s,'gemini private',['antigravity'])
    for model in MODELS:
        q=add(s,'continue',[model]);snap=r.builder.snapshot('general',model,q)
        ids=[m['id'] for m in snap['history']]
        assert (private['id'] in ids)==(model=='deepseek')
        assert (gemini['id'] in ids)==(model=='antigravity')
        from chat_tools import FrozenTools
        tools=FrozenTools(snap)
        hidden=gemini if model=='deepseek' else private
        assert hidden['text'] not in tools.call('chat_read',{'message_id':hidden['id']})
        assert hidden['text'] not in tools.call('chat_search',{'query':hidden['text']})
    shared_private=add(s,'pair history',['deepseek','antigravity'])
    q=add(s,'independent private',['deepseek','antigravity'])
    for model in ('deepseek','antigravity'):
        normal=r.builder.snapshot('general',model,q)
        strict=r.builder.snapshot('general',model,q,strict=True)
        assert shared_private['id'] in {m['id'] for m in normal['history']}
        assert not {private['id'],gemini['id']} & {m['id'] for m in normal['history']}
        assert shared_private['id'] not in {m['id'] for m in strict['history']}
    q=add(s,'independent')
    tasks=[r.tasks.start('general',model,q,independent_mode='strict') for model in MODELS]
    done=[r.tasks.wait(t['task_id']) for t in tasks]
    assert len({tuple(t['snapshot_message_ids']) for t in done})==1
    assert {s.get_by_id(t['reply_id'])['sender'] for t in done}==MODELS
    ds=next(t for t in done if t['identity']=='deepseek');reply=s.get_by_id(ds['reply_id'])
    share=call(client,reply,'share',confirm=True).json()
    assert share['metadata']['share_provenance']['original_author']=='deepseek'
    regenerated=call(client,reply,'regenerate');assert regenerated.status_code==200
    result=r.tasks.wait(regenerated.json()['task_id']);assert result['state']=='completed'
    assert s.get_by_id(result['reply_id'])['sender']=='deepseek'
    with pytest.raises(ValueError):r.tasks.switch('general','deepseek','legacy')

def test_token_budget_not_legacy_byte_ceiling_and_trim(env):
    r,s,_,_=env;four(r)
    r.rooms.update('general',context_mode='maximum')
    r.tasks.config['mock']={'model':'large','context':{'model':'large','window_tokens':1_000_000,'backend_reserve':32000,'status':'DOCUMENTED'}}
    for _ in range(12):add(s,'中文科研问题。'*9000)
    q=add(s,'continue')
    task=r.tasks.wait(r.tasks.start('general','deepseek',q)['task_id'])
    assert task['state']=='completed' and task['input_bytes']>72000
    assert task['input_estimate']+task['image_token_allowance']<=task['budget']
    assert task['trimmed_history_ranges'] and len(s.get_since(-1))==14
    assert task['budget_unit']=='estimated tokens'
    with pytest.raises(ValueError):policy({'model':'unknown','context':{'model':'large','window_tokens':1_000_000}},True)

def test_image_derivative_separate_budget_cache_and_regeneration(env,docs):
    from PIL import Image
    from chat_attachments import image_blocks
    r,s,_,_=env;four(r);r.builder.budget=72000
    path,att=docs('.png');im=Image.effect_noise((2400,1600),75).convert('RGB');im.save(path)
    original=path.read_bytes();q=s.add('user','image',attachments=[att],metadata={'visibility':['deepseek']})
    snap=r.builder.snapshot('general','deepseek',q)
    prompt,audit=r.builder.build('general','deepseek',q,snap)
    assert audit['image_transport_bytes']>72000 and audit['input_bytes']<72000
    record=snap['attachment_content'][f'{q["id"]}:0']
    cached=Path(record['image_path']);mtime=cached.stat().st_mtime_ns
    assert record['encoded_bytes']<=2*1024*1024 and cached!=path
    snap2=r.builder.snapshot('general','deepseek',q)
    assert cached.stat().st_mtime_ns==mtime and path.read_bytes()==original
    assert not r.builder.snapshot('general','codex',add(s,'public'))['attachment_content']
    before=image_blocks(snap,json.loads(prompt))
    Image.new('RGB',(20,20),'blue').save(path)
    assert image_blocks(snap,json.loads(prompt))==before  # frozen derivative, not new original
    cached.write_bytes(b'corrupt')
    with pytest.raises(ValueError):image_blocks(snap,json.loads(prompt))

def test_long_image_and_combined_count_limits(env,docs):
    from PIL import Image
    from chat_attachments import image_blocks
    r,s,_,_=env
    path,att=docs('.png');Image.new('RGB',(500,20000),'white').save(path)
    q=s.add('user','long',attachments=[att])
    with pytest.raises(ValueError):r.builder.snapshot('general','codex',q)
    path,att=docs('.png');Image.new('RGB',(3000,2000),'white').save(path)
    q=s.add('user','large',attachments=[att]);snap=r.builder.snapshot('general','codex',q)
    rec=snap['attachment_content'][f'{q["id"]}:0'];assert max(rec['prepared_size'])<=2560
    assert abs(rec['prepared_size'][0]/rec['prepared_size'][1]-1.5)<.01
    payload={'attachment_evidence':[dict(kind='image',message_id=q['id'],index=0)]*13}
    with pytest.raises(ValueError):image_blocks(snap,payload)

def test_connection_is_not_tied_to_one_identity(env):
    r,*_=env
    with pytest.raises(ValueError,match='requires a configured'):r.tasks.validate_switch('deepseek','legacy')
    with pytest.raises(ValueError,match='Unknown participant'):r.tasks.validate_switch('nobody','mock')
    r.tasks.validate_switch('deepseek','mock')
