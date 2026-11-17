import asyncio
import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path

import pytest
from test_visibility_chat import env,add
from test_r9_models_attachments import docs
from chat_api import APIBackend
from chat_secrets import remember,redact,get_key
from api_protocols import headers


@pytest.fixture(params=['openai-chat','anthropic-messages','google-generate-content'])
def provider(request):
    protocol=request.param
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def reply(self,value,status=200):
            self.send_response(status);self.send_header('Content-Type','application/json');self.end_headers()
            try:self.wfile.write(json.dumps(value).encode())
            except (OSError,ConnectionResetError):pass
        def do_GET(self):
            self.server.gets.append(self.path)
            if protocol=='google-generate-content':self.reply({'models':[{'name':'models/manual-model','supportedGenerationMethods':['generateContent']}]})
            else:self.reply({'data':[{'id':'manual-model','capabilities':{'image_input':{'supported':True},'effort':{'low':{'supported':True},'high':{'supported':False}}}}],'has_more':False})
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])));self.server.requests.append((self.path,body))
            if self.server.delay:time.sleep(self.server.delay)
            if self.server.fail:return self.reply({'error':self.server.echo},400)
            messages=body.get('messages',body.get('contents'))
            called=('tool_result' in json.dumps(messages) if protocol=='anthropic-messages' else 'functionResponse' in json.dumps(messages) if protocol=='google-generate-content' else messages[-1]['role']=='tool')
            call=self.server.call if not called else None
            text=self.server.echo or 'final answer'
            if protocol=='openai-chat':
                msg={'content':text}
                if call:msg={'content':None,'tool_calls':[{'id':'t1','type':'function','function':{'name':call[0],'arguments':json.dumps(call[1])}}]}
                self.reply({'model':'actual-openai','choices':[{'message':msg,'finish_reason':'tool_calls' if call else 'stop'}],'usage':{'prompt_tokens':7,'completion_tokens':4}})
            elif protocol=='anthropic-messages':
                blocks=[{'type':'thinking','thinking':'hidden reasoning','signature':'opaque-signed-block'},{'type':'redacted_thinking','data':'opaque-redacted'}]
                blocks.append({'type':'tool_use','id':'t1','name':call[0],'input':call[1]} if call else {'type':'text','text':text})
                self.reply({'model':'actual-anthropic','content':blocks,'stop_reason':'tool_use' if call else 'end_turn','usage':{'input_tokens':7,'output_tokens':4,'cache_read_input_tokens':3}})
            else:
                parts=[{'text':'hidden reasoning','thought':True}]
                parts.append({'functionCall':{'name':call[0],'args':call[1],'id':'t1'},'thoughtSignature':'opaque-signed-part'} if call else {'text':text,'thoughtSignature':'opaque-final-signature'})
                self.reply({'modelVersion':'actual-google','candidates':[{'content':{'role':'model','parts':parts},'finishReason':'STOP'}],'usageMetadata':{'promptTokenCount':7,'candidatesTokenCount':4,'thoughtsTokenCount':2}})
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.protocol=protocol
    server.requests=[];server.gets=[];server.call=None;server.delay=0;server.fail=False;server.echo=''
    threading.Thread(target=server.serve_forever,daemon=True).start()
    yield server
    server.shutdown();server.server_close()


def connect(r,p,**extra):
    cfg=dict(type='api',protocol=p.protocol,identity='antigravity',enabled=True,mock=True,
             base_url=f'http://127.0.0.1:{p.server_port}/v1',model='manual-model',tools=True,images=True,
             max_requests=30,max_output_tokens=4096,timeout=3,**extra)
    r.tasks.config['native_api']=cfg;r.tasks.backends['native_api']=APIBackend('native_api',cfg,r.tasks.directory)
    r.tasks.switch('general','antigravity','native_api');return cfg


