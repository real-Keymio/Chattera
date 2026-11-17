"""Web search inside one reply: which path a participant uses, and one uniform record of what was searched and opened.

Paths
  native             the connection's own official search (Claude Code WebSearch/WebFetch, Codex web_search, DeepSeek server
                     tools, Google Search grounding, an OpenAI-style `web_search` tool)
  multichat-tools    the model calls Chattera's web_search / web_read / research_* tools itself
  multichat-planned  the model has no tool calling: it names its own queries first, Chattera runs them, only it receives the results
  none               external search is not allowed for this reply (private context)

The record is a list of entries: {kind: search|read|error, at, via, query|queries, results:[{title,url,snippet,site,published}], url, level}.
Nothing here is derived from the answer text: every entry comes from a tool event or a provider response field.
"""
import datetime
import json
import time
from urllib.parse import urlsplit

API_TYPES = {'openai-compatible', 'api'}
NATIVE = ('deepseek', 'web_search_tool', 'google')
LABEL = {'claude': 'Claude Code WebSearch / WebFetch', 'codex': 'Codex web_search', 'deepseek': 'DeepSeek deepseek_web_search / deepseek_read_url',
         'google': 'Google Search grounding', 'web_search_tool': 'provider web_search tool'}


def clip(value, size):
    value = '' if value is None else str(value)
    return value if len(value) <= size else value[:size] + '…'


def source(row):
    """One search result or cited page, keeping only what the provider actually returned."""
    if not isinstance(row, dict): return None
    url = row.get('url') or row.get('uri')
    if not isinstance(url, str) or not url.startswith(('http://', 'https://')): return None
    item = dict(url=clip(url, 600), title=clip(row.get('title') or '', 200))
    for key, names in (('snippet', ('snippet', 'summary', 'cited_text')), ('site', ('site_name',)), ('published', ('publish_time', 'page_age', 'date'))):
        value = next((row[n] for n in names if row.get(n)), None)
        if value: item[key] = clip(value, 300)
    return item


def native_kind(name, cfg):
    """The official search this connection offers, or None. Declared per connection; never inferred from a model name."""
    if cfg.get('type') not in API_TYPES:
        return name if name in ('claude', 'codex') and cfg.get('native_search') != 'off' else None
    chosen = cfg.get('native_search')
    if chosen == 'off': return None
    if chosen in NATIVE: return chosen
    protocol = cfg.get('protocol', 'openai-chat')
    if protocol == 'google-generate-content': return 'google'
    if protocol == 'openai-responses' and (urlsplit(cfg.get('base_url', '')).hostname or '') == 'api.openai.com': return 'web_search_tool'
    if protocol == 'openai-chat' and (urlsplit(cfg.get('base_url', '')).hostname or '') == 'api.deepseek.com': return 'deepseek'
    return None


def plan(name, cfg, tools, allowed, mode):
    """Frozen into the snapshot when the reply starts, so a regeneration searches the same way."""
    if not allowed:
        return dict(mode=mode, path='none', reason='private context: nothing from this conversation may be sent to a search service')
    kind = native_kind(name, cfg)
    if kind: return dict(mode=mode, path='native', native=kind, label=LABEL.get(kind, kind))
    if tools: return dict(mode=mode, path='multichat-tools', label='Chattera web_search / web_read')
    return dict(mode=mode, path='multichat-planned', label="Chattera search with this model's own queries")


def instruction(search, has_shared):
    """What the model is told about searching in this reply."""
    today = datetime.date.today().isoformat()
    if search['path'] == 'none':
        return dict(today=today, web_search='not available in this reply: the context is private, so nothing may be sent to a search service. '
                                            'Answer from what is here and say what you could not check.')
    how = {'native': 'your own built-in web search and page reading',
           'multichat-tools': 'the web_search, web_read, research_search and research_read tools',
           'multichat-planned': 'the search results under own_search, retrieved with the queries you chose a moment ago; nobody else received them'}[search['path']]
    text = (f'Today is {today}. For this reply you can look things up with {how}. Search independently: choose your own queries, follow the sources you find worth reading, '
            'and form your own judgement — other participants search separately and may reach different sources. Cite a source only if it actually came back from a search or '
            'page you opened in this reply (give its URL), keep what you read apart from what you recall, and say so when a search fails or finds nothing. '
            'Searching is for answering this message; do not plan further tasks.')
    if has_shared:
        text += ' The shared_evidence block is a common baseline every participant received; go beyond it where it helps, and say when your own findings differ from it.'
    return dict(today=today, web_search=text)


