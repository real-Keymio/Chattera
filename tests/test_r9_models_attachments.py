"""Local HTTP mock and generated document fixtures; never external API traffic."""
import asyncio
import json
import os
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from test_visibility_chat import env, add, call
from chat_api import APIBackend
from chat_tools import FrozenTools
from chat_visibility import audience


@pytest.fixture
def provider():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            self.server.requests.append(body)
            if self.server.delay: time.sleep(self.server.delay)
            if self.server.fail:
                self.send_response(503); self.end_headers(); return
            msg={'content':'API reply from authorized input'}
            if self.server.tool and body['messages'][-1]['role']!='tool':
                msg={'content':None,'tool_calls':[{'id':'tool-1','type':'function','function':self.server.tool}]}
            elif body['messages'][-1]['role']=='tool': msg['content']=body['messages'][-1]['content']
            data=json.dumps({'model':'provider-confirmed-model','choices':[{'message':msg,'finish_reason':'tool_calls' if msg.get('tool_calls') else 'stop'}],
                             'usage':{'prompt_tokens':12,'completion_tokens':6,'prompt_tokens_details':{'cached_tokens':4}}}).encode()
            self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers()
            try:self.wfile.write(data)
            except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    server.requests=[];server.delay=0;server.fail=False;server.tool=None
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    yield server
    server.shutdown();server.server_close()


def connect(r,provider,**kw):
    cfg=dict(type='openai-compatible',identity='antigravity',enabled=True,mock=True,
             base_url=f'http://127.0.0.1:{provider.server_port}/v1',model='mock-provider-model',
             timeout=5,max_requests=30,max_output_tokens=256,tools=True,images=True,**kw)
    r.tasks.config['gemini_api']=cfg
    r.tasks.backends['gemini_api']=APIBackend('gemini_api',cfg,r.tasks.directory)
    r.tasks.switch('general','antigravity','gemini_api')
    return cfg


def test_api_private_public_independent_tools_and_ledger(env,provider):
    r,s,_,_=env;cfg=connect(r,provider)
    private=add(s,'GEMINI_PRIVATE',['antigravity'])
    done=r.tasks.wait(r.tasks.start('general','antigravity',private)['task_id'])
    assert done['state']=='completed' and audience(s.get_by_id(done['reply_id']))==['antigravity']
    other=add(s,'CLAUDE_PRIVATE',['claude'])
    q=add(s,'public question')
    provider.tool={'name':'chat_search','arguments':json.dumps({'query':'PRIVATE'})}
    done=r.tasks.wait(r.tasks.start('general','antigravity',q)['task_id'])
    assert done['state']=='completed' and s.get_by_id(done['reply_id'])['text']=='[]'
    assert audience(s.get_by_id(done['reply_id']))=='public'
    assert done['usage']=={'prompt_tokens':24,'completion_tokens':12}
    assert done['actual_model']=='provider-confirmed-model' and done['last_response_usage']['prompt_tokens_details']['cached_tokens']==4
    provider.tool=None
    q=add(s,'same public input')
    for model in ('claude','antigravity'):
        t=r.tasks.wait(r.tasks.start('general',model,q,independent_mode='strict')['task_id'])
        assert t['state']=='completed' and audience(s.get_by_id(t['reply_id']))==[model]
        snap=json.loads((Path(r.tasks.tasks[t['task_id']]['directory'])/'snapshot.json').read_text())
        assert private['id'] not in t['snapshot_message_ids'] and other['id'] not in t['snapshot_message_ids']
        assert not any(m['id']>q['id'] for m in snap['history'])
    restored=APIBackend('gemini_api',cfg,r.tasks.directory)
    assert restored.usage['requests']==4


