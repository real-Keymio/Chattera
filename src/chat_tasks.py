from chat_identities import IDENTITIES, LEGACY_CAPABLE
"""Single-owner task lifecycle; final replies commit under the same cancellation lock."""
import json
import hashlib
import threading
import time
import uuid
import copy
from pathlib import Path
from chat_rooms import append_event, encode
from chat_backends import CLIBackend, MockBackend
from model_settings import API_TYPES, merge, validate_settings
from chat_secrets import redact


class TaskManager:
    def __init__(self, data_dir, builder, config=None):
        self.directory = Path(data_dir) / "chat_calls"
        self.directory.mkdir(exist_ok=True)
        self.log = self.directory / "invocations.jsonl"
        self.builder, self.config = builder, config or {}
        self.lock = threading.RLock()
        self.registry = None  # set by the runtime; absent in unit tests that build a TaskManager directly
        self.tasks, self.active = {}, {}
        self.backends = {"mock": MockBackend()}
        secret_names = [c['key_env'] for c in self.config.values() if c.get('type') in API_TYPES and c.get('key_env')]
        import cli_adapters
        for model in cli_adapters.names():
            if model in self.config and self.config[model].get("type") not in API_TYPES:
                self.backends[model] = CLIBackend(model, dict(self.config[model], secret_env_names=secret_names))
        from chat_api import APIBackend
        for name, cfg in self.config.items():
            if cfg.get("type") in API_TYPES:
                self.backends[name] = APIBackend(name, cfg, self.directory)
        self._recover()

    def install_backend(self, name, cfg):
        """Create or replace one backend at runtime (profile edits). Validation errors leave the old one in place."""
        with self.lock:
            if cfg.get('type') in API_TYPES:
                from chat_api import APIBackend
                runner = APIBackend(name, {k: v for k, v in cfg.items()}, self.directory)
            else:
                import cli_adapters
                if cli_adapters.get(name) is None: raise ValueError('Unsupported backend type')
                secret_names = [c['key_env'] for c in self.config.values() if c.get('type') in API_TYPES and c.get('key_env')]
                runner = CLIBackend(name, dict(cfg, secret_env_names=secret_names))
            self.config[name] = cfg
            self.backends[name] = runner

    def cancel_backend(self, name):
        with self.lock:
            for task in list(self.tasks.values()):
                if task['state'] == 'running' and task.get('backend') == name:
                    self.cancel(task['channel'], task['model'])

    def quick_cli(self, name, model):
        """Smallest possible real CLI call, for the connection test. No room, no tools."""
        import tempfile
        runner = copy.copy(self.backends[name])
        runner.config = dict(runner.config, model=model, timeout=min(runner.config.get('timeout', 120), 90))
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'snapshot.json').write_text('{}', 'utf-8')
            result = runner.run(json.dumps({'request': 'Reply with the single word: ready'}), dict(directory=folder, baseline=True), lambda tree: None, threading.Event())
        return dict(text=result.get('text', ''), model=result.get('model', 'UNKNOWN'), usage=result.get('usage_fields', 'UNKNOWN'))

    def _recover(self):
        if not self.log.exists():
            return
        previous = {}
        for line in self.log.read_text("utf-8").splitlines():
            try:
                item = json.loads(line)
                previous[item["task_id"]] = item
            except (ValueError, KeyError):
                continue
        replies = {(m.get("metadata") or {}).get("task_id"): m["id"]
                   for m in self.builder.store.get_since(-1)}
        for task in previous.values():
            if task["state"] == "running":
                task.update(state="completed" if task["task_id"] in replies else "failed",
                            reason="server restart recovery", usage="UNKNOWN")
                if task["task_id"] in replies:
                    task["reply_id"] = replies[task["task_id"]]
                append_event(self.log, task)
            self.tasks[task["task_id"]] = task

    def public(self, task):
        return redact({k: v for k, v in task.items() if not k.startswith("_") and k not in ("cancel", "tree", "thread", "directory")})

    def record(self, task):
        task["updated_at"] = time.time()
        append_event(self.log, self.public(task))
        if getattr(self, "on_change", None):
            self.on_change(self.public(task))

    def list(self, channel=None):
        with self.lock:
            return [self.public(t) for t in self.tasks.values() if not channel or t["channel"] == channel][-100:]

    def participant_ids(self):
        return self.registry.active_ids() if self.registry else list(IDENTITIES)

    def validate_switch(self, model, backend, settings=None, require_available=True):
        if model not in self.participant_ids(): raise ValueError("Unknown participant")
        if backend == "legacy" and model not in LEGACY_CAPABLE: raise ValueError("This participant requires a configured API backend")
        if backend != "legacy" and backend not in self.backends:
            raise ValueError("Backend is not configured")
        if require_available and backend in self.backends and hasattr(self.backends[backend], 'available') and not self.backends[backend].available():
            raise ValueError("API backend disabled or configured key environment variable absent")
        if settings is not None: validate_settings(self.config.get(backend,{}),settings,backend)

    def switch(self, channel, model, backend, settings=None):
        self.validate_switch(model, backend, settings)
        with self.lock:
            self.cancel(channel, model)
            self.builder.rooms.set_owner(channel, model, backend, settings)

    def capabilities(self, backend, effective=None):
        cfg = effective or self.config.get(backend, {})
        api = cfg.get('type') in API_TYPES
        runner = self.backends.get(backend)
        return dict(available=backend == 'legacy' or (runner is not None and (not hasattr(runner, 'available') or runner.available())),
                    images=bool(cfg.get('images')) if api else backend in ('codex', 'claude'),
                    documents=backend != 'legacy', tools=bool(cfg.get('tools')) if api else backend != 'legacy',
                    private=backend in self.backends, independent=backend in self.backends)

    def effective_config(self, channel, model, override=None):
        backend=self.builder.rooms.owner(channel,model)
        room=self.builder.rooms.get(channel).get('model_settings',{}).get(model,{})
        if room.get('backend')!=backend:room={}
        base=self.backends[backend].config if hasattr(self.backends.get(backend),'list_models') else self.config.get(backend,{})
        own=self.registry.participant_settings(model,backend) if self.registry else {}
        return merge(base,{k:v for k,v in room.items() if k!='backend'},override or {},backend,own)

    def model_info(self, backend):
        cfg=self.config.get(backend,{})
        from context_budget import policy
        return redact(dict(backend=backend,protocol=cfg.get('protocol','openai-chat' if cfg.get('type')=='openai-compatible' else 'native-cli'),
                    current_model=cfg.get('model','UNKNOWN'),effort=cfg.get('effort',cfg.get('reasoning_value','UNKNOWN')),
                    thinking=cfg.get('thinking'),model_capabilities=redact(cfg.get('model_capabilities',{})),
                    capability_source='operator configuration; not inferred from model name',status='UNKNOWN',
                    capabilities=self.capabilities(backend),context_policy=policy(cfg,self.capabilities(backend)['tools']),manual_model_allowed=True,
                    **self.route_info(backend,cfg)))

    def route_info(self, backend, cfg):
        """Where a call actually goes. Host name only; never the key, path or query."""
        from urllib.parse import urlparse
        api = cfg.get('type') in API_TYPES
        runner = self.backends.get(backend)
        return dict(route='api' if api else 'mock' if backend == 'mock' else 'subscription-cli',
                    endpoint_host=(urlparse(cfg.get('base_url', '')).hostname or 'UNKNOWN') if api else None,
                    timeout_seconds=cfg.get('timeout', 'UNKNOWN'), max_output_tokens=cfg.get('max_output_tokens', 'UNKNOWN') if api else None,
                    max_requests=cfg.get('max_requests', 'UNKNOWN') if api else None,
                    cumulative_usage=dict(getattr(runner, 'usage', {})) if api and runner else None)

    def verification(self):
        """Evidence from recorded real calls, as opposed to capabilities merely declared in configuration."""
        result = {}
        with self.lock:
            for task in sorted(self.tasks.values(), key=lambda t: t.get('created_at', 0)):
                row = result.setdefault(task.get('backend', 'UNKNOWN'), dict(completed=0, failed=0, last_success_at=None,
                    last_failure_at=None, last_failure_reason=None, image_verified_at=None, tools_verified_at=None, actual_model=None, models={}))
                per = row['models'].setdefault(str(task.get('requested_model', 'UNKNOWN')), dict(completed=0, failed=0, last_success_at=None,
                    actual_model=None, image_verified_at=None, tools_verified_at=None))
                stamp = task.get('updated_at', task.get('created_at'))
                if task['state'] == 'completed':
                    row['completed'] += 1; row['last_success_at'] = stamp
                    per['completed'] += 1; per['last_success_at'] = stamp
                    if task.get('actual_model') not in (None, 'UNKNOWN'): row['actual_model'] = per['actual_model'] = task['actual_model']
                    if any(i.get('image_sha256') for i in task.get('image_processing') or []): row['image_verified_at'] = per['image_verified_at'] = stamp
                    if task.get('tool_calls'): row['tools_verified_at'] = per['tools_verified_at'] = stamp
                elif task['state'] == 'failed':
                    row['failed'] += 1; row['last_failure_at'] = stamp; row['last_failure_reason'] = task.get('reason')
                    per['failed'] += 1
        return redact(result)

    def start(self, channel, model, current, **options):
        with self.lock:
            if model not in self.participant_ids():
                raise ValueError("This participant has been removed")
            backend = self.builder.rooms.owner(channel, model)
            if backend not in self.backends:
                raise ValueError("Room is owned by the legacy backend")
            if hasattr(self.backends[backend], 'available') and not self.backends[backend].available():
                raise ValueError("Connection is switched off or has no key")
            key = (channel, model)
            if key in self.active:
                raise ValueError("This model is already replying in this room")
            override=options.pop('settings_override', None)
            if override is None:override=(current.get('metadata') or {}).get('model_overrides',{}).get(model,{})
            effective_cfg=self.effective_config(channel,model,override)
            caps=self.capabilities(backend,effective_cfg)
            room=self.builder.rooms.get(channel)
            mode=room.get('context_mode','256k');custom=room.get('context_limit_tokens')
            prepared = options.pop('prepared', None)
            evidence_id = options.pop('evidence_id', None)
            search_mode = options.pop('search_mode', None)
            snapshot = prepared['snapshot'] if prepared else self.builder.snapshot(channel, model, current, options.get('independent_mode') == 'strict')
            if not prepared:
                snapshot['backend_capabilities'] = caps
                if evidence_id and getattr(self.builder, 'evidence', None):
                    snapshot['shared_evidence'] = self.builder.evidence.load(evidence_id)
                if getattr(self.builder, 'library_info', None):
                    try: snapshot['library'] = self.builder.library_info(channel, model)
                    except (ValueError, OSError): pass
                if search_mode:
                    from chat_search import plan
                    snapshot['search'] = plan(backend, effective_cfg, caps['tools'], bool(snapshot.get('research_allowed')), search_mode)
                pacing = self.builder.agent_settings() if getattr(self.builder, 'agent_settings', None) else {}
                if pacing.get('agent'):
                    # Research agent: the chat folder for notes and downloads, and (for subscription CLIs) their own web search.
                    snapshot['agent'] = True
                    if getattr(self.builder, 'files_folder', None):
                        try: snapshot['workspace'] = dict(path=str(self.builder.files_folder(channel)))
                        except (OSError, ValueError): pass
                if getattr(self.builder, 'lens_for', None):
                    lens = self.builder.lens_for(channel, model, current)
                    if lens: snapshot['research_lens'] = lens
                from context_budget import policy
                snapshot['context_policy']=policy(effective_cfg,caps['tools'],mode,custom)
            if prepared:
                prompt, audit = prepared['prompt'], dict(prepared['audit'])
            else:
                prompt, audit = self.builder.build(channel, model, current, snapshot)
            from chat_attachments import image_blocks
            if image_blocks(snapshot, json.loads(prompt)) and not caps['images']:
                raise ValueError('Selected backend does not support image input; choose a compatible backend')
            if prepared and snapshot.get('backend_capabilities', {}).get('tools') and not caps['tools']:
                raise ValueError('Regeneration backend lacks the original snapshot tool capabilities; send a new question')
            snapshot=redact(snapshot); prompt=redact(prompt)
            actual_input_bytes=len(prompt.encode('utf-8'))
            from context_budget import check,policy,count
            effective_policy=policy(effective_cfg,caps['tools'],mode,custom)
            check(prompt,dict(snapshot,context_policy=effective_policy),json.loads(prompt))
            if not effective_policy and actual_input_bytes>self.builder.budget:raise ValueError('Redacted input exceeds configured budget')
            # Regeneration retains the original bytes/images, but admission must
            # be audited against the current selected backend's actual limits.
            snapshot['context_policy']=effective_policy
            if effective_policy:
                audit.update(context_policy=effective_policy,input_estimate=count(prompt),
                    budget=effective_policy['initial_budget_tokens'],budget_unit='estimated tokens',
                    estimate_method=effective_policy['count_method'])
            audit['input_bytes']=actual_input_bytes
            audit['multimodal_tokens']='UNKNOWN'
            from chat_visibility import inherited
            visibility = options.pop('reply_visibility', None) or inherited(snapshot['history'])
            if options.get('independent_mode'):
                visibility = [model]
            if not prepared and getattr(self.builder, 'remember_summary', None):
                try: self.builder.remember_summary(channel, model, (snapshot.get('summary') or {}).get('version'), (snapshot.get('prompt_split') or {}).get('base_message_id'))
                except OSError: pass
            task_id = str(uuid.uuid4())
            directory = self.directory / task_id
            directory.mkdir()
            (directory / "snapshot.json").write_text(encode(snapshot), "utf-8")
            (directory / "input.json").write_text(prompt, "utf-8")
            task = dict(audit, task_id=task_id, channel=channel, model=model, backend=backend,
                        state="running", usage="UNKNOWN", rate_limits="UNKNOWN", created_at=time.time(),
                        current_id=current["id"], identity=model, directory=str(directory), cancel=threading.Event(),
                        effort=effective_cfg.get("effort", effective_cfg.get("reasoning_value", "UNKNOWN")), **options)
            task['visibility'] = visibility
            task['history_policy'] = snapshot.get('history_policy', 'UNKNOWN')
            task['delivery_policy'] = 'withheld_independent' if options.get('independent_mode') else 'normal'
            task['execution_policy'] = 'concurrent_frozen_cutoff_no_same_turn_peer_replies'
            task['snapshot_message_ids'] = [m['id'] for m in snapshot['history']]
            task['snapshot_cutoff'] = snapshot['max_id']
            task['snapshot_summary_version'] = (snapshot.get('summary') or {}).get('version')
            if snapshot.get('research_lens'): task['research_lens'] = {k: snapshot['research_lens'][k] for k in ('id', 'name')}
            if snapshot.get('shared_evidence'): task['evidence_id'] = snapshot['shared_evidence']['id']
            if snapshot.get('search'): task['search'] = snapshot['search']
            if snapshot.get('library'): task['library_tag'] = snapshot['library'].get('task_tag')
            task['_effective_config']=effective_cfg
            if getattr(self.builder, 'agent_settings', None):   # without it (embedded use, tests) the connection's timeout applies
                minutes = self.builder.agent_settings().get('reply_minutes', 30)
                task['_deadline_seconds'] = minutes * 60 if minutes else None   # None: until the model is done or the user stops it
                task['time_limit_minutes'] = minutes or 'none'
            task['requested_model']=effective_cfg.get('model','UNKNOWN')
            task['requested_effort']=task['effort']
            task['requested_thinking']=effective_cfg.get('thinking')
            task["configuration"] = redact(dict(effective_cfg, tool_call_guard=__import__('chat_tools').TOOL_BUDGET,
                                          tool_result_chars=__import__('chat_tools').RESULT_CHARS, output_capture_bytes=64_000_000))
            task["input_sha256"] = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
            self.tasks[task_id] = task
            self.active[key] = task_id
            self.record(task)
            thread = threading.Thread(target=self._run, args=(task, prompt), daemon=True)
            task["thread"] = thread
            thread.start()
            return self.public(task)

    def _run(self, task, prompt):
        def attach(tree):
            with self.lock:
                task["tree"] = tree
                if task["state"] != "running":
                    tree.kill()
        def spawn(factory):
            with self.lock:
                if task["state"] != "running":
                    raise TimeoutError("Cancelled before process creation")
                tree = factory()
                task["tree"] = tree
                return tree
        attach.spawn = spawn
        def progress():
            with self.lock:
                if task["state"] == "running" and not task.get("output_started_at"):
                    task["output_started_at"] = time.time()
                    self.record(task)
        attach.progress = progress
        def searching(entry):
            # Live note for the interface: what this participant is looking up right now.
            with self.lock:
                if task["state"] == "running":
                    task["activity"] = dict(kind=entry['kind'], text=str(entry.get('query') or ' · '.join(entry.get('queries') or []) or entry.get('url') or '')[:200], at=entry.get('at'))
                    self.record(task)
        attach.search = searching
        def step(count=1):
            # Live count of tool calls for the interface ("37 steps"); recorded at most every 1.5 s.
            with self.lock:
                if task['state'] != 'running': return
                task['steps'] = task.get('steps', 0) + count
                if time.time() - task.get('_steps_recorded', 0) > 1.5:
                    task['_steps_recorded'] = time.time(); self.record(task)
        attach.step = step
        try:
            runner=self.backends[task['backend']]
            if isinstance(runner,CLIBackend):
                runner=copy.copy(runner)
                runner.config=dict(runner.config,**task['_effective_config'])
            try:
                result = redact(runner.run(prompt, task, attach, task["cancel"]))
            except Exception as exc:
                # The model refused the input as too long: once, with less history (60% of the room).
                smaller = self.smaller_input(task, exc)
                if smaller is None: raise
                prompt = smaller
                result = redact(runner.run(prompt, task, attach, task["cancel"]))
            with self.lock:
                # The final append and cancellation/switch are serialized.
                if task["state"] != "running" or self.builder.rooms.owner(task["channel"], task["model"]) != task["backend"]:
                    return
                if task.get('replaces_message_id') is not None:
                    from chat_visibility import effective
                    active = {m['id'] for m in effective(self.builder.store.get_since(-1, channel=task['channel']))}
                    if task['replaces_message_id'] not in active:
                        raise ValueError('Reply version changed during regeneration; result discarded')
                text = result.pop("text")
                if not isinstance(text, str) or not text.strip() or len(text) > 100000:
                    raise ValueError("Invalid final answer")
                task.update(result)
                # Keep logical identity distinct from the actual backend-reported model.
                task["actual_model"] = task.pop("model")
                task["model"] = task["identity"]
                reply = self.builder.store.add(task["model"], text, channel=task["channel"],
                    reply_to=task["current_id"], metadata=dict(task_id=task["task_id"],
                    room_id=task["room_id"], backend=task["backend"], targets=[],
                    visibility=task['visibility'], independent_group=task.get('independent_group'),
                    version=task.get('message_version', 1), version_root=task.get('version_root'), replaces_message_id=task.get('replaces_message_id'), generated_from=task["current_id"]))
                task.pop('activity', None)
                if task.get('search'):
                    from chat_search import from_tool_events, summary
                    events = []
                    log = Path(task['directory']) / 'tool-events.jsonl'
                    if log.exists():
                        for line in log.read_text('utf-8').splitlines():
                            try: events.append(json.loads(line))
                            except ValueError: pass
                    task['search_summary'] = summary(list(task.get('search_trace') or []) + from_tool_events(e for e in events if e.get('status') != 'started'))
                try:
                    import chat_files
                    saved = chat_files.written(task['directory'])
                    if saved: task['workspace_files'] = saved
                except OSError: pass
                if getattr(self.builder, 'library_ingest', None):
                    try: self.builder.library_ingest(task, text, reply['id'])
                    except Exception as exc: task['library_error'] = type(exc).__name__ + ': ' + str(exc)[:160]
                task.update(state="completed", reply_id=reply["id"])
                self.record(task)
        except Exception as exc:
            with self.lock:
                if task["state"] == "running":
                    task.pop('activity', None)
                    task.update(state="failed", reason=f"{type(exc).__name__}: {exc}")
                    self.record(task)
        finally:
            with self.lock:
                key = (task["channel"], task.get("identity", task["model"]))
                if self.active.get(key) == task["task_id"]:
                    self.active.pop(key, None)

    def smaller_input(self, task, exc):
        from context_budget import overflowed
        if task.get('context_retry') or task['state'] != 'running' or not overflowed(str(exc)): return None
        directory = Path(task['directory'])
        try:
            snapshot = json.loads((directory / 'snapshot.json').read_text('utf-8'))
            current = self.builder.store.get_by_id(task['current_id'])
            policy = snapshot.get('context_policy')
            if not policy or not current: return None
            snapshot['context_policy'] = dict(policy, initial_budget_tokens=int(policy['initial_budget_tokens'] * .6), reduced_after='input too long')
            prompt, audit = self.builder.build(task['channel'], task['identity'], current, snapshot)
        except (OSError, ValueError):
            return None
        prompt = redact(prompt)
        (directory / 'input.json').write_text(prompt, 'utf-8')
        (directory / 'snapshot.json').write_text(encode(redact(snapshot)), 'utf-8')
        with self.lock:
            task.update({k: audit[k] for k in ('history_included_ids', 'trimmed_history_ranges', 'summarized_history_ranges', 'included_message_ranges',
                                              'message_ids', 'input_bytes', 'input_estimate') if k in audit},
                        context_retry=dict(first_error=str(exc)[:300], budget=snapshot['context_policy']['initial_budget_tokens']),
                        input_sha256=hashlib.sha256(prompt.encode('utf-8')).hexdigest())
            self.record(task)
        return prompt

    def cancel(self, channel=None, model=None):
        with self.lock:
            for task in self.tasks.values():
                if task["state"] != "running" or (channel and task["channel"] != channel) or (model and task["model"] != model):
                    continue
                task["state"] = "cancelled"
                task["cancel"].set()
                tree = task.get("tree")
                if tree:
                    tree.kill()
                    tree.process.wait(timeout=5)
                self.record(task)
                self.active.pop((task["channel"], task["model"]), None)

    def wait(self, task_id, timeout=10):
        thread = self.tasks[task_id].get("thread")
        if thread:
            thread.join(timeout)
        return self.public(self.tasks[task_id])