def test_native_wires_tools_thinking_attachments_and_privacy(env,provider,docs):
    from PIL import Image
    r,s,_,_=env;cfg=connect(r,provider);r.builder.budget=72000
    if provider.protocol=='anthropic-messages':setting={'effort':'high','thinking':{'type':'adaptive'}}
    elif provider.protocol=='google-generate-content':setting={'thinking':{'thinkingBudget':1024,'includeThoughts':True}}
    else:
        r.tasks.backends['native_api'].config['reasoning_parameter']='reasoning_effort'
        r.tasks.config['native_api']['reasoning_parameter']='reasoning_effort';setting={'effort':'high'}
    r.tasks.switch('general','antigravity','native_api',dict(model='room-model',**setting))
    _,text=docs('.txt',b'DOCUMENT_EVIDENCE')
    path,img=docs('.png');Image.new('RGB',(8,8),'red').save(path)
    foreign=add(s,'OTHER_PRIVATE',['claude'])
    q=s.add('user','read document',attachments=[text,img],metadata={'visibility':['antigravity']})
    provider.call=('attachment_read',{'message_id':q['id'],'index':0})
    done=r.tasks.wait(r.tasks.start('general','antigravity',q)['task_id'])
    assert done['state']=='completed',done.get('reason')
    assert s.get_by_id(done['reply_id'])['text']=='final answer'
    assert done['usage']=={'prompt_tokens':14,'completion_tokens':8}
    assert done['requested_model']=='room-model' and done['actual_model'].startswith('actual-')
    raw=json.dumps(provider.requests)
    assert 'DOCUMENT_EVIDENCE' in raw and 'OTHER_PRIVATE' not in raw
    assert 'research_search' not in raw
    if provider.protocol=='anthropic-messages':
        assert provider.requests[0][0]=='/v1/messages'
        assert provider.requests[1][1]['messages'][1]['content'][0]['signature']=='opaque-signed-block'
        assert 'base64' in raw and 'tool_result' in raw and 'output_config' in raw
    elif provider.protocol=='google-generate-content':
        assert provider.requests[0][0]=='/v1/models/room-model:generateContent'
        assert provider.requests[1][1]['contents'][1]['parts'][1]['thoughtSignature']=='opaque-signed-part'
        assert 'inlineData' in raw and 'functionResponse' in raw and 'thinkingConfig' in raw
    else:assert 'image_url' in raw and 'reasoning_effort' in raw
    # Sensitive opaque reasoning never goes to chat, audit, or output artifacts.
    files=''.join(f.read_text('utf-8',errors='ignore') for f in r.tasks.directory.rglob('*.json*'))
    assert 'hidden reasoning' not in files and 'opaque-signed' not in files
    public=add(s,'strict public question')
    provider.call=None
    done=r.tasks.wait(r.tasks.start('general','antigravity',public,independent_mode='strict')['task_id'])
    assert done['state']=='completed' and q['id'] not in done['snapshot_message_ids']
    assert s.get_by_id(done['reply_id'])['metadata']['visibility']==['antigravity']


def test_discovery_room_override_cancel_timeout_and_redaction(env,provider,monkeypatch):
    r,s,_,_=env;cfg=connect(r,provider)
    result=asyncio.run(r.tasks.backends['native_api'].list_models())
    assert result['status']=='VERIFIED' and result['models'][0]['generation_verified']=='UNKNOWN'
    if provider.protocol=='anthropic-messages':
        assert result['models'][0]['reported_capabilities']['effort_values']==['low']
        assert result['models'][0]['reported_capabilities']['images'] is True
    else:assert result['models'][0]['reported_capabilities']['effort_values']=='UNKNOWN'
    r.tasks.switch('general','antigravity','native_api',{'model':'room-default'})
    r.tasks.switch('other','antigravity','native_api',{'model':'other-default'})
    q=add(s,'once')
    task=r.tasks.start('general','antigravity',q,settings_override={'model':'one-shot'})
    done=r.tasks.wait(task['task_id']);assert done['state']=='completed' and done['requested_model']=='one-shot'
    assert r.tasks.effective_config('general','antigravity')['model']=='room-default'
    assert r.tasks.effective_config('other','antigravity')['model']=='other-default'
    assert cfg['model']=='manual-model'
    from chat_rooms import RoomCatalog
    restored=RoomCatalog(r.rooms.path,[])
    assert restored.get('general')['model_settings']['antigravity']['model']=='room-default'
    # Dynamic, synthetic sentinel: never an actual supplied credential.
    secret=remember('synthetic-'+uuid.uuid4().hex)
    provider.echo=secret
    done=r.tasks.wait(r.tasks.start('general','antigravity',q)['task_id'])
    assert s.get_by_id(done['reply_id'])['text']=='[REDACTED]'
    provider.fail=True
    provider.echo='Unsupported parameter thinking '+secret
    done=r.tasks.wait(r.tasks.start('general','antigravity',q)['task_id'])
    assert done['state']=='failed' and secret not in done['reason'] and 'provider-referenced parameters: thinking' in done['reason']
    assert secret not in s._path.read_text('utf-8')+r.tasks.log.read_text('utf-8')
    provider.fail=False;provider.delay=.4;provider.echo=''
    task=r.tasks.start('general','antigravity',q);r.tasks.cancel('general','antigravity')
    assert r.tasks.wait(task['task_id'])['state']=='cancelled'
    provider.delay=1.3;r.tasks.backends['native_api'].config['timeout']=1
    done=r.tasks.wait(r.tasks.start('general','antigravity',q)['task_id'])
    assert done['state']=='failed' and ('deadline' in done['reason'] or 'timeout' in done['reason'])  # reply limit, or the connection's own per-request timeout


