"""Codex (OpenAI's official CLI), `codex exec --json` in a read-only sandbox."""
import json
from pathlib import Path

from .base import CLIAdapter, exchange, status_command


class Codex(CLIAdapter):
    name = 'codex'
    label = 'Codex'
    commands = ('codex',)
    effort_values = ['low', 'medium', 'high', 'xhigh']
    capabilities = dict(
        images=(True, 'tested: --image for attached pictures (adapter test + recorded calls)'),
        files=(True, 'tested: attachments arrive as extracted text through the room tool server'),
        search=(True, 'provider: web_search="live", enabled only for replies started with native search'),
        context_window=('UNKNOWN', 'reported per model by the model catalogue; not assumed'))

    def default_locations(self):
        native = r'node_modules\@openai\codex\node_modules\@openai\codex-win32-x64\vendor\x86_64-pc-windows-msvc\bin\codex.exe'
        return [r'%APPDATA%\npm\\' + native, r'%LOCALAPPDATA%\Programs\codex\codex.exe', r'%USERPROFILE%\.local\bin\codex.exe']

    def resolve(self, path):
        """The npm `codex` shim starts Node; the package ships the real executable, which is what gets called."""
        if path.suffix.lower() in ('.ps1', '.cmd', '') and path.parent.name.lower() == 'npm':
            native = path.parent / 'node_modules/@openai/codex/node_modules/@openai/codex-win32-x64/vendor/x86_64-pc-windows-msvc/bin/codex.exe'
            if native.is_file(): return native
        return super().resolve(path)

    # ---- Model List ----
    def list_models(self, cfg, timeout=40):
        """The app server's `model/list` method, following nextCursor. No model call."""
        rows, cursor = [], None
        for _ in range(8):
            reply = exchange([cfg['executable'], 'app-server'],
                             [{'method': 'initialize', 'id': 0, 'params': {'clientInfo': {'name': 'chattera', 'title': 'Chattera', 'version': '1'}}},
                              {'method': 'initialized'}, {'method': 'model/list', 'id': 1, 'params': {'cursor': cursor} if cursor else {}}],
                             lambda item: item.get('id') == 1, timeout, None, cfg.get('secret_env_names', []))
            page = ((reply or {}).get('result') or {}).get('data')
            if not isinstance(page, list): raise ValueError('no model list in the app-server reply')
            rows += page
            cursor = reply['result'].get('nextCursor')
            if not cursor: break
        result = []
        for row in rows:
            if row.get('hidden') or not isinstance(row.get('model'), str): continue
            levels = [x.get('reasoningEffort') for x in row.get('supportedReasoningEfforts') or [] if isinstance(x.get('reasoningEffort'), str)]
            result.append(dict(id=row['model'], label=str(row.get('displayName') or row['model'])[:80], description=str(row.get('description') or '')[:200],
                               effort_values=levels, no_effort=not levels, default_effort=row.get('defaultReasoningEffort'),
                               is_default=bool(row.get('isDefault')), images='image' in (row.get('inputModalities') or [])))
        return result

    # ---- Authentication ----
    def auth_status(self, cfg):
        """`codex login status`: exit 0 and "Logged in using …" when signed in. Only the method word is kept."""
        try:
            code, out = status_command([cfg['executable'], 'login', 'status'], secrets=cfg.get('secret_env_names', []))
        except Exception as exc:
            return dict(status='UNKNOWN', method=None, source='codex login status failed: ' + type(exc).__name__)
        text = out.strip().splitlines()[0] if out.strip() else ''
        if code == 0 and text.lower().startswith('logged in'):
            method = 'ChatGPT' if 'chatgpt' in text.lower() else 'API key' if 'api key' in text.lower() else None
            return dict(status='signed-in', method=method, source='codex login status')
        if text.lower().startswith('not logged in'):
            return dict(status='signed-out', method=None, source='codex login status')
        return dict(status='UNKNOWN', method=None, source='codex login status gave an unrecognised answer')

    # ---- Generate ----
    def command(self, cfg, directory, mcp, baseline=False, search=False):
        args = [cfg['executable'], 'exec', '--ignore-user-config', '--ephemeral', '--skip-git-repo-check',
                '--json', '-s', 'read-only', '-m', cfg['model'], '-c', 'approval_policy="never"',
                '-c', 'forced_login_method="chatgpt"', '-c', 'web_search="live"' if search else 'web_search="disabled"',
                '-c', 'model_reasoning_effort=' + json.dumps(cfg.get('effort', 'medium')),
                '-c', 'project_doc_max_bytes=0']
        for feature in ('shell_tool', 'apps', 'plugins', 'shell_snapshot', 'memories'):
            args.extend(['--disable', feature])
        if cfg.get('context', {}).get('model') == cfg.get('model') and cfg.get('context', {}).get('window_tokens'):
            args.extend(['-c', 'model_context_window=' + str(cfg['context']['window_tokens'])])
        if not baseline:
            for key, value in dict(command=mcp['command'], args=mcp['args'], enabled_tools=list(self.room_tools), required=True).items():
                args.extend(['-c', 'mcp_servers.room.' + key + '=' + json.dumps(value)])
            args.extend(['-c', 'mcp_servers.room.default_tools_approval_mode="approve"'])
        return args + ['-']

    def prepare_input(self, cfg, command, prompt, payload, snapshot):
        command = list(command)
        for item in payload.get('attachment_evidence', []):
            if item.get('kind') == 'image':
                value = snapshot['attachment_content'][f"{item['message_id']}:{item['index']}"]
                command[-1:-1] = ['--image', value['image_path']]
        return command, prompt.encode('utf-8')

    def tool_event(self, event):
        return int(event.get('type') == 'item.started' and event.get('item', {}).get('type') in ('mcp_tool_call', 'web_search'))

    def parse(self, cfg, output):
        events = self.json_lines(output)
        if any(e.get('type') == 'turn.failed' for e in events):
            raise RuntimeError('Codex turn failed')
        messages = [e['item']['text'] for e in events if e.get('type') == 'item.completed'
                    and e.get('item', {}).get('type') == 'agent_message']
        completed = next((e for e in reversed(events) if e.get('type') == 'turn.completed'), None)
        if not completed or not messages:
            raise RuntimeError('Codex did not return a completed final result')
        return dict(text=messages[-1], usage=completed.get('usage', 'UNKNOWN'), usage_fields=self.usage(completed.get('usage')),
                    model='UNKNOWN', requested_model=cfg['model'], rate_limits='UNKNOWN',
                    tool_calls=[e['item'].get('tool') for e in events if e.get('type') == 'item.completed'
                                and e.get('item', {}).get('type') == 'mcp_tool_call'])

    # ---- Usage ----
    def usage(self, raw):
        raw = raw if isinstance(raw, dict) else {}
        return dict(input_tokens=raw.get('input_tokens', 'UNKNOWN'), cache_creation_input_tokens=raw.get('cache_write_input_tokens', 'UNKNOWN'),
                    cache_read_input_tokens=raw.get('cached_input_tokens', 'UNKNOWN'), output_tokens=raw.get('output_tokens', 'UNKNOWN'),
                    input_semantics='includes cached input; do not add cache again', cache_creation_detail=raw.get('cache_creation', 'UNKNOWN'),
                    subscription_debit='UNKNOWN', billed_amount='UNKNOWN')
