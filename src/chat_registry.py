"""UI-managed overlay on the static chat configuration: API profiles, model catalogue, saved
combinations, research lenses and retrieval settings.

The launch configuration file is never rewritten. Everything edited from the interface lives in
`<data>/model_registry.json`; deleting that file restores the launch configuration. API keys are
written only to the existing private key directory and are never returned by any endpoint.
"""
import copy
import json
import os
import re
import subprocess
import threading
import time
import uuid
from pathlib import Path

from chat_identities import IDENTITIES, BUILTIN
from chat_secrets import get_key, key_path, redact, remember
from model_settings import API_TYPES, CLI_EFFORT, merge
from chat_search import native_kind, NATIVE

NAME = r'[a-z][a-z0-9_-]{0,63}'
MODEL = r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}'
BUILTIN_LABELS = {'claude': 'Claude', 'codex': 'Codex', 'antigravity': 'Gemini', 'deepseek': 'DeepSeek'}
BUILTIN_COLORS = {'claude': '#c8643c', 'codex': '#2f8f7a', 'antigravity': '#4a78d6', 'deepseek': '#7a5cc8'}
PALETTE = ['#c2477f', '#b8862b', '#3f9fb5', '#6b8f3a', '#d0603a', '#8a6fd1', '#2f7fb8', '#a8576f', '#4f9a6e', '#9a7a3c']
EFFORT_LABELS = {'none': '关闭', 'minimal': '极低', 'low': '低', 'medium': '中', 'high': '高', 'xhigh': '超高', 'max': '最大', 'ultra': '极限'}
RESERVED = {'legacy', 'mock', 'system', 'user', 'all', 'both', 'you', 'human', 'public'}
import cli_adapters as _adapters
CLI_LABELS = {name: _adapters.get(name).label for name in _adapters.names()}
# Live display tables for every participant id, kept in step with the registry (see Registry.refresh).
LABELS = dict(BUILTIN_LABELS)
COLORS = dict(BUILTIN_COLORS)
PROFILE_FIELDS = {'identity', 'protocol', 'base_url', 'model', 'enabled', 'timeout', 'max_output_tokens', 'max_requests',
                  'max_requests_per_task', 'tools', 'images', 'effort', 'thinking', 'reasoning_parameter', 'output_limit_field',
                  'provider', 'anthropic_version', 'anthropic_auth', 'native_search', 'google_auth'}

LENSES = {
 'open': dict(name='开放科学讨论', builtin=True, summary='默认。自然交流，质疑前提，保留分歧，不把讨论变成交付任务。', method=''),
 'design': dict(name='研究设计与方法审查', builtin=True, summary='变量操作化、测量效度、对照、混杂、可重复性。', method=(
  'For this reply, read the question as a methodologist would. Work out what was (or would be) actually manipulated and measured, and whether '
  'those operationalisations capture the construct the claim is about. Look for what the comparison is against: the control or counterfactual, '
  'how units were assigned, what else differs between groups, and which confounders or selection steps could produce the same result. Ask what '
  'observation would have contradicted the hypothesis and whether the design could have produced it. Consider measurement validity and '
  'reliability, blinding, pre-specification, and whether someone else could repeat the procedure from what is reported. Say which weaknesses '
  'would change the conclusion and which are minor. This is a way of looking, not a verdict to reach: if the design is sound, say so plainly.')),
 'stats': dict(name='统计与因果推断', builtin=True, summary='效应量、区间、统计假设、样本量、选择偏倚、因果识别。', method=(
  'For this reply, reason as a statistician concerned with what the numbers can support. Prefer effect sizes with intervals over significance '
  'labels; check whether the sample size and variance make the reported precision plausible. Name the assumptions the analysis depends on '
  '(independence, distributional form, missing-data mechanism, multiplicity, model specification) and whether they are likely to hold. '
  'Separate association from causal claims: state the identification strategy, the estimand, and what selection, reverse causation, '
  'collider or measurement bias could do to it. When numbers are given, check them (use the calculation tool if it is available) rather than '
  'trusting the arithmetic. Distinguish "no evidence of an effect" from "evidence of no effect". Do not invent statistics that were not provided.')),
 'evidence': dict(name='文献与证据审查', builtin=True, summary='来源、证据等级、方法与结论的对应、未被证实的推断。', method=(
  'For this reply, act as a careful reader of sources. For each substantive claim, ask where it comes from and how strong that kind of source '
  'is (systematic review, trial, observational study, preprint, expert opinion, secondary reporting). Check whether the cited methods can '
  'support the stated conclusion, and mark where the text moves from what was shown to what is inferred. Keep three things apart: what you '
  'read in material supplied in this conversation, what a retrieval tool returned in this turn and at what depth (metadata, abstract, full '
  'text), and what you recall from training. Cite only sources you can trace, with their identifier; if you could not read something, say so '
  'instead of summarising it from memory. Note relevant evidence that is missing or that would cut the other way.')),
 'transfer': dict(name='跨学科与转化研究', builtin=True, summary='方法与结论能否迁移到其他领域、人群和应用环境。', method=(
  'For this reply, think about transfer. Identify the conditions under which the finding or method was established (population, model system, '
  'scale, setting, time horizon, measurement tools) and ask which of those conditions the result depends on. Consider how it would '
  'behave in a different field, population or practical setting: which assumptions travel, which mechanisms are likely shared, and '
  'what would have to be re-validated. Bring in concepts or methods from neighbouring disciplines when they explain the phenomenon better '
  'or expose a blind spot, and say how the analogy could fail. Distinguish efficacy under ideal conditions from effectiveness in use, and '
  'note practical, ethical or regulatory constraints only where they bear on feasibility.')),
}


def _atomic(path, value):
    temp = Path(path).with_suffix('.' + os.urandom(6).hex() + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=1), 'utf-8')
    temp.replace(path)