def test_private_key_file_and_auth_headers(tmp_path):
    directory=tmp_path/'.multichat-private';directory.mkdir();path=directory/'profile.key'
    value='synthetic-'+uuid.uuid4().hex;path.write_text(value)
    assert get_key({'key_file':str(path)})==value
    for p,name in [('openai-chat','Authorization'),('anthropic-messages','x-api-key'),('google-generate-content','x-goog-api-key')]:
        h=headers({'type':'api','protocol':p},value)
        assert value in h[name] and value not in json.dumps(redact(h))
    assert headers({'type':'api','protocol':'anthropic-messages','anthropic_auth':'bearer'},value)['Authorization']=='Bearer '+value
    with pytest.raises(ValueError):get_key({'key_file':str(tmp_path/'auth.json')})


def test_cli_overrides_are_task_local(env):
    from chat_backends import CLIBackend
    r,s,_,_=env
    for name in ('claude','codex'):
        cfg={'model':'original-model','effort':'medium','executable':'not-executed'}
        r.tasks.config[name]=cfg
        # Configuration merge is independent of process launch; no real calls here.
        r.tasks.switch('general',name,'mock')
        settings=r.tasks.effective_config('general',name,{'model':'alternate-model','effort':'high'})
        runner=CLIBackend(name,dict(cfg,**settings))
        args=runner.command(r.tasks.directory)
        assert 'alternate-model' in args
        assert ('high' in args if name=='claude' else 'model_reasoning_effort="high"' in args)
        assert cfg['model']=='original-model' and cfg['effort']=='medium'


def test_cli_capability_probe_strips_explicit_api_environment(monkeypatch):
    import sys
    from types import SimpleNamespace
    from model_discovery import cli_discovery
    monkeypatch.setenv('CUSTOM_PROVIDER_CREDENTIAL','synthetic-value-only')
    calls=[]
    def run(args,**kwargs):
        assert 'CUSTOM_PROVIDER_CREDENTIAL' not in kwargs['env']
        calls.append(args)
        return SimpleNamespace(returncode=0,stdout=b'--model --effort (low, medium, high, xhigh, max)')
    monkeypatch.setattr('subprocess.run',run)
    result=cli_discovery('claude',{'executable':sys.executable,'secret_env_names':['CUSTOM_PROVIDER_CREDENTIAL']})
    assert result['status']=='VERIFIED' and len(calls)==2


def test_effective_capabilities_checked_before_append(env,provider,docs):
    from PIL import Image
    r,s,_,_=env;connect(r,provider)
    r.tasks.backends['native_api'].config['model_capabilities']={'text-only':{'images':False}}
    path,img=docs('.png');Image.new('RGB',(8,8),'red').save(path)
    event=dict(type='message',text='read',channel='general',targets=['antigravity'],attachments=[img],
               model_overrides={'antigravity':{'model':'text-only'}})
    before=len(s.get_since(-1))
    with pytest.raises(ValueError,match='image input'):asyncio.run(r.websocket(event))
    with pytest.raises(ValueError,match='image input'):
        s.add('user','read',attachments=[img],metadata={'targets':['antigravity'],'model_overrides':event['model_overrides']})
    assert len(s.get_since(-1))==before and not provider.requests
    with pytest.raises(ValueError,match='explicit reply targets'):
        s.add('user','read',metadata={'targets':['claude'],'model_overrides':{'antigravity':{'model':'oops'}}})


