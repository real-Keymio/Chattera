"""Claude Code (Anthropic's official CLI), print mode with stream-json output."""
import json
import tempfile

from .base import CLIAdapter, exchange, status_command


class ClaudeCode(CLIAdapter):
    name = 'claude'
    label = 'Claude Code'
    commands = ('claude',)
    effort_values = ['low', 'medium', 'high', 'xhigh', 'max']
    capabilities = dict(
        images=(True, 'tested: --input-format stream-json with base64 image blocks (adapter test + recorded calls)'),
        files=(True, 'tested: attachments arrive as extracted text through the room tool server'),
        search=(True, 'provider: built-in WebSearch / WebFetch tools, enabled only for replies started with native search'),
        context_window=('UNKNOWN', 'reported per model by the model catalogue; not assumed'))

    def default_locations(self):
        # Standalone installs first. The copy bundled with the Claude desktop app comes last: it works on its own once it
        # has been signed in (`claude.exe auth login`; the sign-in is shared through %USERPROFILE%\.claude), but its
        # folder changes with every app update, so a missing path is looked up again at the next start.
        return [r'%USERPROFILE%\.local\bin\claude.exe',             # native installer
                r'%LOCALAPPDATA%\Programs\claude\claude.exe',
                r'%APPDATA%\npm\claude.cmd',                         # npm global install
                r'%LOCALAPPDATA%\Claude*\claude-code\*\*\claude.exe']  # copy bundled with the Claude desktop app

    def failure_reason(self, output):
        """The CLI's own error text from a failed print-mode run (e.g. "Failed to authenticate: …"), or None."""
        for line in output.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict) and event.get('type') == 'result' and event.get('is_error') and isinstance(event.get('result'), str):
                return event['result']
        return None

    # ---- Model List ----
    def list_models(self, cfg, timeout=40):
        """The reply to the SDK control channel's `initialize` request (the list its own model picker shows). No model call."""
        with tempfile.TemporaryDirectory() as folder:
            reply = exchange([cfg['executable'], '-p', '--input-format', 'stream-json', '--output-format', 'stream-json', '--verbose',
                              '--no-session-persistence', '--tools=', '--strict-mcp-config', '--setting-sources', 'project', '--disable-slash-commands'],
                             [{'type': 'control_request', 'request_id': 'models', 'request': {'subtype': 'initialize'}}],
                             lambda item: item.get('type') == 'control_response', timeout, folder, cfg.get('secret_env_names', []))
        rows = ((reply or {}).get('response') or {}).get('response', {}).get('models')
        if not isinstance(rows, list): raise ValueError('no model list in the CLI reply')
        merged = {}
        for row in rows:
            model = row.get('resolvedModel') or row.get('value')
            if not isinstance(model, str): continue
            entry = merged.setdefault(model, dict(id=model, label=None, description='', aliases=[], is_default=False))
            if row.get('value') == 'default': entry['is_default'] = True
            elif row.get('value') != model: entry['aliases'].append(row['value'])
            name = row.get('displayName', '')
            if name.startswith('Default'): name = row.get('description', '')
            elif not entry['description']: entry['description'] = str(row.get('description', ''))[:200]
            if name and (entry['label'] is None or row.get('value') == model): entry['label'] = str(name)[:80]
            levels = row.get('supportedEffortLevels') if row.get('supportsEffort') else []
            entry['effort_values'] = [x for x in levels if isinstance(x, str)] if isinstance(levels, list) else []
            entry['no_effort'] = not entry['effort_values']
        return [dict(entry, label=entry['label'] or entry['id']) for entry in merged.values()]

    # ---- Authentication ----
    def auth_status(self, cfg):
        """`claude auth status` prints JSON; only `loggedIn` and `authMethod` are read."""
        try:
            code, out = status_command([cfg['executable'], 'auth', 'status'], secrets=cfg.get('secret_env_names', []))
            start = out.find('{')
            value = json.loads(out[start:out.rfind('}') + 1]) if start >= 0 else {}
        except Exception as exc:
            return dict(status='UNKNOWN', method=None, source='claude auth status failed: ' + type(exc).__name__)
        if not isinstance(value.get('loggedIn'), bool):
            return dict(status='UNKNOWN', method=None, source='claude auth status gave no loggedIn field')
        method = value.get('authMethod') if isinstance(value.get('authMethod'), str) else None
        return dict(status='signed-in' if value['loggedIn'] else 'signed-out', method=method, source='claude auth status')

    # ---- Generate ----
    def command(self, cfg, directory, mcp, baseline=False, search=False):
        path = directory / 'mcp.json'
        path.write_text(json.dumps({'mcpServers': {} if baseline else {'room': mcp}}), 'utf-8')
        return [cfg['executable'], '-p', '--output-format', 'stream-json', '--verbose', '--include-partial-messages',
                '--no-session-persistence', '--restricted', '--strict-mcp-config',
                '--mcp-config', str(path), *(['--tools', 'WebSearch,WebFetch'] if search else ['--tools=']), '--permission-mode', 'dontAsk',
                '--permission-prompts', 'none', '--disable-slash-commands', '--no-chrome',
                '--setting-sources', 'project', '--settings', '{"disableAllHooks":true}',
                '--model', cfg['model'], '--effort', cfg.get('effort', 'medium'),
                '--allowedTools', ','.join(['mcp__room__' + t for t in self.room_tools] + (['WebSearch', 'WebFetch'] if search else []))]

    def prepare_input(self, cfg, command, prompt, payload, snapshot):
        if not any(item.get('kind') == 'image' for item in payload.get('attachment_evidence', [])):
            return command, prompt.encode('utf-8')
        # --input-format stream-json accepts one user message whose content holds the text prompt plus base64 image blocks.
        from chat_attachments import image_blocks
        content = [{'type': 'text', 'text': prompt}]
        for block in image_blocks(snapshot, payload):
            header, data = block['image_url']['url'].split(',', 1)
            content.append({'type': 'image', 'source': {'type': 'base64', 'media_type': header[5:].split(';')[0], 'data': data}})
        command = list(command)
        position = command.index('--output-format')
        command[position:position] = ['--input-format', 'stream-json']
        return command, (json.dumps({'type': 'user', 'message': {'role': 'user', 'content': content}}) + '\n').encode('utf-8')

    def tool_event(self, event):
        if event.get('type') == 'assistant':
            return sum(b.get('type') == 'tool_use' for b in event.get('message', {}).get('content', []))
        return 0

    def progress_event(self, event):
        nested = event.get('event', {})
        return event.get('type') == 'stream_event' and nested.get('delta', {}).get('type') == 'text_delta'

    def parse(self, cfg, output):
        events = self.json_lines(output)
        final = next((e for e in reversed(events) if e.get('type') == 'result'), {})
        if final.get('is_error') or not final.get('result'):
            raise RuntimeError('Claude did not return a successful final result')
        init = next((e for e in events if e.get('type') == 'system' and e.get('subtype') == 'init'), {})
        return dict(text=final['result'], usage=final.get('usage', 'UNKNOWN'), usage_fields=self.usage(final.get('usage')),
                    model=init.get('model', 'UNKNOWN'), requested_model=cfg['model'], model_usage=final.get('modelUsage', 'UNKNOWN'),
                    tools=init.get('tools', []), mcp=init.get('mcp_servers', []),
                    tool_calls=[b.get('name') for e in events if e.get('type') == 'assistant'
                                for b in e.get('message', {}).get('content', []) if b.get('type') == 'tool_use'],
                    rate_limits=final.get('rate_limit_info', 'UNKNOWN'))

    # ---- Usage ----
    def usage(self, raw):
        raw = raw if isinstance(raw, dict) else {}
        return dict(input_tokens=raw.get('input_tokens', 'UNKNOWN'), cache_creation_input_tokens=raw.get('cache_creation_input_tokens', 'UNKNOWN'),
                    cache_read_input_tokens=raw.get('cache_read_input_tokens', 'UNKNOWN'), output_tokens=raw.get('output_tokens', 'UNKNOWN'),
                    input_semantics='excludes cache fields', cache_creation_detail=raw.get('cache_creation', 'UNKNOWN'),
                    subscription_debit='UNKNOWN', billed_amount='UNKNOWN')
