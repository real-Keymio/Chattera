"""HTTP side of the research library, plus the read-only scan of locally installed subscription CLIs."""
import asyncio
import json
import uuid
from pathlib import Path

from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse

from chat_library import Library, DEFAULT_LIMIT
from chattera_i18n import _


def install(runtime, app_module, data_dir):
    api, registry, tasks = app_module.app, runtime.registry, runtime.tasks
    settings = lambda: registry.data.get('library', {})
    import os
    library = Library(settings().get('root') or os.environ.get('CHATTERA_LIBRARY_DIR') or Path(data_dir).resolve() / 'library', settings)
    runtime.library = library

    def fail(exc, code=400):
        return JSONResponse({'error': str(exc)}, status_code=code)

    def changed(room_id):
        loop = app_module._event_loop
        if loop and not loop.is_closed():
            asyncio.run_coroutine_threadsafe(app_module._broadcast(json.dumps({'type': 'chat_library', 'data': {'room_id': room_id}})), loop)
    runtime.library_changed = changed

    # ---- what a reply is given, and what it leaves behind ----
    def library_info(channel, model):
        room_id = runtime.rooms.get(channel)['id']
        rows = runtime.library.view(room_id, model)
        return dict(root=str(runtime.library.root), room_id=room_id, cache_limit_bytes=runtime.library.limit(), task_tag=uuid.uuid4().hex,
                    visible_ids=[r['id'] for r in rows], view=runtime.library.brief(room_id, model))

    def library_ingest(task, answer, reply_id):
        from chat_search import from_tool_events
        events, log = [], Path(task['directory']) / 'tool-events.jsonl'
        if log.exists():
            for line in log.read_text('utf-8').splitlines():
                try: events.append(json.loads(line))
                except ValueError: pass
        entries = list(task.get('search_trace') or []) + from_tool_events(e for e in events if e.get('status') != 'started')
        touched = any(str(e.get('tool', '')).startswith('library_') for e in events)
        if not entries and not task.get('search_citations') and not touched: return
        origin = dict(task_id=task.get('library_tag') or task['task_id'], message_id=reply_id)
        runtime.library.ingest_task(task['room_id'], task['identity'], entries, task.get('search_citations'), answer, origin)
        changed(task['room_id'])
    runtime.builder.library_info = library_info
    runtime.builder.library_ingest = library_ingest

    room_of = lambda room_id: runtime.rooms.get(room_id)['id']

    @api.get('/api/chat/rooms/{room_id}/library')
    async def listing(room_id: str):
        try: return dict(items=runtime.library.view(room_of(room_id)))
        except ValueError as exc: return fail(exc, 404)

    @api.post('/api/chat/rooms/{room_id}/library')
    async def add(room_id: str, request: Request):
        """The user adds a source by DOI or URL (metadata only)."""
        body = await request.json()
        try:
            rid, ref = room_of(room_id), str(body.get('ref') or '').strip()
            import re
            fields = dict(doi=ref) if re.match(r'^(doi:|https?://(dx\.)?doi\.org/)?10\.\d{4,9}/', ref, re.I) else dict(url=ref)
            if fields.get('doi'):
                try:
                    found = (await asyncio.to_thread(runtime.evidence.scholar_search, re.sub(r'^(doi:|https?://(dx\.)?doi\.org/)', '', ref, flags=re.I), 1))['results']
                    if found: fields = dict(found[0], doi=ref)
                except Exception: pass
            item = runtime.library.upsert(rid, fields, 'user', shared=body.get('shared') is not False)
            if not item: raise ValueError(_('请填写 DOI 或 https:// 开头的网址'))
            changed(rid)
            return item
        except (ValueError, KeyError) as exc: return fail(exc)

    @api.post('/api/chat/rooms/{room_id}/library/{item_id}/{action}')
    async def act(room_id: str, item_id: str, action: str, request: Request):
        body = await request.json()
        try:
            rid = room_of(room_id)
            if action == 'share':
                to = body.get('to')
                if to not in ('room', 'none') and (not isinstance(to, list) or not to or set(to) - set(registry.active_ids())): raise ValueError(_('选择要分享给谁'))
                result = runtime.library.share(rid, item_id, to, body.get('notes') is True)
            elif action == 'fetch':
                result = await asyncio.to_thread(runtime.library.fetch, rid, item_id, runtime.evidence)
            elif action == 'keep':
                if body.get('on') is not False and not runtime.library.load(rid)['items'].get(item_id, {}).get('file'):
                    await asyncio.to_thread(runtime.library.fetch, rid, item_id, runtime.evidence)
                runtime.library.keep(rid, item_id, body.get('on') is not False)
                result = dict(kept=body.get('on') is not False)
            elif action == 'notes':
                result = runtime.library.note(rid, item_id, 'user', body.get('text'), shared=body.get('shared') is True)
                if not result: raise ValueError(_('笔记内容不能为空'))
            elif action == 'note-share':
                note_id, on = body.get('note_id'), body.get('on') is not False
                def flip(data):
                    for note in data['notes']:
                        if note['id'] == note_id and note['item_id'] == item_id: note['shared'] = on
                runtime.library.update(rid, flip); result = dict(ok=True)
            elif action == 'note-remove':
                runtime.library.remove_note(rid, body.get('note_id')); result = dict(ok=True)
            elif action == 'remove':
                runtime.library.remove(rid, item_id); result = dict(ok=True)
            else: raise ValueError('Unknown action')
            changed(rid)
            return result
        except (ValueError, KeyError, OSError) as exc: return fail(exc)

    @api.get('/api/chat/rooms/{room_id}/library/{item_id}/text')
    async def text(room_id: str, item_id: str, offset: int = 0):
        try:
            part = runtime.library.text(room_of(room_id), item_id, offset, 12000)
            return part or fail(_('全文还没有获取，或缓存已被清理'), 404)
        except ValueError as exc: return fail(exc, 404)

    @api.get('/api/chat/rooms/{room_id}/library/{item_id}/file')
    async def original(room_id: str, item_id: str):
        try: path = runtime.library.original(room_of(room_id), item_id)
        except ValueError as exc: return fail(exc, 404)
        if not path: return fail(_('没有缓存的原始文件'), 404)
        return FileResponse(path, media_type='application/pdf', headers={'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'no-store'})

    @api.get('/api/chat/rooms/{room_id}/library/{item_id}/page')
    async def page_image(room_id: str, item_id: str, page: int = 1):
        import base64
        from fastapi.responses import Response
        try:
            path = runtime.library.original(room_of(room_id), item_id)
            if not path: return fail(_('这份资料没有缓存的 PDF（可以先“获取全文”）'), 404)
            if not 1 <= page <= 2000: raise ValueError(_('页码无效'))
            png = (await asyncio.to_thread(runtime.attachments.worker, path, dict(mode='page', page=page), 40))['png']
        except (ValueError, OSError, KeyError) as exc: return fail(exc)
        return Response(base64.b64decode(png), media_type='image/png', headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})

    @api.get('/api/chat/library/stats')
    async def stats():
        return runtime.library.stats()

    @api.post('/api/chat/library/settings')
    async def configure(request: Request):
        body = await request.json()
        try:
            with registry.lock:
                value = dict(registry.data.get('library', {}))
                if 'cache_limit_bytes' in body:
                    limit = body['cache_limit_bytes']
                    if type(limit) is not int or not 0 <= limit <= 200 * (1 << 30): raise ValueError(_('缓存上限应在 0 到 200 GiB 之间'))
                    value['cache_limit_bytes'] = limit
                if 'root' in body:
                    root = Path(str(body['root'] or '').strip()) if body['root'] else Path(data_dir).resolve() / 'library'
                    if not root.is_absolute(): raise ValueError(_('资料库位置需要是完整路径'))
                    root.mkdir(parents=True, exist_ok=True)
                    value['root'] = str(root) if body['root'] else None
                    runtime.library = Library(root, settings)   # existing files are not moved: the old folder stays where it is
                registry.data['library'] = {k: v for k, v in value.items() if v is not None}
                registry.save()
                runtime.place_caches()   # caches follow the library folder
            await asyncio.to_thread(runtime.library.cleanup)
            return runtime.library.stats()
        except (ValueError, OSError) as exc: return fail(exc)

    @api.post('/api/chat/library/cleanup')
    async def cleanup(request: Request):
        body = await request.json()
        removed = await asyncio.to_thread(runtime.library.cleanup, body.get('everything') is True)
        return dict(removed_bytes=removed, **runtime.library.stats())

    @api.get('/api/chat/cli-discovery')
    async def cli_discovery():
        from model_discovery import local_clis
        return await asyncio.to_thread(local_clis, registry.backends(), tasks.backends)