def test_api_cancel_switch_timeout_failure_and_cap(env,provider):
    r,s,_,_=env;cfg=connect(r,provider);provider.delay=.6
    q=add(s,'cancel')
    task=r.tasks.start('general','antigravity',q)
    with pytest.raises(ValueError):r.tasks.start('general','antigravity',q)
    deadline=time.time()+3
    while not provider.requests and time.time()<deadline:time.sleep(.01)
    r.tasks.switch('general','antigravity','legacy')
    assert r.tasks.wait(task['task_id'])['state']=='cancelled'
    assert not any(m.get('reply_to')==q['id'] for m in s.get_since(-1))
    r.tasks.switch('general','antigravity','gemini_api')
    provider.delay=1.2;r.tasks.backends['gemini_api'].config['timeout']=1
    done=r.tasks.wait(r.tasks.start('general','antigravity',q)['task_id'])
    assert done['state']=='failed' and 'deadline' in done['reason']
    provider.delay=0;provider.fail=True
    done=r.tasks.wait(r.tasks.start('general','antigravity',q)['task_id'])
    assert done['state']=='failed' and '503' in done['reason']
    provider.fail=False;r.tasks.backends['gemini_api'].config['max_requests']=3
    done=r.tasks.wait(r.tasks.start('general','antigravity',q)['task_id'])
    assert done['state']=='failed' and 'cap reached' in done['reason']
    assert len(provider.requests)==3


@pytest.fixture
def docs(env):
    r,s,_,_=env
    dependency=os.environ.get('MULTICHAT_TEST_PARSER_DEPS','')
    if dependency:
        import sys
        sys.path.insert(0,dependency)
    pytest.importorskip('pypdf');pytest.importorskip('docx');pytest.importorskip('PIL')
    r.attachments.dependencies=dependency
    r.attachments.upload.mkdir(parents=True,exist_ok=True)
    def write(ext,data=None):
        path=r.attachments.upload/(uuid.uuid4().hex+ext)
        if data is not None:path.write_bytes(data)
        return path,{'name':'research'+ext,'url':'/uploads/'+path.name,'kind':'image' if ext in ('.png','.jpg','.webp') else 'file'}
    return write


def pdf(path):
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject,NameObject,DecodedStreamObject
    writer=PdfWriter()
    for number in range(1,4):
        page=writer.add_blank_page(width=300,height=300)
        font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
        page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
        content=DecodedStreamObject();content.set_data(f'BT /F1 12 Tf 20 250 Td (PAGE_{number}_EVIDENCE) Tj ET'.encode())
        page[NameObject('/Contents')]=writer._add_object(content)
    writer.write(path)


def test_pdf_docx_text_cache_permissions_and_api_consumption(env,provider,docs):
    from docx import Document
    r,s,_,c=env;connect(r,provider)
    r.builder.budget=72000
    path,att=docs('.pdf');pdf(path)
    q=s.add('user','read page two',attachments=[att],metadata={'visibility':['antigravity'],'targets':[]})
    provider.tool={'name':'attachment_read','arguments':json.dumps({'message_id':q['id'],'index':0,'unit':2})}
    done=r.tasks.wait(r.tasks.start('general','antigravity',q)['task_id'])
    assert done['state']=='completed'
    reply=s.get_by_id(done['reply_id']);assert 'PAGE_2_EVIDENCE' in reply['text'] and '"unit": 2' in reply['text']
    assert done['attachment_sources'][0]['message_id']==q['id']
    snap=r.builder.snapshot('general','antigravity',q)
    assert FrozenTools(snap).call('research_search',{'query':'private'})=='Tool unavailable: ValueError'
    public=add(s,'public now');other=r.builder.snapshot('general','claude',public)
    assert 'PAGE_2_EVIDENCE' not in json.dumps(other)
    assert 'unavailable' in FrozenTools(other).call('attachment_read',{'message_id':q['id'],'index':0})
    for channel,scope in [('general','public'),('other',['antigravity'])]:
        with pytest.raises(ValueError):s.add('user','rebind',attachments=[att],channel=channel,metadata={'visibility':scope})
    # Editing retains attachments; sharing publishes reviewed text only.
    edit=call(c,q,'edit',text='edited request').json();assert edit['attachments']==[att]
    share=call(c,q,'share',confirm=True).json();assert not share.get('attachments')
    before=list(r.attachments.cache.rglob('*.json'))[0].stat().st_mtime_ns
    assert r.attachments.extract('general',att)['units'][1]['text']=='PAGE_2_EVIDENCE'
    assert list(r.attachments.cache.rglob('*.json'))[0].stat().st_mtime_ns==before
    p,a=docs('.docx');d=Document();d.add_paragraph('DOCX_EVIDENCE');d.save(p)
    assert r.attachments.extract('general',a)['units'][0]['text']=='DOCX_EVIDENCE'
    for ext in ('.txt','.md'):
        p,a=docs(ext,('LONG_EVIDENCE\n'*20000).encode())
        q=s.add('user','read excerpt',attachments=[a])
        prompt,audit=r.builder.build('general','claude',q)
        assert 'LONG_EVIDENCE' in prompt and audit['input_bytes']<=72000
        # Attached documents may use up to about half of the input budget; the rest stays for history.
        assert len(prompt.encode())<=72000*.55