class CliTrace:
    """Builds the record from a CLI's event stream as the events arrive (so each entry has a real time)."""
    def __init__(self, name, notify=None):
        self.name, self.entries, self.pending, self.notify = name, [], {}, notify

    def add(self, entry):
        if len(self.entries) < 60: self.entries.append(entry)
        if self.notify:
            try: self.notify(entry)
            except Exception: pass

    def feed(self, event):
        now = time.time()
        try:
            if self.name == 'claude': self.claude(event, now)
            else: self.codex(event, now)
        except (AttributeError, KeyError, TypeError, ValueError):
            pass

    def claude(self, event, now):
        blocks = (event.get('message') or {}).get('content')
        if not isinstance(blocks, list): return
        if event.get('type') == 'assistant':
            for block in blocks:
                if block.get('type') != 'tool_use' or block.get('name') not in ('WebSearch', 'WebFetch'): continue
                args = block.get('input') or {}
                entry = (dict(kind='search', at=now, via='Claude Code WebSearch', query=clip(args.get('query'), 300), results=[]) if block['name'] == 'WebSearch'
                         else dict(kind='read', at=now, via='Claude Code WebFetch', url=clip(args.get('url'), 600), level='page read by the CLI'))
                self.pending[block.get('id')] = entry
                self.add(entry)
        elif event.get('type') == 'user':
            detail = event.get('tool_use_result')
            for block in blocks:
                entry = self.pending.pop(block.get('tool_use_id'), None) if block.get('type') == 'tool_result' else None
                if entry is None: continue
                entry['seconds'] = round(now - entry['at'], 1)
                if block.get('is_error'):
                    text = block.get('content')
                    entry['error'] = clip(text if isinstance(text, str) else json.dumps(text, ensure_ascii=False), 240)
                elif entry['kind'] == 'search' and isinstance(detail, dict):
                    for group in detail.get('results') or []:
                        for row in (group.get('content') or []) if isinstance(group, dict) else []:
                            item = source(row)
                            if item and len(entry['results']) < 20: entry['results'].append(item)
                elif entry['kind'] == 'read' and isinstance(detail, dict):
                    for key in ('code', 'bytes'):
                        if isinstance(detail.get(key), int): entry[key] = detail[key]

    def codex(self, event, now):
        item = event.get('item') or {}
        if event.get('type') != 'item.completed' or item.get('type') != 'web_search': return
        action = item.get('action') or {}
        kind = action.get('type')
        if kind == 'search':
            queries = [clip(q, 300) for q in action.get('queries') or ([item['query']] if item.get('query') else []) if isinstance(q, str)]
            if queries: self.add(dict(kind='search', at=now, via='Codex web_search', queries=queries, results=[],
                                      note='Codex reports its queries and the pages it opens, not the result list'))
        elif kind in ('open_page', 'find_in_page') and isinstance(action.get('url'), str):
            entry = dict(kind='read', at=now, via='Codex web_search', url=clip(action['url'], 600), level='page opened by the CLI')
            if action.get('pattern'): entry['find'] = clip(action['pattern'], 120)
            self.add(entry)


def from_openai(message, usage, via):
    """DeepSeek server tools and OpenAI-style web_search annotations in a chat.completions message."""
    entries, citations, now = [], [], time.time()
    for group in message.get('server_tool_calls') or []:
        for call in (group.get('calls') or []) if isinstance(group, dict) else []:
            action = call.get('action') or {}
            rows = [s for s in (source(r) for r in call.get('sources') or []) if s][:20]
            if 'search' in str(call.get('type')):
                entry = dict(kind='search', at=now, via=via, queries=[clip(q, 300) for q in action.get('queries') or [] if isinstance(q, str)], results=rows)
                if call.get('status') not in (None, 'completed'): entry['error'] = clip(call.get('status'), 80)
                entries.append(entry)
            else:
                for row in rows or ([dict(url=clip(action.get('url'), 600), title='')] if isinstance(action.get('url'), str) else []):
                    entries.append(dict(kind='read', at=now, via=via, url=row['url'], title=row.get('title', ''), level='page read by the provider'))
    for note in message.get('annotations') or []:
        if not isinstance(note, dict): continue
        row = note.get('url_citation') if isinstance(note.get('url_citation'), dict) else note
        item = source(row)
        if item and len(citations) < 60:
            for key in ('start_index', 'end_index'):
                if isinstance(row.get(key), int): item[key.split('_')[0]] = row[key]
            citations.append(item)
    if citations and not entries:
        seen = {}
        for item in citations: seen.setdefault(item['url'], {k: v for k, v in item.items() if k not in ('start', 'end')})
        entries.append(dict(kind='search', at=now, via=via, queries=[], results=list(seen.values())[:20], note='the provider returned its sources but not the queries it ran'))
    count = (usage or {}).get('web_search_usage')
    return entries, citations, count if isinstance(count, dict) else None


