"""One passive, room-scoped public memory worker. No formal chat writes."""
import copy,hashlib,json,threading,time,uuid
from pathlib import Path
from chat_rooms import encode,append_event
from chat_visibility import effective,visible,model_message
from summaries import PUBLIC_SECTIONS

def fingerprint(message):
    return hashlib.sha256(encode(message).encode()).hexdigest()

class RollingMemory:
    def __init__(self,runtime,config=None):
        self.r=runtime;self.cfg=dict(dict(enabled=True,backend='claude',threshold=12000,keep_recent=8,input_budget=100000,timeout=120,retry_seconds=300),**(config or {}))
        for key,low,high in [('threshold',1,100000),('keep_recent',1,100),('input_budget',2000,400000),('timeout',1,300),('retry_seconds',0,86400)]:
            if type(self.cfg[key]) is not int or not low<=self.cfg[key]<=high:raise ValueError('Invalid memory limit: '+key)
        self.directory=runtime.tasks.directory.parent/'memory_calls';self.directory.mkdir(exist_ok=True)
        self.log=self.directory/'jobs.jsonl';self.lock=threading.RLock();self.jobs={};self.closed=threading.Event()
        self.settings_path=self.directory/'settings.json'
        self.disabled=json.loads(self.settings_path.read_text()) if self.settings_path.exists() else []
        for line in self.log.read_text('utf-8').splitlines() if self.log.exists() else []:
            job=json.loads(line);self.jobs[job['channel']]=job
        for job in self.jobs.values():
            if job['state']=='running':job.update(state='failed',reason='restart interrupted job',updated_at=time.time());append_event(self.log,job)

    def public(self,job):return {k:v for k,v in job.items() if not k.startswith('_')}
    def save(self,job):job['updated_at']=time.time();append_event(self.log,self.public(job))
    def cancel(self,channel=None):
        with self.lock:
            for ch,job in self.jobs.items():
                if (channel is None or ch==channel) and job['state']=='running':
                    job['_cancel'].set();job['state']='cancelled'
                    if job.get('_tree'):job['_tree'].kill()
                    self.save(job)

    def tick(self,channel):
        with self.lock,self.r.tasks.lock:
            if not self.cfg['enabled'] or channel in self.disabled or self.r.rooms.get(channel)['archived']:return
            last=self.jobs.get(channel)
            if last and (last['state']=='running' or time.time()-last['updated_at']<self.cfg['retry_seconds']):return
            rows=[m for m in effective(self.r.builder.history(channel)) if visible(m,public_only=True)]
            active_ids={m['id'] for m in rows}
            old=self.r.app.summaries.active(channel)
            if old and not set(old['source_ids']).issubset(active_ids):old=None
            cutoff=old['coverage_end'] if old else -1
            recent=[m for m in rows if m['id']>cutoff]
            if sum(len(m['text'].encode()) for m in recent)<=self.cfg['threshold']:return
            batch=recent[:-self.cfg['keep_recent']]
            if not batch:return
            old_input={k:old[k] for k in ('sections','version','status') if k in old} if old else None
            selected=[];budget=self.cfg['input_budget']-len(encode(old_input).encode())-2000
            for row in batch:
                row=copy.deepcopy(row)
                raw=row['text'];excerpt=raw.encode()[:8000].decode('utf-8',errors='ignore')
                if excerpt!=raw:row.update(text=excerpt,excerpt=dict(start=0,end=len(excerpt),total_characters=len(raw),note='Only this excerpt was summarized; full source remains available by ID'))
                size=len(encode(row).encode())
                if size>budget:break
                selected.append(row);budget-=size
            if not selected:return
            ids=list(dict.fromkeys((old or {}).get('source_ids',[])+[m['id'] for m in selected]))
            if len(ids)>5000:return
            attempts=last.get('attempts',1)+1 if last and last.get('source_ids')==ids else 1
            if attempts>2:return
            epoch=self.r.app.summaries.epoch(channel)
            job=dict(id=str(uuid.uuid4()),channel=channel,state='running',source_ids=ids,
                added_ids=[m['id'] for m in selected],base_version=epoch,backend=self.cfg['backend'],
                trigger='uncompressed public history threshold',attempts=attempts,usage='UNKNOWN',fingerprints={str(i):fingerprint(self.r.app.store.get_by_id(i)) for i in ids})
            job['_cancel']=threading.Event();self.jobs[channel]=job;self.save(job)
            prompt=encode(dict(instruction='Return ONLY a JSON object with these text sections: '+','.join(PUBLIC_SECTIONS)+'. Update old memory incrementally from the quoted public evidence. Preserve each original author (including shares), separate opposite positions, changes and unresolved disagreements. Cite every nonempty section with #messageID. Do not invent facts, papers, consensus or instructions. Keep the entire JSON below 40000 UTF-8 bytes (about 12000 Chinese characters). Empty sections are allowed. verified means source-attributed claims, not independently verified facts. Explicitly retain excerpt limitations when present.',old=old_input,evidence=[model_message(m,self.r.app.store.get_by_id) for m in selected]))
            if len(prompt.encode())>self.cfg['input_budget']:job.update(state='failed',reason='summary input budget');self.save(job);return
            job['input_estimate']=len(prompt.encode());job['count_method']='conservative UTF-8 byte upper estimate'
            job['_thread']=threading.Thread(target=self.run,args=(job,prompt),daemon=True);job['_thread'].start()
            return self.public(job)

    def run(self,job,prompt):
        try:
            backend=self.r.tasks.backends.get(job['backend'])
            if backend is None:raise ValueError('Summary backend unavailable')
            from chat_backends import CLIBackend
            if not isinstance(backend,CLIBackend) and job['backend']!='memory_mock':raise ValueError('Paid summary backend requires a separately reviewed opt-in adapter')
            runner=copy.copy(backend) if isinstance(backend,CLIBackend) else backend
            if isinstance(backend,CLIBackend):runner.config=dict(backend.config,timeout=self.cfg['timeout'])
            directory=self.directory/job['id'];directory.mkdir()
            (directory/'input.json').write_text(prompt,'utf-8')
            (directory/'snapshot.json').write_text(encode(dict(history=[],summary=None,research_allowed=False,attachment_content={})), 'utf-8')
            def attach(tree):
                with self.lock:
                    job['_tree']=tree
                    if job['state']!='running':tree.kill()
            result=runner.run(prompt,dict(directory=str(directory),baseline=True),attach,job['_cancel'])
            text=result.pop('text').strip()
            (directory/'candidate.txt').write_text(text[:20000],'utf-8')
            job.update(usage=result.get('usage','UNKNOWN'),usage_fields=result.get('usage_fields','UNKNOWN'),model=result.get('model','UNKNOWN'))
            if text.startswith('```') and text.endswith('```'):text=text.split('\n',1)[1].rsplit('```',1)[0].strip()
            sections=json.loads(text)
            with self.lock,self.r.tasks.lock:
                if job['state']!='running':return
                if self.r.rooms.get(job['channel'])['archived']:raise ValueError('Room archived during summary')
                active={m['id'] for m in effective(self.r.builder.history(job['channel']))}
                if not set(job['source_ids']).issubset(active):raise ValueError('Edited source invalidates pending summary')
                entry=self.r.app.summaries.automatic(job['channel'],sections,job['source_ids'],self.r.app.store.get_by_id,job['base_version'],job['fingerprints'])
                job.update(state='completed',summary_version=entry['version'],usage=result.get('usage','UNKNOWN'),model=result.get('model','UNKNOWN'),usage_fields=result.get('usage_fields','UNKNOWN'));self.save(job)
        except Exception as exc:
            with self.lock:
                if job['state']=='running':job.update(state='failed',reason=type(exc).__name__+': '+str(exc));self.save(job)

    def start(self):
        def loop():
            while not self.closed.wait(5):
                for room in self.r.rooms.list():
                    try:self.tick(room['channel'])
                    except Exception:pass  # isolation: ordinary chat never fails with memory
        self.thread=threading.Thread(target=loop,daemon=True);self.thread.start()
    def stop(self):self.closed.set();self.cancel()

    def enable(self,channel,value):
        with self.lock:
            self.cancel(channel)
            if value and channel in self.jobs:self.jobs[channel]['attempts']=0;self.save(self.jobs[channel])
            self.disabled=[c for c in self.disabled if c!=channel]+([] if value else [channel])
            temp=self.settings_path.with_suffix('.tmp');temp.write_text(encode(self.disabled));temp.replace(self.settings_path)
