"""Discussion aids on top of the room model: retrieval records, what each answer was actually given,
pinned premises, research lenses per room, and forking a room from a message. Nothing here rewrites history."""
import json
import shutil
import time
import uuid
from pathlib import Path

from fastapi import Request
from fastapi.responses import JSONResponse

from chat_identities import IDENTITIES, BUILTIN
from chat_visibility import audience, effective
from chattera_i18n import _


def install(runtime, app_module):
    api, tasks, store = app_module.app, runtime.tasks, app_module.store

    def fail(exc, code=400):
        return JSONResponse({'error': str(exc)}, status_code=code)

    @api.get('/api/chat/evidence/{evidence_id}')
    async def evidence(evidence_id: str):
        try:
            bundle = runtime.evidence.load(evidence_id)
        except ValueError as exc:
            return fail(exc, 404)
        for item in bundle['items']:
            if item.get('text'): item['text'] = item['text'][:3000]
        return bundle

    @api.get('/api/chat/tasks/{task_id}/materials')
    async def materials(task_id: str):
        """What this one call was given at the start, and what the model then fetched itself with tools."""
        task = tasks.tasks.get(task_id)
        if not task: return fail('Unknown call', 404)
        directory = tasks.directory / task_id
        try:
            payload = json.loads((directory / 'input.json').read_text('utf-8'))
            snapshot = json.loads((directory / 'snapshot.json').read_text('utf-8'))
        except (OSError, ValueError):
            return fail('The record of this call is no longer available', 404)
        events = []
        log = directory / 'tool-events.jsonl'
        if log.exists():
            for line in log.read_text('utf-8').splitlines():
                try:
                    event = json.loads(line)
                    if event.get('status') != 'started': events.append(event)
                except ValueError: pass
        content = snapshot.get('attachment_content', {})
        given = {(e['message_id'], e['index']): e for e in payload.get('attachment_evidence', [])}
        documents, images = [], []
        for record in content.values():
            key = (record['message_id'], record['index'])
            if record.get('kind') == 'image':
                if key in given:
                    images.append(dict(name=record.get('name'), message_id=key[0], original_size=record.get('original_size'),
                                       prepared_size=record.get('prepared_size'), delivered='pixels sent to the model'))
                continue
            if record.get('kind') != 'text': continue
            units = record.get('units', [])
            total = sum(len(u['text']) for u in units)
            reads = [dict(args=e.get('args'), coverage=(e.get('summary') or {}).get('coverage'), status=e['status']) for e in events
                     if e.get('tool') == 'attachment_read' and (e.get('args') or {}).get('message_id') == key[0] and (e.get('args') or {}).get('index', 0) == key[1]]
            initial = given.get(key)
            documents.append(dict(name=record.get('name'), message_id=key[0], index=key[1], format=record.get('format'), unit_type=record.get('unit_type'),
                page_count=record.get('page_count'), total_units=len(units), total_characters=total,
                ocr_pages=record.get('ocr_pages', []), pages_without_text=record.get('pages_without_text', []),
                ocr_units=[u['unit'] for u in units if u.get('source') == 'ocr'],
                initial=dict(coverage=initial.get('coverage'), extraction=initial.get('extraction')) if initial else None,
                tool_reads=reads))
        evidence_view = None
        if snapshot.get('shared_evidence'):
            bundle = snapshot['shared_evidence']
            delivery = {d['n']: d for d in task.get('evidence_delivery') or []}
            evidence_view = dict(id=bundle['id'], queries=bundle['queries'], query_source=bundle.get('query_source'),
                items=[dict(n=i['n'], kind=i['kind'], title=i['title'], url=i.get('url'), found_level=i['level'],
                            given_level=delivery.get(i['n'], {}).get('level', i['level']), given_characters=delivery.get(i['n'], {}).get('given_characters', 0),
                            total_characters=i.get('total_characters', 0)) for i in bundle['items']])
        caps = snapshot.get('backend_capabilities') or {}
        from chat_search import from_tool_events, summary as search_summary
        searched = sorted(list(task.get('search_trace') or []) + from_tool_events(events), key=lambda e: e.get('at') or 0)
        search_view = dict(plan=task.get('search') or snapshot.get('search'), entries=searched, citations=task.get('search_citations') or [],
                           usage=task.get('search_usage'), totals=search_summary(searched)) if (task.get('search') or snapshot.get('search') or searched) else None
        return dict(task_id=task_id, search=search_view, model=task.get('identity', task.get('model')), backend=task.get('backend'), state=task.get('state'),
            requested_model=task.get('requested_model'), actual_model=task.get('actual_model'), effort=task.get('requested_effort'),
            lens=task.get('research_lens'), documents=documents, images=images, shared_evidence=evidence_view,
            tool_calls=[{k: e.get(k) for k in ('tool', 'status', 'args', 'summary', 'elapsed_seconds', 'error') if e.get(k) is not None} for e in events],
            tools_available=bool(caps.get('tools')), external_tools_allowed=bool(snapshot.get('research_allowed')),
            history=dict(included=len(task.get('history_included_ids') or []), trimmed=task.get('trimmed_history_ranges') or [],
                         summarized=task.get('summarized_history_ranges') or [], summary_version=task.get('snapshot_summary_version')),
            pins=len(snapshot.get('pins') or []), input_estimate=task.get('input_estimate'), budget=task.get('budget'), budget_unit=task.get('budget_unit'),
            usage=task.get('usage_fields') or task.get('usage'))

    @api.post('/api/chat/rooms/{room_id}/pins')
    async def add_pin(room_id: str, request: Request):
        body = await request.json()
        try:
            room = runtime.rooms.get(room_id)
            pins = list(room.get('pins', []))
            if len(pins) >= 12: raise ValueError(_('最多固定 12 条；请先取消一些'))
            text, author, source = str(body.get('text') or '').strip(), None, None
            if body.get('message_id') is not None:
                message = store.get_by_id(body['message_id'])
                if not message or message.get('channel', 'general') != room['channel'] or message.get('type', 'chat') != 'chat':
                    raise ValueError('Message unavailable')
                if audience(message) != 'public': raise ValueError(_('私聊内容不能固定到房间背景；请先公开分享'))
                from chat_visibility import model_message
                author, source = model_message(message, store.get_by_id)['sender'], message['id']
                text = text or message['text']
            if not text or len(text) > 2000: raise ValueError(_('固定内容需要 1–2000 个字符；较长的回答请先选中要固定的那一段'))
            from chat_secrets import redact
            pins.append(dict(id=uuid.uuid4().hex[:12], text=redact(text), source_message_id=source, author=author, created_at=time.time()))
            return runtime.rooms.patch(room_id, pins=pins)
        except (ValueError, KeyError, TypeError) as exc:
            return fail(exc)

    @api.delete('/api/chat/rooms/{room_id}/pins/{pin_id}')
    async def remove_pin(room_id: str, pin_id: str):
        try:
            room = runtime.rooms.get(room_id)
            return runtime.rooms.patch(room_id, pins=[p for p in room.get('pins', []) if p.get('id') != pin_id])
        except ValueError as exc:
            return fail(exc, 404)

    @api.post('/api/chat/rooms/{room_id}/lens')
    async def set_lens(room_id: str, request: Request):
        body = await request.json()
        try:
            known = runtime.registry.lenses()
            fields = {}
            if 'lens' in body:
                if body['lens'] not in known: raise ValueError('Unknown research lens')
                fields['lens'] = None if body['lens'] == 'open' else body['lens']
            if 'lens_by_model' in body:
                value = body['lens_by_model'] or {}
                if not isinstance(value, dict) or set(value) - set(IDENTITIES) or any(v not in known for v in value.values()): raise ValueError('Unknown research lens')
                fields['lens_by_model'] = value or None
            return runtime.rooms.patch(room_id, **fields)
        except (ValueError, KeyError, TypeError) as exc:
            return fail(exc)

    @api.get('/api/chat/rooms/{room_id}/effective-prompt')
    async def effective_prompt(room_id: str, model: str):
        """The standing instructions a participant would receive in this room right now (no history, no message)."""
        try:
            room = runtime.rooms.get(room_id)
            if model not in IDENTITIES: raise ValueError('Unknown participant')
            from chat_rooms import IDENTITY
            lens = runtime.lens_for(room['channel'], model, {})
            return dict(identity=IDENTITY.format(model=model, label=runtime.labels.get(model, model)), lens=lens,
                        room_context=runtime.builder.room_prompts.get(room['id'], runtime.builder.room_prompts.get(room['channel'], {})),
                        global_rules=runtime.builder.rules.active_list() if runtime.builder.rules else {'rules': []},
                        pins=room.get('pins', []))
        except ValueError as exc:
            return fail(exc)

    @api.post('/api/chat/rooms/{room_id}/fork')
    async def fork(room_id: str, request: Request):
        """New room holding a copy of this room's current history up to one message. The source room is not changed."""
        import asyncio
        body = await request.json()
        try:
            source = runtime.rooms.get(room_id)
            cutoff = body['message_id']
            if type(cutoff) is not int: raise ValueError('Choose the message to branch from')
            rows = [m for m in effective(store.get_since(-1, channel=source['channel']))
                    if m['id'] <= cutoff and m.get('type', 'chat') in ('chat', 'evidence')]
            if not rows or rows[-1]['id'] != cutoff: raise ValueError('Branch from a current message in this chat')
            if len(rows) > 3000: raise ValueError('This chat is too long to branch in one step')
            name = runtime.rooms.validate_name(body.get('name') or (source['name'][:60] + _(' · 分支')))

            def copy_history():
                room = runtime.create(name)
                channel = room['channel']
                for model, backend in source['owners'].items():
                    if model not in BUILTIN and not source.get('model_settings', {}).get(model): continue  # follows the participant's own connection
                    if backend in tasks.backends:
                        settings = source.get('model_settings', {}).get(model)
                        settings = {k: v for k, v in settings.items() if k != 'backend'} if settings and settings.get('backend') == backend else None
                        try: tasks.switch(channel, model, backend, settings)
                        except ValueError: pass
                runtime.rooms.update(room['id'], context_mode=source.get('context_mode'), context_limit_tokens=source.get('context_limit_tokens'))
                mapping = {}
                origin = dict(room_id=source['id'], room_name=source['name'], message_id=cutoff, at=time.time())
                upload = runtime.attachments.upload
                with tasks.lock:
                    runtime.revision_write = True
                    try:
                        for message in rows:
                            meta = message.get('metadata') or {}
                            new_meta = dict(visibility=audience(message), targets=[], forked_from=dict(room_id=source['id'], message_id=message['id']))
                            for key in ('independent_group', 'backend', 'evidence_id', 'evidence_counts'):
                                if meta.get(key) is not None: new_meta[key] = meta[key]
                            if meta.get('shared_from') is not None:
                                from chat_visibility import provenance
                                origin_info = provenance(message, store.get_by_id)
                                for key in ('original_message_id', 'source_message_id'):
                                    origin_info[key] = mapping.get(origin_info.get(key), origin_info.get(key))
                                new_meta.update(shared_from=mapping.get(meta['shared_from'], -1), share_provenance=origin_info)
                            attachments = []
                            for item in message.get('attachments', []):
                                try:
                                    path = runtime.attachments.path(item)
                                    target = upload / (uuid.uuid4().hex + path.suffix.lower())
                                    shutil.copyfile(path, target)
                                    attachments.append(dict(item, url='/uploads/' + target.name))
                                except (ValueError, OSError):
                                    continue
                            added = store.add(message['sender'], message['text'], msg_type=message.get('type', 'chat'), channel=channel,
                                              reply_to=mapping.get(message.get('reply_to')), metadata=new_meta, attachments=attachments,
                                              timestamp=message.get('timestamp'), time_str=message.get('time'))
                            mapping[message['id']] = added['id']
                    finally:
                        runtime.revision_write = False
                pins = [dict(p, source_message_id=mapping.get(p.get('source_message_id'))) for p in source.get('pins', [])
                        if p.get('source_message_id') is None or p.get('source_message_id') in mapping]
                return runtime.rooms.patch(room['id'], forked_from=origin, lens=source.get('lens'), lens_by_model=source.get('lens_by_model'), pins=pins or None)

            room = await asyncio.to_thread(copy_history)
            runtime.sync_settings()
            await app_module.broadcast_settings()
            await app_module.broadcast_agents()
            return room
        except (ValueError, KeyError, TypeError, OSError) as exc:
            return fail(exc)