def from_responses(items, via):
    """web_search_call items and url_citation annotations in an OpenAI Responses output."""
    entries, citations, now = [], [], time.time()
    for item in items:
        if not isinstance(item, dict): continue
        if item.get('type') == 'web_search_call':
            action = item.get('action') or {}
            kind = action.get('type')
            if kind == 'open_page' and isinstance(action.get('url'), str):
                entries.append(dict(kind='read', at=now, via=via, url=clip(action['url'], 600), title='', level='page read by the provider'))
                continue
            queries = action.get('queries') or ([action['query']] if isinstance(action.get('query'), str) else [])
            entry = dict(kind='search', at=now, via=via, queries=[clip(q, 300) for q in queries if isinstance(q, str)],
                         results=[s for s in (source(r) for r in action.get('sources') or []) if s][:20])
            if item.get('status') not in (None, 'completed'): entry['error'] = clip(item.get('status'), 80)
            entries.append(entry)
        elif item.get('type') == 'message':
            for part in item.get('content') or []:
                for note in (part.get('annotations') or []) if isinstance(part, dict) else []:
                    found = source(note) if isinstance(note, dict) and note.get('type') == 'url_citation' else None
                    if found and len(citations) < 60:
                        for key in ('start_index', 'end_index'):
                            if isinstance(note.get(key), int): found[key.split('_')[0]] = note[key]
                        citations.append(found)
    if citations and not entries:
        seen = {}
        for item in citations: seen.setdefault(item['url'], {k: v for k, v in item.items() if k not in ('start', 'end')})
        entries.append(dict(kind='search', at=now, via=via, queries=[], results=list(seen.values())[:20], note='the provider returned its sources but not the queries it ran'))
    return entries, citations


def from_google(candidate):
    """Google Search grounding metadata of one candidate."""
    meta, now = candidate.get('groundingMetadata') or {}, time.time()
    chunks = [source(c.get('web') or {}) for c in meta.get('groundingChunks') or []]
    if not chunks and not meta.get('webSearchQueries'): return [], []
    entry = dict(kind='search', at=now, via='Google Search grounding', queries=[clip(q, 300) for q in meta.get('webSearchQueries') or [] if isinstance(q, str)],
                 results=[c for c in chunks if c][:20], note='Google returns redirect links and site names; page titles are not provided')
    citations = []
    for support in meta.get('groundingSupports') or []:
        segment = support.get('segment') or {}
        for index in support.get('groundingChunkIndices') or []:
            if isinstance(index, int) and 0 <= index < len(chunks) and chunks[index] and len(citations) < 60:
                citations.append(dict(chunks[index], start=segment.get('startIndex', 0), end=segment.get('endIndex')))
    return [entry], citations


def from_tool_events(events):
    """Chattera's own tools, from the per-call audit log."""
    entries = []
    for event in events:
        tool, info, args = event.get('tool'), event.get('summary') or {}, event.get('args') or {}
        if tool in ('library_read', 'library_view_page') and event.get('status') == 'success':
            entries.append({k: v for k, v in dict(kind='read', at=event.get('time'), via='Chattera ' + tool, seconds=event.get('elapsed_seconds'), url=info.get('url'), title=clip(info.get('title') or '', 200),
                                                 level=info.get('level'), page=info.get('page'), characters=info.get('characters'), total_characters=info.get('total_characters')).items() if v is not None})
            continue
        if tool not in ('web_search', 'research_search', 'web_read', 'research_read'): continue
        base = dict(at=event.get('time'), via='Chattera ' + tool, seconds=event.get('elapsed_seconds'))
        if event.get('status') != 'success':
            entries.append(dict(base, kind='search' if tool.endswith('search') else 'read', query=clip(args.get('query'), 300) if args.get('query') else None,
                                url=clip(args.get('url'), 600) if args.get('url') else None,
                                error=clip(str(event.get('error_type', '')) + ': ' + str(event.get('error', '')), 240)))
        elif tool.endswith('search'):
            rows = [source(dict(r, url=r.get('url') or ('https://doi.org/' + r['doi'] if r.get('doi') else None))) for r in info.get('results') or []]
            entries.append(dict(base, kind='search', query=clip(info.get('query') or args.get('query'), 300),
                                provider=info.get('provider') or ' + '.join(info.get('sources') or []), results=[r for r in rows if r]))
        else:
            entries.append(dict(base, kind='read', url=clip(info.get('url') or args.get('url'), 600), title=clip(info.get('title') or '', 200), level=info.get('level'),
                                characters=info.get('characters'), total_characters=info.get('total_characters')))
    return [{k: v for k, v in e.items() if v is not None} for e in entries]


def summary(entries):
    return dict(searches=sum(e['kind'] == 'search' for e in entries), pages=sum(e['kind'] == 'read' for e in entries),
                sources=len({r['url'] for e in entries for r in e.get('results', [])} | {e['url'] for e in entries if e['kind'] == 'read' and e.get('url')}),
                errors=sum(bool(e.get('error')) or e['kind'] == 'error' for e in entries))