class Registry:
    def __init__(self, data_dir, cfg, config_path=None):
        self.path = Path(data_dir) / 'model_registry.json'
        self.lock = threading.RLock()
        self.base = copy.deepcopy(cfg.get('backends', {}))
        self.base_defaults = dict(cfg.get('default_owners', {}))
        self.data = dict(version=1, profiles={}, cli={}, removed=[], catalog={}, combos=[], defaults={}, lenses={}, search={}, tests={},
                         participants={}, meta={}, library={})
        if self.path.exists():
            self.data.update(json.loads(self.path.read_text('utf-8')))
        private = cfg.get('private_dir')
        for backend in self.base.values():
            if not private and backend.get('key_file'): private = str(Path(backend['key_file']).parent)
        if not private and config_path:
            private = str(Path(config_path).resolve().parents[2] / '.multichat-private')
        self.private = Path(private) if private else Path(data_dir).resolve() / '.multichat-private'
        self.seed_catalog()
        self.refresh()

    def save(self):
        with self.lock: _atomic(self.path, self.data)

    # ---- effective configuration ----
    def backends(self):
        """Launch configuration + overlay, with the catalogue compiled into per-model capabilities and windows."""
        with self.lock:
            result = {}
            for name, cfg in self.base.items():
                if name in self.data['removed']: continue
                value = copy.deepcopy(cfg)
                if cfg.get('type') in API_TYPES: value.update(self.data['profiles'].get(name, {}))
                else: value.update({k: v for k, v in self.data['cli'].get(name, {}).items() if k in ('model', 'effort', 'timeout', 'enabled', 'native_search')})
                result[name] = value
            for name, cfg in self.data['profiles'].items():
                if name not in self.base and name not in self.data['removed']:
                    result[name] = copy.deepcopy(cfg)
            for name, value in result.items():
                models = self.data['catalog'].get(name, {})
                caps = dict(value.get('model_capabilities', {}))
                contexts = {}
                api = value.get('type') in API_TYPES
                for model, entry in models.items():
                    cap = dict(caps.get(model, {}))
                    if api:
                        for flag in ('tools', 'images'):
                            if type(entry.get(flag)) is bool: cap[flag] = entry[flag]
                    if entry.get('effort_values'): cap['effort_values'] = list(entry['effort_values'])
                    if cap: caps[model] = cap
                    if type(entry.get('window_tokens')) is int:
                        base_context = value.get('context', {}) if value.get('context', {}).get('model') == model else {}
                        contexts[model] = dict(model=model, window_tokens=entry['window_tokens'],
                            native_window_tokens=entry.get('native_window_tokens', base_context.get('native_window_tokens', 'UNKNOWN')),
                            backend_reserve=entry.get('backend_reserve', base_context.get('backend_reserve', 4096 if api else 32000)),
                            status=entry.get('window_status', 'UNKNOWN'), source=entry.get('window_source', 'model catalogue entry'))
                if caps: value['model_capabilities'] = caps
                if contexts: value['model_contexts'] = contexts
            return result

    # ---- participants ----
    def participants(self):
        """Everyone who can speak in a chat: the four built-in identities plus those added here. Retired ones stay listed
        so their old messages keep a name."""
        with self.lock:
            rows = []
            for pid in [*BUILTIN, *[p for p in self.data['participants'] if p not in BUILTIN]]:
                saved = self.data['participants'].get(pid, {})
                builtin = pid in BUILTIN
                connection = saved.get('connection') or ((self.data['defaults'].get(pid) or self.base_defaults.get(pid)) if builtin else None)
                rows.append(dict(id=pid, builtin=builtin, label=saved.get('label') or BUILTIN_LABELS.get(pid, pid),
                                 color=saved.get('color') or BUILTIN_COLORS.get(pid, '#888888'), connection=connection,
                                 model=saved.get('model'), effort=saved.get('effort'), thinking=saved.get('thinking'),
                                 hidden=bool(saved.get('hidden')), retired=bool(saved.get('retired')) and not builtin,
                                 created_at=saved.get('created_at')))
            return rows

    def participant(self, pid):
        return next((row for row in self.participants() if row['id'] == pid), None)

    def active_ids(self):
        return [row['id'] for row in self.participants() if not row['retired']]

    def dynamic_owners(self):
        return {row['id']: row['connection'] for row in self.participants()
                if not row['builtin'] and not row['retired'] and row['connection']}

    def participant_settings(self, pid, backend):
        """The participant's own model choice; applies wherever it speaks through its own connection."""
        row = self.participant(pid)
        if not row or row['connection'] != backend: return {}
        return {k: row[k] for k in ('model', 'effort', 'thinking') if row[k] is not None}

    def refresh(self):
        """Publish ids, names and colours to the live tables other modules read."""
        rows = self.participants()
        IDENTITIES[:] = [row['id'] for row in rows]
        for row in rows:
            LABELS[row['id']] = row['label']
            COLORS[row['id']] = row['color']

    def defaults(self):
        return {row['id']: row['connection'] for row in self.participants() if not row['retired'] and row['connection']}

    def default_effort(self, backend, model, current=None):
        """An effort the model accepts: the current one if allowed, else the model's stated default, else a middle level."""
        cfg = self.backends().get(backend, {})
        entry = self.data['catalog'].get(backend, {}).get(model or cfg.get('model'), {})
        levels = entry.get('effort_values') or (None if cfg.get('type') in API_TYPES else CLI_EFFORT.get(backend))
        if not levels or current in levels: return current
        if entry.get('default_effort') in levels: return entry['default_effort']
        return 'medium' if 'medium' in levels else levels[len(levels) // 2]

    def connection_label(self, name, cfg=None):
        saved = self.data['meta'].get(name, {}).get('label')
        if saved: return saved
        cfg = cfg or self.backends().get(name, {})
        if cfg.get('type') not in API_TYPES: return CLI_LABELS.get(name, name)
        return {'deepseek': 'DeepSeek'}.get(name, name)

    def seed_catalog(self):
        """The model each backend is configured with is always in the catalogue (declared, not verified)."""
        with self.lock:
            changed = False
            for name, cfg in self.backends().items():
                model = cfg.get('model')
                models = self.data['catalog'].setdefault(name, {})
                if model and model not in models:
                    context = cfg.get('context', {}) if cfg.get('context', {}).get('model') == model else {}
                    api = cfg.get('type') in API_TYPES
                    entry = dict(label=model, source='launch configuration', added_at=time.time())
                    if api: entry.update(tools=bool(cfg.get('tools')), images=bool(cfg.get('images')))
                    if type(context.get('window_tokens')) is int:
                        entry.update(window_tokens=context['window_tokens'], window_status=context.get('status', 'UNKNOWN'),
                                     window_source=context.get('source', ''), native_window_tokens=context.get('native_window_tokens', 'UNKNOWN'),
                                     backend_reserve=context.get('backend_reserve', 4096 if api else 32000))
                    levels = cfg.get('model_capabilities', {}).get(model, {}).get('effort_values') or CLI_EFFORT.get(name)
                    if levels: entry['effort_values'] = list(levels)
                    models[model] = entry
                    changed = True
            if changed: self.save()

    # ---- keys ----
    def key_file(self, name):
        if not re.fullmatch(NAME, name): raise ValueError('Invalid profile name')
        return self.private / (name + '.key')

    def write_key(self, name, value):
        value = str(value or '').strip()
        if len(value) < 8 or len(value) > 4096 or any(c.isspace() for c in value): raise ValueError('Invalid API key format')
        root = self.private
        from chat_secrets import KEY_DIRS
        if root.name not in KEY_DIRS or not root.is_absolute() or root.is_symlink(): raise ValueError('Private key directory is not configured')
        if not root.exists():
            root.mkdir(parents=True)
            if os.name == 'nt':
                who = subprocess.check_output(['whoami'], text=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).strip()
                done = subprocess.run(['icacls', str(root), '/inheritance:r', '/grant:r', who + ':(OI)(CI)F'], capture_output=True,
                                      creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                if done.returncode: raise ValueError('Could not restrict the private key directory; no key written')
            else: root.chmod(0o700)
        path = self.key_file(name)
        temp = path.with_suffix('.' + os.urandom(6).hex() + '.tmp')
        temp.write_text(value, 'utf-8')
        if os.name != 'nt': temp.chmod(0o600)
        temp.replace(path)
        remember(value)
        return str(path)

    def remove_key(self, name):
        path = self.key_file(name)
        if path.exists(): path.unlink()

    # ---- views ----
    def profile_view(self, name, cfg, runner=None):
        api = cfg.get('type') in API_TYPES
        from urllib.parse import urlsplit
        view = dict(name=name, label=self.connection_label(name, cfg), preset=self.data['meta'].get(name, {}).get('preset'),
                    kind='api' if api else 'cli', model=cfg.get('model'), effort=cfg.get('effort', cfg.get('reasoning_value')),
                    timeout=cfg.get('timeout', 120), origin='launch configuration' if name in self.base else 'added in interface',
                    native_search=cfg.get('native_search'), search_kind=native_kind(name, cfg), google_auth=cfg.get('google_auth'),
                    edited=name in self.data['profiles'] or name in self.data['cli'])
        if api:
            try: present = bool(get_key(cfg))
            except ValueError: present = False
            view.update(identity=cfg.get('identity'), protocol=cfg.get('protocol', 'openai-chat'), base_url=cfg.get('base_url', ''),
                        host=urlsplit(cfg.get('base_url', '')).hostname or '', enabled=cfg.get('enabled') is True, key_present=present,
                        key_source='private key file' if cfg.get('key_file') else 'environment variable ' + cfg.get('key_env', ''),
                        tools=bool(cfg.get('tools')), images=bool(cfg.get('images')), thinking=cfg.get('thinking'),
                        max_output_tokens=cfg.get('max_output_tokens'), max_requests=cfg.get('max_requests'),
                        max_requests_per_task=cfg.get('max_requests_per_task'), reasoning_parameter=cfg.get('reasoning_parameter'),
                        provider=cfg.get('provider', ''), usage=dict(getattr(runner, 'usage', {})) if runner else None,
                        available=bool(runner and runner.available()))
        else:
            view.update(executable=Path(cfg.get('executable', '')).name, version=(cfg.get('verified_cli') or {}).get('version'),
                        enabled=cfg.get('enabled', True) is not False, available=runner is not None and runner.available(),
                        effort_values=CLI_EFFORT.get(name, []))
        return redact(view)

    def lenses(self):
        return {**{k: dict(v, id=k) for k, v in LENSES.items()}, **{k: dict(v, id=k, builtin=False) for k, v in self.data['lenses'].items()}}

    def search_settings(self):
        value = dict(web_provider='duckduckgo', reader_fallback=True, planner='auto')
        value.update({k: v for k, v in self.data['search'].items()})
        return value


def install(runtime, app_module, cfg, config_path):
    from fastapi import Request
    from fastapi.responses import JSONResponse
    registry = runtime.registry
    tasks = runtime.tasks
    api = app_module.app

    def fail(exc, code=400):
        return JSONResponse({'error': str(redact(str(exc)))}, status_code=code)

    def rooms_using(name):
        """Chats where someone was explicitly switched to this connection (not counting participants that merely default to it)."""
        with runtime.rooms.lock:
            return [r['name'] for r in runtime.rooms.rooms.values() if not r['archived'] and name in r['owners'].values()]

    def bound(name):
        return [row['label'] for row in registry.participants() if not row['retired'] and row['connection'] == name]

    def slug(text, taken, fallback):
        value = re.sub(r'[^a-z0-9]+', '-', str(text or '').lower()).strip('-')[:40]
        if not re.fullmatch(NAME, value or ''): value = fallback
        base, n = value, 2
        while value in taken:
            value = f'{base}-{n}'; n += 1
        return value

    async def announce():
        runtime.sync_participants()
        runtime.sync_settings()
        await app_module.broadcast_settings()
        await app_module.broadcast_agents()

    def search_runtime():
        value = registry.search_settings()
        for field, profile in (('_web_key', 'search_' + value.get('web_provider', '')), ('_openalex_key', 'search_openalex')):
            path = registry.private / (profile + '.key')
            try:
                if path.exists(): value[field] = remember(path.read_text('utf-8').strip())
            except OSError: pass
        return value
    runtime.evidence.settings = search_runtime

    def overview():
        effective = registry.backends()
        verification = tasks.verification()
        profiles = [registry.profile_view(name, c, tasks.backends.get(name)) for name, c in effective.items()]
        catalog = {}
        for name, models in registry.data['catalog'].items():
            if name not in effective: continue
            proven = (verification.get(name) or {}).get('models', {})
            catalog[name] = [dict(entry, id=model, test=registry.data['tests'].get(name, {}).get(model), production=proven.get(model),
                                  current=effective[name].get('model') == model) for model, entry in models.items()]
        search = registry.search_settings()
        return redact(dict(
            participants=[dict(row, available=bool(row['connection'] in tasks.backends and tasks.capabilities(row['connection'])['available']),
                               legacy_capable=row['id'] in BUILTIN[:3]) for row in registry.participants()],
            effort_labels=EFFORT_LABELS,
            profiles=profiles, catalog=catalog, combos=registry.data['combos'], defaults=registry.defaults(),
            verification=verification, lenses=list(registry.lenses().values()),
            search=dict(search, web_key_present=(registry.private / ('search_' + search['web_provider'] + '.key')).exists(),
                        openalex_key_present=(registry.private / 'search_openalex.key').exists(),
                        planner_effective=runtime.planner_backend()),
            cli_effort=CLI_EFFORT))

    def apply(name):
        """Rebuild one backend from the effective configuration; raise before changing anything if invalid."""
        effective = registry.backends()
        if name not in effective:
            tasks.config.pop(name, None); tasks.backends.pop(name, None)
            return
        tasks.install_backend(name, effective[name])

    @api.get('/api/chat/models')
    async def models_overview():
        return overview()

    @api.post('/api/chat/profiles')
    async def save_profile(request: Request):
        body = await request.json()
        name = str(body.get('name') or '')
        try:
            label = str(body.get('label') or '').strip()[:40]
            if not name:
                if not label: raise ValueError('给这个连接起个名字')
                name = slug(label, set(registry.backends()) | set(registry.data['removed']) | {'legacy', 'mock', *BUILTIN[:3]}, 'api-' + uuid.uuid4().hex[:6])
            if not re.fullmatch(NAME, name) or name in ('legacy', 'mock', *BUILTIN[:3]) or name.startswith('search_'):
                raise ValueError('Profile name: lowercase letters, digits, - or _, starting with a letter; reserved names are not allowed')
            changes = {k: v for k, v in (body.get('settings') or {}).items() if k in PROFILE_FIELDS}
            with registry.lock, tasks.lock:
                existing = registry.backends().get(name)
                if existing and existing.get('type') not in API_TYPES: raise ValueError('That name belongs to a subscription CLI')
                before = copy.deepcopy(registry.data)
                if existing is None:
                    changes = dict(dict(type='api', protocol='openai-chat', enabled=True, timeout=120, max_output_tokens=8192, max_requests=None,
                                        max_requests_per_task=1, tools=False, images=False, key_file=str(registry.key_file(name))), **changes)
                    if os.environ.get('MULTICHAT_ALLOW_LOOPBACK_API') == '1' and re.match(r'http://127\.0\.0\.1[:/]', str(changes.get('base_url', ''))):
                        changes['mock'] = True
                elif name in registry.base:
                    changes = dict(registry.data['profiles'].get(name, {}), **changes)
                else:
                    changes = dict(registry.data['profiles'][name], **changes)
                if changes.get('native_search') not in (None, 'off', *NATIVE): raise ValueError('Unknown search route')
                if changes.get('google_auth') not in (None, 'bearer'): raise ValueError('Unknown Google authentication mode')
                source_key = body.get('copy_key_from')
                if source_key is not None:
                    # Same provider account reached over another protocol: reuse its key inside the private directory, never through the browser.
                    other = registry.backends().get(str(source_key), {})
                    if existing is not None or not other.get('key_file') or not Path(other['key_file']).is_file(): raise ValueError('No stored key to reuse')
                    registry.write_key(name, Path(other['key_file']).read_text('utf-8').strip())
                for key in [k for k, v in changes.items() if v is None and k not in ('max_requests', 'thinking')]: changes.pop(key)
                if changes.get('thinking') is None: changes.pop('thinking', None)
                if changes.get('tools') and changes.get('max_requests_per_task', 1) < 2: changes['max_requests_per_task'] = 6
                registry.data['profiles'][name] = changes
                model = changes.get('model') or (existing or {}).get('model')
                entry = registry.data['catalog'].setdefault(name, {}).get(model)
                for flag in ('tools', 'images'):
                    if entry is not None and flag in (body.get('settings') or {}): entry[flag] = bool(changes.get(flag))
                if name in registry.data['removed']: registry.data['removed'].remove(name)
                if label or body.get('preset'):
                    note = registry.data['meta'].setdefault(name, {})
                    if label: note['label'] = label
                    if isinstance(body.get('preset'), str): note['preset'] = body['preset'][:40]
                try:
                    registry.seed_catalog()
                    apply(name)
                except Exception:
                    registry.data = before
                    raise
                registry.save()
            return dict(overview(), saved=name)
        except (ValueError, KeyError, TypeError) as exc:
            return fail(exc)

    @api.post('/api/chat/profiles/{name}/duplicate')
    async def duplicate_profile(name: str, request: Request):
        body = await request.json()
        new = str(body.get('name') or '')
        try:
            with registry.lock, tasks.lock:
                source = registry.backends().get(name)
                if not source or source.get('type') not in API_TYPES: raise ValueError('Only API profiles can be duplicated')
                if not new: new = slug(name + '-copy', set(registry.backends()) | set(registry.data['removed']), 'api-' + uuid.uuid4().hex[:6])
                if not re.fullmatch(NAME, new) or new in registry.backends() or new in ('legacy', 'mock', *BUILTIN[:3]) or new.startswith('search_'):
                    raise ValueError('Choose a new, unused profile name')
                registry.data['meta'][new] = dict(registry.data['meta'].get(name, {}), label=(str(body.get('label') or '').strip() or registry.connection_label(name) + ' 副本')[:40])
                value = {k: v for k, v in source.items() if k not in ('key_env', 'key_file', 'model_capabilities', 'model_contexts', 'verified_cli')}
                value['key_file'] = str(registry.key_file(new))
                registry.data['profiles'][new] = value
                registry.data['catalog'][new] = copy.deepcopy(registry.data['catalog'].get(name, {}))
                try: apply(new)
                except Exception:
                    registry.data['profiles'].pop(new); registry.data['catalog'].pop(new, None); registry.data['meta'].pop(new, None)
                    raise
                registry.save()
            return dict(overview(), saved=new)
        except (ValueError, KeyError, TypeError) as exc:
            return fail(exc)

    @api.delete('/api/chat/profiles/{name}')
    async def remove_profile(name: str):
        try:
            with registry.lock, tasks.lock:
                source = registry.backends().get(name)
                if not source or source.get('type') not in API_TYPES: raise ValueError('Only API profiles can be removed')
                people = bound(name)
                if people: raise ValueError('还有参与者在使用这个连接：' + '、'.join(people[:5]) + '。请先给他们换一个连接，或移除这些参与者。')
                used = rooms_using(name)
                if used: raise ValueError('这些聊天里有参与者被切换到了该连接：' + '、'.join(used[:5]) + '。请先在聊天里换回。')
                tasks.cancel_backend(name)
                registry.data['profiles'].pop(name, None)
                if name in registry.base: registry.data['removed'].append(name)
                registry.data['catalog'].pop(name, None)
                registry.data['meta'].pop(name, None)
                registry.data['tests'].pop(name, None)
                apply(name)
                registry.save()
            return overview()
        except (ValueError, KeyError) as exc:
            return fail(exc)

    @api.post('/api/chat/profiles/{name}/key')
    async def set_key(name: str, request: Request):
        body = await request.json()
        try:
            with registry.lock, tasks.lock:
                if name.startswith('search_'):
                    if name not in ('search_tavily', 'search_brave', 'search_openalex'): raise ValueError('Unknown search service')
                    registry.write_key(name, body.get('key'))
                    return overview()
                source = registry.backends().get(name)
                if not source or source.get('type') not in API_TYPES: raise ValueError('Unknown API profile')
                if source.get('key_env'): raise ValueError('This profile reads its key from an environment variable; change it there')
                path = Path(source['key_file']) if source.get('key_file') else registry.key_file(name)
                key_path(str(path))
                if path.parent != registry.private: raise ValueError('Key file is outside the private key directory')
                registry.write_key(path.stem, body.get('key'))
            return overview()
        except (ValueError, KeyError, OSError) as exc:
            return fail(exc)

    @api.delete('/api/chat/profiles/{name}/key')
    async def delete_key(name: str):
        try:
            with registry.lock, tasks.lock:
                if name.startswith('search_'):
                    registry.remove_key(name)
                    return overview()
                source = registry.backends().get(name)
                if not source or not source.get('key_file'): raise ValueError('No removable key file for this profile')
                tasks.cancel_backend(name)
                registry.remove_key(Path(source['key_file']).stem)
            return overview()
        except (ValueError, KeyError, OSError) as exc:
            return fail(exc)

    @api.post('/api/chat/profiles/{name}/test')
    async def test_profile(name: str, request: Request):
        """One small real request. Recorded as 'tested', distinct from catalogue declarations and production calls."""
        import asyncio
        body = await request.json()
        runner = tasks.backends.get(name)
        if runner is None: return fail('Unknown backend', 404)
        cfg = registry.backends()[name]
        model = body.get('model') or cfg.get('model')
        try:
            if not re.fullmatch(MODEL, str(model)): raise ValueError('Invalid model ID')
            started = time.time()
            if hasattr(runner, 'quick'):
                outcome = await asyncio.to_thread(runner.quick, 'Reply with the single word: ready', model)
            else:
                outcome = await asyncio.to_thread(tasks.quick_cli, name, model)
            record = dict(ok=True, at=time.time(), seconds=round(time.time() - started, 1), reported_model=outcome.get('model', 'UNKNOWN'),
                          usage=outcome.get('usage', 'UNKNOWN'), reply=str(outcome.get('text', ''))[:80])
        except Exception as exc:
            record = dict(ok=False, at=time.time(), reason=redact(f'{type(exc).__name__}: {exc}')[:300])
        with registry.lock:
            registry.data['tests'].setdefault(name, {})[model] = record
            registry.save()
        return dict(test=redact(record), overview=overview())

    @api.post('/api/chat/cli/{name}')
    async def save_cli(name: str, request: Request):
        body = await request.json()
        try:
            with registry.lock, tasks.lock:
                import cli_adapters
                if cli_adapters.get(name) is None or name not in registry.base: raise ValueError('Unknown subscription CLI')
                changes = {k: body[k] for k in ('model', 'effort', 'timeout', 'enabled') if body.get(k) is not None}
                if 'native_search' in body:
                    if body['native_search'] not in (None, 'off'): raise ValueError('Unknown search route')
                    changes['native_search'] = body['native_search'] or 'on'
                if 'enabled' in changes and type(changes['enabled']) is not bool: raise ValueError('enabled must be true or false')
                if changes.get('enabled') is False: tasks.cancel_backend(name)
                if isinstance(body.get('label'), str) and body['label'].strip(): registry.data['meta'].setdefault(name, {})['label'] = body['label'].strip()[:40]
                if 'model' in changes and 'effort' not in changes:
                    current = registry.backends()[name].get('effort')
                    registry.data['catalog'].setdefault(name, {})
                    fitted = registry.default_effort(name, changes['model'], current)
                    if fitted != current: changes['effort'] = fitted
                if 'model' in changes and not re.fullmatch(MODEL, str(changes['model'])): raise ValueError('Invalid model ID')
                if 'effort' in changes and not re.fullmatch(r'[A-Za-z0-9_-]{1,40}', str(changes['effort'])): raise ValueError('Invalid effort')
                if 'timeout' in changes and (type(changes['timeout']) is not int or not 10 <= changes['timeout'] <= 1800): raise ValueError('Timeout: 10–1800 seconds')
                before = copy.deepcopy(registry.data)
                registry.data['cli'][name] = dict(registry.data['cli'].get(name, {}), **changes)
                try:
                    registry.seed_catalog()
                    apply(name)
                except Exception:
                    registry.data = before
                    raise
                registry.save()
            return overview()
        except (ValueError, KeyError, TypeError) as exc:
            return fail(exc)

    def put_model(backend, body):
        """Add or edit one catalogue entry (caller holds the locks and applies the backend afterwards)."""
        cfg = registry.backends().get(backend)
        if cfg is None: raise ValueError('Unknown backend')
        api_kind = cfg.get('type') in API_TYPES
        model = str(body.get('id', ''))
        if not re.fullmatch(MODEL, model): raise ValueError('模型 ID 只能包含字母、数字和 . _ : / -')
        models = registry.data['catalog'].setdefault(backend, {})
        new = model not in models
        entry = dict(models.get(model, {'added_at': time.time(), 'source': str(body.get('source', 'added by hand'))[:80]}))
        for key in ('label', 'window_source', 'note', 'description'):
            if isinstance(body.get(key), str): entry[key] = body[key][:300]
        if not entry.get('label'): entry['label'] = model
        for key in ('tools', 'images'):
            if type(body.get(key)) is bool and api_kind: entry[key] = body[key]
        if 'window_tokens' in body:
            window = body['window_tokens']
            if window in (None, ''): entry.pop('window_tokens', None)
            elif type(window) is not int or not 4096 <= window <= 2_000_000: raise ValueError('上下文窗口应在 4096 到 2000000 Token 之间')
            else:
                entry['window_tokens'] = window
                entry['window_status'] = body.get('window_status') if body.get('window_status') in ('VERIFIED', 'DOCUMENTED', 'PROVIDER-REPORTED') else 'UNKNOWN'
                if not isinstance(body.get('window_source'), str): entry['window_source'] = '手动填写'
        if 'effort_values' in body:
            levels = body['effort_values'] or []
            if not isinstance(levels, list) or any(not isinstance(x, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,40}', x) for x in levels):
                raise ValueError('Invalid effort levels')
            entry['effort_values'] = levels
            if not levels: entry.pop('effort_values')
        if 'default_effort' in body:
            if body['default_effort'] and re.fullmatch(r'[A-Za-z0-9_-]{1,40}', str(body['default_effort'])): entry['default_effort'] = body['default_effort']
            else: entry.pop('default_effort', None)
        if type(body.get('no_effort')) is bool:
            entry['no_effort'] = body['no_effort']
            if not body['no_effort']: entry.pop('no_effort')
        if type(entry.get('window_tokens')) is not int:
            # No stated window: assume one so the model can be used, and say that it is an assumption.
            same = cfg.get('context', {}) if not api_kind else {}
            entry.update(window_tokens=same.get('window_tokens', 400000) if not api_kind else 128000, window_status='UNKNOWN',
                         window_source='沿用该订阅默认模型的窗口（未核实）' if not api_kind else '默认假设 128K（未核实）')
        if api_kind and new:
            entry.setdefault('tools', False); entry.setdefault('images', False)
        models[model] = entry
        try:
            from context_budget import policy
            effective = dict(registry.backends()[backend], model=model)
            effective['context'] = effective.get('model_contexts', {}).get(model, {})
            caps = effective.get('model_capabilities', {}).get(model, {})
            policy(effective, bool(caps.get('tools', effective.get('tools'))) if api_kind else True)
        except ValueError:
            need = (cfg.get('max_output_tokens', 8192) if api_kind else 128000) + (4096 if api_kind else 32000) + 208512
            raise ValueError(f'「{entry["label"]}」的上下文窗口太小，放不下工具调用的预留空间（至少约 {need // 1000 + 1}K）。请填写它的实际窗口，或关闭工具调用。') from None
        return model

    @api.post('/api/chat/catalog/{backend}')
    async def save_model(backend: str, request: Request):
        """One model, or {"models": [...]} to add several from a discovery listing."""
        body = await request.json()
        try:
            with registry.lock, tasks.lock:
                before = copy.deepcopy(registry.data)
                try:
                    rows = body.get('models') if isinstance(body.get('models'), list) else [body]
                    if not rows or len(rows) > 200: raise ValueError('Choose 1–200 models')
                    for row in rows: put_model(backend, row)
                    apply(backend)
                except Exception:
                    registry.data = before
                    raise
                registry.save()
            return overview()
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            return fail(exc)

    # Test instances only: lets a stand-in provider on this machine be added through the normal flow. Never set in production.
    loopback_allowed = os.environ.get('MULTICHAT_ALLOW_LOOPBACK_API') == '1'

    def listing(raw, cfg):
        """A provider model listing in the shape the interface uses."""
        rows = []
        for item in raw.get('models', []):
            reported, declared = item.get('reported') or {}, item.get('reported_capabilities') or {}
            levels = reported.get('effort_values') or (declared.get('effort_values') if isinstance(declared.get('effort_values'), list) else None)
            modalities = reported.get('input_modalities')
            rows.append(dict(id=item['id'].removeprefix('models/') if cfg.get('protocol') == 'google-generate-content' else item['id'],
                             label=reported.get('display_name') or item['id'].removeprefix('models/'),
                             window_tokens=next((reported[k] for k in ('context_window', 'inputTokenLimit') if type(reported.get(k)) is int), None),
                             effort_values=levels, images=('image' in modalities) if isinstance(modalities, list) else (declared.get('images') if type(declared.get('images')) is bool else None)))
        source = str(raw.get('source', ''))
        denied = 'HTTP 401' in source or 'HTTP 403' in source
        reason = {'BLOCKED': '这个连接还没有可用的密钥，或已停用。',
                  'UNKNOWN': '服务商拒绝了这个密钥（401/403）。' if denied else '服务商没有提供模型列表（' + source[:80] + '）。'}.get(raw.get('status'))
        return dict(models=rows, ok=raw.get('status') == 'VERIFIED', reason=reason, auth=False if denied else None, source='服务商的 /models 接口')

    @api.post('/api/chat/connections/probe')
    async def probe(request: Request):
        """Before a connection is saved: check the address and key by asking for the model list. Nothing is stored."""
        from urllib.parse import urlsplit
        from chat_api import fetch_models
        import api_protocols
        body = await request.json()
        try:
            protocol = body.get('protocol')
            if protocol not in api_protocols.PROTOCOLS: raise ValueError('Unknown protocol')
            base = str(body.get('base_url') or '').strip()
            url = urlsplit(base)
            local = loopback_allowed and url.scheme == 'http' and url.hostname in ('127.0.0.1', '::1')
            if (url.scheme != 'https' and not local) or not url.hostname or url.username or url.password or url.query or url.fragment:
                raise ValueError('API requires HTTPS; base URL must not contain credentials, query or fragment')
            key = str(body.get('key') or '').strip()
            if len(key) < 8 or len(key) > 4096 or any(c.isspace() for c in key): raise ValueError('Invalid API key format')
            remember(key)
            cfg = dict(type='api', protocol=protocol, base_url=base, timeout=15)
            return redact(listing(await fetch_models(cfg, key), cfg))
        except (ValueError, KeyError, TypeError) as exc:
            return fail(exc)

    @api.post('/api/chat/connections/{name}/discover')
    async def discover(name: str):
        """Ask the provider (or the installed CLI) which models it offers. Listing only: nothing is added, no model is called."""
        import asyncio
        runner, cfg = tasks.backends.get(name), registry.backends().get(name)
        if runner is None or cfg is None: return fail('Unknown connection', 404)
        if hasattr(runner, 'list_models'):
            result = listing(await runner.list_models(), cfg)
        else:
            from model_discovery import cli_models
            result = await asyncio.to_thread(cli_models, name, runner.config)
        existing = registry.data['catalog'].get(name, {})
        for row in result['models']: row['added'] = row['id'] in existing
        return redact(result)

    @api.post('/api/chat/participants')
    async def save_participant(request: Request):
        """Create or edit a chat participant: a stable id, a name, and the connection + model it speaks through."""
        body = await request.json()
        try:
            with registry.lock, tasks.lock:
                before = copy.deepcopy(registry.data)
                pid = body.get('id')
                rows = {row['id']: row for row in registry.participants()}
                if pid is None:
                    label = str(body.get('label') or '').strip()
                    if not 1 <= len(label) <= 40: raise ValueError('给参与者起个名字（1–40 个字符）')
                    taken = set(IDENTITIES) | RESERVED | {str(app_module.room_settings.get('username', 'user')).lower()}
                    pid = slug(body.get('slug') or label, taken, 'p-' + uuid.uuid4().hex[:6])
                    used = {row['color'] for row in rows.values()}
                    saved = dict(created_at=time.time(), color=next((c for c in PALETTE if c not in used), PALETTE[len(rows) % len(PALETTE)]))
                elif pid in rows:
                    saved = dict(registry.data['participants'].get(pid, {}))
                else: raise ValueError('Unknown participant')
                for key in ('label', 'color', 'connection', 'model', 'effort', 'thinking', 'hidden', 'retired'):
                    if key not in body: continue
                    value = body[key]
                    if value in (None, ''): saved.pop(key, None); continue
                    if key == 'label':
                        value = str(value).strip()
                        if not 1 <= len(value) <= 40: raise ValueError('名字需要 1–40 个字符')
                        if any(row['label'] == value and row['id'] != pid and not row['retired'] for row in rows.values()): raise ValueError('已经有同名的参与者')
                    elif key == 'color':
                        if not re.fullmatch(r'#[0-9a-fA-F]{6}', str(value)): raise ValueError('Invalid colour')
                    elif key == 'connection':
                        if value != 'legacy' and value not in registry.backends(): raise ValueError('Unknown connection')
                    elif key in ('hidden', 'retired'):
                        if type(value) is not bool: raise ValueError(key + ' must be true or false')
                        if key == 'retired' and pid in BUILTIN: raise ValueError('内置参与者不能移除，可以改为不在选择器中显示')
                        if not value: saved.pop(key, None); continue
                    saved[key] = value
                registry.data['participants'][pid] = saved
                registry.refresh()
                try:
                    row = registry.participant(pid)
                    if not row['retired']:
                        if not row['connection']: raise ValueError('选择这个参与者使用的连接')
                        if row['connection'] != 'legacy' and row['model'] is not None:
                            fitted = registry.default_effort(row['connection'], row['model'], row['effort'] or registry.backends()[row['connection']].get('effort'))
                            if row['effort'] is None and fitted != registry.backends()[row['connection']].get('effort'): saved['effort'] = fitted
                        row = registry.participant(pid)
                        settings = {k: row[k] for k in ('model', 'effort', 'thinking') if row[k] is not None}
                        tasks.validate_switch(pid, row['connection'], settings or None, require_available=False)
                    else:
                        tasks.cancel(model=pid)
                except Exception:
                    registry.data = before
                    registry.refresh()
                    raise
                registry.save()
            await announce()
            return dict(id=pid, overview=overview())
        except (ValueError, KeyError, TypeError) as exc:
            return fail(exc)

    @api.delete('/api/chat/participants/{pid}')
    async def remove_participant(pid: str):
        """Remove a participant from chats. If it ever spoke or was addressed, its record is kept (retired) so history keeps its name."""
        try:
            if pid in BUILTIN: raise ValueError('内置参与者不能移除，可以改为不在选择器中显示')
            with registry.lock, tasks.lock:
                if pid not in registry.data['participants']: raise ValueError('Unknown participant')
                tasks.cancel(model=pid)
                def mentioned(message):
                    meta = message.get('metadata') or {}
                    audience = meta.get('visibility')
                    return message.get('sender') == pid or pid in (meta.get('targets') or []) or (isinstance(audience, list) and pid in audience)
                if any(mentioned(m) for m in app_module.store.get_since(-1)):
                    registry.data['participants'][pid]['retired'] = True
                    kept = True
                else:
                    registry.data['participants'].pop(pid)
                    kept = False
                registry.data['combos'] = [c for c in registry.data['combos'] if pid not in c.get('items', {})]
                registry.refresh()
                registry.save()
            await announce()
            return dict(kept_for_history=kept, overview=overview())
        except (ValueError, KeyError) as exc:
            return fail(exc)

    @api.post('/api/chat/catalog/{backend}/remove')
    async def remove_model(backend: str, request: Request):
        body = await request.json()
        try:
            with registry.lock, tasks.lock:
                model = body.get('id')
                if registry.backends().get(backend, {}).get('model') == model: raise ValueError('This is the backend\'s current default model')
                registry.data['catalog'].get(backend, {}).pop(model, None)
                apply(backend)
                registry.save()
            return overview()
        except (ValueError, KeyError) as exc:
            return fail(exc)

    @api.post('/api/chat/combos')
    async def save_combo(request: Request):
        body = await request.json()
        try:
            name = str(body.get('name', '')).strip()
            if not 1 <= len(name) <= 40: raise ValueError('Name the combination (1–40 characters)')
            items = body.get('items')
            if not isinstance(items, dict) or not items or set(items) - set(IDENTITIES): raise ValueError('Choose at least one participant')
            with registry.lock:
                for identity, value in items.items():
                    backend = value.get('backend')
                    tasks.validate_switch(identity, backend, {k: value[k] for k in ('model', 'effort', 'thinking') if value.get(k) is not None})
                registry.data['combos'] = [c for c in registry.data['combos'] if c['name'] != name][-19:] + [
                    dict(id=uuid.uuid4().hex[:12], name=name, items=items, created_at=time.time())]
                registry.save()
            return overview()
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            return fail(exc)

    @api.delete('/api/chat/combos/{combo_id}')
    async def remove_combo(combo_id: str):
        with registry.lock:
            registry.data['combos'] = [c for c in registry.data['combos'] if c['id'] != combo_id]
            registry.save()
        return overview()

    @api.post('/api/chat/defaults')
    async def save_default(request: Request):
        body = await request.json()
        try:
            with registry.lock:
                tasks.validate_switch(body['identity'], body['backend'])
                registry.data['participants'].setdefault(body['identity'], {})['connection'] = body['backend']
                registry.save()
            return overview()
        except (ValueError, KeyError) as exc:
            return fail(exc)

    @api.post('/api/chat/lenses')
    async def save_lens(request: Request):
        body = await request.json()
        try:
            name, method = str(body.get('name', '')).strip(), str(body.get('method', '')).strip()
            if not 1 <= len(name) <= 40 or not 1 <= len(method) <= 4000: raise ValueError('A lens needs a name (≤40) and a method description (≤4000 characters)')
            if redact(method) != method: raise ValueError('Secret values cannot be stored in a lens')
            with registry.lock:
                lens_id = body.get('id')
                if lens_id in LENSES: raise ValueError('Built-in lenses cannot be changed; duplicate one instead')
                if lens_id not in registry.data['lenses']:
                    if len(registry.data['lenses']) >= 40: raise ValueError('Too many custom lenses')
                    lens_id = 'c' + uuid.uuid4().hex[:10]
                registry.data['lenses'][lens_id] = dict(name=name, method=method, summary=str(body.get('summary', ''))[:200], updated_at=time.time())
                registry.save()
            return dict(id=lens_id, overview=overview())
        except (ValueError, KeyError) as exc:
            return fail(exc)

    @api.delete('/api/chat/lenses/{lens_id}')
    async def remove_lens(lens_id: str):
        with registry.lock:
            registry.data['lenses'].pop(lens_id, None)
            registry.save()
        return overview()

    @api.post('/api/chat/search-test')
    async def search_test(request: Request):
        import asyncio
        body = await request.json()
        try:
            run = runtime.evidence.web_search if body.get('kind') == 'web' else runtime.evidence.scholar_search
            return redact(await asyncio.to_thread(run, str(body.get('query', '')), 6))
        except Exception as exc:
            return fail(type(exc).__name__ + ': ' + str(exc)[:200])

    @api.post('/api/chat/search-settings')
    async def save_search(request: Request):
        body = await request.json()
        try:
            with registry.lock:
                if 'web_provider' in body:
                    if body['web_provider'] not in ('duckduckgo', 'tavily', 'brave'): raise ValueError('Unknown search provider')
                    registry.data['search']['web_provider'] = body['web_provider']
                if type(body.get('reader_fallback')) is bool: registry.data['search']['reader_fallback'] = body['reader_fallback']
                if 'planner' in body:
                    if body['planner'] not in ('auto', 'off') and body['planner'] not in registry.backends(): raise ValueError('Unknown planner backend')
                    registry.data['search']['planner'] = body['planner']
                registry.save()
            return overview()
        except (ValueError, KeyError) as exc:
            return fail(exc)