def test_image_wire_input_capability_budget_and_path(env,provider,docs):
    from PIL import Image
    r,s,_,_=env;connect(r,provider);r.builder.budget=72000
    for ext,fmt in [('.png','PNG'),('.jpg','JPEG'),('.webp','WEBP')]:
        p,a=docs(ext);Image.new('RGB',(8,8),(255,0,0)).save(p,fmt)
        q=s.add('user','describe',attachments=[a])
        done=r.tasks.wait(r.tasks.start('general','antigravity',q)['task_id']);assert done['state']=='completed'
        assert provider.requests[-1]['messages'][1]['content'][1]['type']=='image_url'
        assert done['image_transport_bytes']>0 and done['input_bytes']<=72000
        with pytest.raises(ValueError,match='image input'):r.tasks.start('general','claude',q)
    for url in ('/uploads/../secret.txt','https://evil/x.pdf','/uploads/a.pdf'):
        with pytest.raises(ValueError):r.attachments.path({'url':url})
    p,a=docs('.txt',b'x'*(50*1024*1024+1))
    with pytest.raises(ValueError):r.attachments.path(a)
    p,a=docs('.pdf',b'not PDF')
    with pytest.raises(ValueError):r.attachments.extract('general',a)


def test_api_configuration_does_not_discover_keys(env,provider,monkeypatch):
    r,*_=env;cfg=connect(r,provider)
    cfg.update(mock=False,base_url='https://example.invalid/v1',enabled=False,key_env='EXPLICIT_R9_KEY')
    backend=APIBackend('external',cfg,r.tasks.directory)
    monkeypatch.setenv('OPENAI_API_KEY','must-not-use')
    assert not backend.available()
    for override in ({'base_url':'https://secret@example.com/v1'},{'api_key':'secret'},{'reasoning_parameter':'anything'}):
        with pytest.raises(ValueError):APIBackend('invalid',dict(cfg,**override),r.tasks.directory)
    from chat_backends import clean_environment
    monkeypatch.setenv('EXPLICIT_R9_KEY','secret-test-value')
    assert 'EXPLICIT_R9_KEY' not in clean_environment(['EXPLICIT_R9_KEY'])


def test_no_tool_provider_and_snapshot_restart(env,provider,docs):
    r,s,_,_=env;connect(r,provider)
    r.tasks.config['gemini_api']['tools']=False
    r.tasks.backends['gemini_api'].config['tools']=False
    p,a=docs('.txt',b'persisted private evidence')
    q=s.add('user','read this',attachments=[a],metadata={'visibility':['antigravity']})
    t=r.tasks.wait(r.tasks.start('general','antigravity',q)['task_id'])
    assert t['state']=='completed' and 'tools' not in provider.requests[-1]
    assert 'provider has no tool-call support' in provider.requests[-1]['messages'][1]['content'][0]['text']
    directory=Path(r.tasks.tasks[t['task_id']]['directory'])
    frozen=json.loads((directory/'snapshot.json').read_text())
    # The serialized snapshot alone retains ACL-selected document content after restart.
    assert 'persisted private evidence' in FrozenTools(frozen).call('attachment_read',{'message_id':q['id'],'index':0})
    from store import MessageStore
    restored=MessageStore(str(s._path))
    assert restored.get_by_id(q['id'])['attachments']==[a]
    from chat_visibility import PublicStore
    assert PublicStore(restored).get_by_id(q['id']) is None


def test_image_preflight_and_budget_refusal(env,docs):
    from PIL import Image
    r,s,_,_=env
    p,a=docs('.png');Image.new('RGB',(8,8),'red').save(p)
    # A mock/Claude text-only backend cannot silently accept an image request.
    before=len(s.get_since(-1))
    with pytest.raises(ValueError,match='image input'):
        asyncio.run(r.websocket(dict(type='message',text='see',channel='general',targets=['claude'],attachments=[a])))
    assert len(s.get_since(-1))==before
    q=s.add('user','see',attachments=[a])
    r.builder.budget=256
    with pytest.raises(ValueError,match='budget'):r.builder.build('general','claude',q)