def test_settings_validation_and_owner_transfer(env,provider):
    from model_settings import merge
    r,s,_,_=env;connect(r,provider)
    provider.delay=.5
    q=add(s,'pending')
    task=r.tasks.start('general','antigravity',q)
    with pytest.raises(ValueError):r.tasks.switch('general','antigravity','native_api',{'model':'invalid model'})
    assert r.tasks.tasks[task['task_id']]['state']=='running'
    r.tasks.switch('general','antigravity','native_api',{'model':'new-model'})
    assert r.tasks.wait(task['task_id'])['state']=='cancelled'
    assert not any((m.get('metadata') or {}).get('task_id')==task['task_id'] for m in s.get_since(-1))
    if provider.protocol=='google-generate-content':
        cfg=r.tasks.backends['native_api'].config
        assert merge(cfg,{}, {'effort':'HIGH'},'native_api')['effort']=='HIGH'
        with pytest.raises(ValueError):merge(cfg,{}, {'effort':'high'},'native_api')
        with pytest.raises(ValueError):merge(cfg,{'thinking':{'thinkingBudget':1024}},{'effort':'HIGH'},'native_api')
    r.tasks.switch('general','antigravity','legacy')
    with pytest.raises(ValueError,match='Legacy backend'):r.tasks.effective_config('general','antigravity',{'model':'manual'})


def test_logging_redacts_values_headers_and_tracebacks(tmp_path):
    import io,logging,subprocess
    from chat_secrets import RedactingFilter
    secret=remember('synthetic-'+uuid.uuid4().hex)
    stream=io.StringIO();handler=logging.StreamHandler(stream);handler.addFilter(RedactingFilter())
    logger=logging.getLogger('multichat_secret_test');logger.addHandler(handler);logger.setLevel(logging.INFO);logger.propagate=False
    try:
        logger.info('request %s',{'Authorization':'Bearer '+secret})
        try:raise ValueError(secret)
        except ValueError:logger.exception('sanitized error')
        assert secret not in stream.getvalue() and '[REDACTED]' in stream.getvalue()
        assert redact({'authorization':secret,'arbitrary':secret})=={'authorization':'[REDACTED]','arbitrary':'[REDACTED]'}
        check=subprocess.run(['git','check-ignore','--stdin'],input='.multichat-private/profile.key\n',text=True,capture_output=True)
        assert check.returncode==0
    finally:logger.removeHandler(handler)


def test_disabled_profiles_do_not_discover_or_call(tmp_path,monkeypatch):
    monkeypatch.delenv('MULTICHAT_MISSING_TEST_KEY',raising=False)
    for protocol in ('openai-chat','anthropic-messages','google-generate-content'):
        backend=APIBackend('disabled_api',dict(type='api',identity='antigravity',protocol=protocol,
            enabled=False,base_url='https://example.invalid/v1',model='manual',key_env='MULTICHAT_MISSING_TEST_KEY'),tmp_path)
        assert not backend.available()
        assert asyncio.run(backend.list_models())['status']=='BLOCKED'
        with pytest.raises(ValueError):backend.run('',{},None,threading.Event())


def test_private_key_writer_no_echo_or_overwrite(tmp_path):
    import subprocess,sys
    value='synthetic-'+uuid.uuid4().hex
    root=tmp_path/'.multichat-private'
    command=[sys.executable,str(Path(__file__).parents[1]/'scripts/set_api_key.py'),'--directory',str(root),'--profile','test_profile','--stdin']
    response=subprocess.run(command,input=value,text=True,capture_output=True)
    assert response.returncode==0 and value not in response.stdout+response.stderr
    assert (root/'test_profile.key').read_text('utf-8')==value
    again=subprocess.run(command,input='different-synthetic-key',text=True,capture_output=True)
    assert again.returncode!=0 and (root/'test_profile.key').read_text('utf-8')==value


def test_edit_restore_and_regenerate_preserve_invocation_settings(env,provider):
    r,s,_,client=env;connect(r,provider)
    q=s.add('user','original question',metadata={'visibility':['antigravity'],'targets':['antigravity'],
        'model_overrides':{'antigravity':{'model':'one-shot'}}})
    done=r.tasks.wait(r.tasks.start('general','antigravity',q)['task_id'])
    assert done['state']=='completed'
    edited=client.post(f'/api/chat/message/{q["id"]}/edit',json={'text':'edited question'})
    assert edited.status_code==200 and not edited.json()['metadata'].get('model_overrides')
    assert client.post(f'/api/chat/message/{q["id"]}/restore',json={}).status_code==200
    r.tasks.switch('general','antigravity','native_api',{'model':'changed-room-default'})
    response=client.post(f'/api/chat/message/{done["reply_id"]}/regenerate',json={})
    assert response.status_code==200,response.text
    regenerated=r.tasks.wait(response.json()['task_id'])
    assert regenerated['state']=='completed' and regenerated['requested_model']=='one-shot'
    assert regenerated['visibility']==['antigravity']
    assert regenerated['input_sha256']==done['input_sha256']
