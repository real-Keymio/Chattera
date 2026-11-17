"""Deleting a chat for good: its messages and everything recorded only for it.

A chat's data is spread over several stores. Each is cleaned here, never another chat's:
  messages (agentchattr_log.jsonl, todos)      reply calls (chat_calls/<task>, invocations.jsonl)
  summaries and their versions                 automatic-memory jobs (memory_calls)
  shared-retrieval bundles (evidence/<id>)     canvas and layout (workspaces/<room>.json)
  uploaded attachments and their parse cache   research library (library/rooms/<room>.json)   chat folder (<library>/chat-files/<room>)
A branch is a full copy of its source (messages, attachments, library), so deleting either never touches the other.
Content-addressed caches shared by every chat (library files, rendered PDF pages) lose only what no remaining chat uses.

Every rewritten file is written to a temporary file and swapped in. The message log is copied to
agentchattr_log.jsonl.before-delete first; that copy is removed at the next start.
"""
import hashlib
import json
import os
import shutil
from pathlib import Path

from fastapi import Request
from fastapi.responses import JSONResponse

from chattera_i18n import _

BACKUP_SUFFIX = '.before-delete'


def _replace_lines(path, keep):
    """Rewrite a JSONL file keeping the lines `keep(obj)` accepts; unparsable lines are kept as they are."""
    path = Path(path)
    if not path.exists(): return 0
    removed, out = 0, []
    for line in path.read_text('utf-8').splitlines():
        try: value = json.loads(line)
        except ValueError: out.append(line); continue
        if keep(value): out.append(line)
        else: removed += 1
    if removed:
        temp = path.with_name(path.name + '.tmp')
        with open(temp, 'w', encoding='utf-8', newline='\n') as stream:
            stream.write(''.join(l + '\n' for l in out)); stream.flush(); os.fsync(stream.fileno())
        os.replace(temp, path)
    return removed


def _rmtree(path):
    if Path(path).is_dir(): shutil.rmtree(path, ignore_errors=True); return 1
    return 0


def clean_backups(data_dir):
    for path in Path(data_dir).glob('*' + BACKUP_SUFFIX):
        path.unlink(missing_ok=True)


def delete_room(runtime, app_module, data_dir, room_id):
    data_dir = Path(data_dir)
    store, tasks = app_module.store, runtime.tasks
    room = runtime.rooms.get(room_id)
    channel, rid = room['channel'], room['id']
    # 1. Stop everything still working for this chat, and wait for it to let go.
    tasks.cancel(channel)
    if getattr(runtime, 'memory', None): runtime.memory.cancel(channel)
    for task in list(tasks.tasks.values()):
        if task.get('channel') == channel and task.get('thread'): task['thread'].join(10)
    counts = {}
    with tasks.lock:
        # 2. Forget the room first: nothing can be added to it from here on (store.add asks the catalogue).
        with runtime.rooms.lock:
            runtime.rooms.rooms.pop(rid, None)
            runtime.rooms.save()
        # 3. Messages.
        with store._lock:
            gone = [m for m in store._messages if m.get('channel', 'general') == channel]
            ids = {m['id'] for m in gone}
            if gone:
                log = Path(store._path)
                shutil.copyfile(log, log.with_name(log.name + BACKUP_SUFFIX))
                store._messages = [m for m in store._messages if m['id'] not in ids]
                temp = log.with_name(log.name + '.tmp')
                with open(temp, 'w', encoding='utf-8') as stream:
                    for m in store._messages: stream.write(json.dumps(m, ensure_ascii=False) + '\n')
                    stream.flush(); os.fsync(stream.fileno())
                os.replace(temp, log)
                if any(i in store._todos for i in ids):
                    for i in ids: store._todos.pop(i, None)
                    store._save_todos()
        counts['messages'] = len(gone)
        # 4. Attachments (a branch holds its own copies) and their parse cache.
        upload = Path(runtime.attachments.upload)
        still_used = {a.get('url') for m in store.get_since(-1) for a in m.get('attachments') or []}
        removed_files, digests = 0, set()
        for message in gone:
            for att in message.get('attachments') or []:
                url = str(att.get('url', ''))
                if not url.startswith('/uploads/') or url in still_used: continue
                path = upload / url.rsplit('/', 1)[-1]
                if path.resolve().parent != upload or not path.is_file(): continue
                digests.add(hashlib.sha256(path.read_bytes()).hexdigest())
                path.unlink(missing_ok=True); removed_files += 1
        cache = Path(runtime.attachments.cache)
        _rmtree(cache / hashlib.sha256(channel.encode()).hexdigest())
        for digest in digests:
            for page in (cache / 'pages').glob(digest + '-*.png'): page.unlink(missing_ok=True)
        counts['attachments'] = removed_files
        # 5. Shared-retrieval bundles recorded in this chat.
        evidence = {str((m.get('metadata') or {}).get('evidence_id')) for m in gone if (m.get('metadata') or {}).get('evidence_id')}
        for identity in evidence:
            if all(c.isalnum() or c in '-_' for c in identity): (data_dir / 'evidence' / (identity + '.json')).unlink(missing_ok=True)
        # 6. Reply calls: snapshots, inputs and the invocation log.
        task_ids = [t for t, task in tasks.tasks.items() if task.get('channel') == channel]
        for task_id in task_ids:
            tasks.tasks.pop(task_id, None)
            if all(c.isalnum() or c == '-' for c in task_id): _rmtree(tasks.directory / task_id)
        _replace_lines(tasks.log, lambda row: row.get('channel') != channel)
        counts['calls'] = len(task_ids)
        # 7. Summaries and automatic memory.
        summaries = app_module.summaries
        with summaries._lock:
            if summaries._summaries.pop(channel, None) is not None: summaries._save()
            summaries._versions = [v for v in summaries._versions if v.get('channel') != channel]
        _replace_lines(summaries._versions_path, lambda row: row.get('channel') != channel)
        memory = getattr(runtime, 'memory', None)
        if memory:
            with memory.lock:
                jobs = set()
                for line in (memory.log.read_text('utf-8').splitlines() if memory.log.exists() else []):
                    try: row = json.loads(line)
                    except ValueError: continue
                    if row.get('channel') == channel and row.get('id'): jobs.add(str(row['id']))
                for job in jobs:
                    if all(c.isalnum() or c == '-' for c in job): _rmtree(memory.directory / job)
                _replace_lines(memory.log, lambda row: row.get('channel') != channel)
                memory.jobs.pop(channel, None)
                if channel in memory.disabled:
                    memory.disabled = [c for c in memory.disabled if c != channel]
                    temp = memory.settings_path.with_suffix('.tmp'); temp.write_text(json.dumps(memory.disabled)); temp.replace(memory.settings_path)
        # 8. Canvas / layout, and the research library.
        (data_dir / 'workspaces' / (rid + '.json')).unlink(missing_ok=True)
        try:
            import chat_files
            counts['chat_folder'] = _rmtree(chat_files.base(runtime) / rid)
        except (OSError, AttributeError): counts['chat_folder'] = 0
        library = getattr(runtime, 'library', None)
        counts['library'] = library.forget_room(rid) if library else dict(sources=0, files_removed=0)
    runtime.sync_settings()
    return dict(room_id=rid, name=room['name'], **counts)


def install(runtime, app_module, data_dir):
    clean_backups(data_dir)
    api = app_module.app

    async def announce():
        await app_module.broadcast_settings()
        await app_module.broadcast_agents()

    @api.post('/api/chat/rooms/{room_id}/delete')
    async def delete(room_id: str, request: Request):
        import asyncio
        body = await request.json()
        if body.get('confirm') is not True:
            return JSONResponse({'error': _('删除需要确认')}, status_code=400)
        try:
            result = await asyncio.to_thread(delete_room, runtime, app_module, data_dir, room_id)
        except ValueError as exc:
            return JSONResponse({'error': str(exc)}, status_code=404)
        await announce()
        return result

    @api.post('/api/chat/archived/delete')
    async def delete_archived(request: Request):
        """Empty the archive: delete every archived chat."""
        import asyncio
        body = await request.json()
        if body.get('confirm') is not True:
            return JSONResponse({'error': _('删除需要确认')}, status_code=400)
        done = []
        for room in [r for r in runtime.rooms.list() if r['archived']]:
            done.append(await asyncio.to_thread(delete_room, runtime, app_module, data_dir, room['id']))
        await announce()
        return dict(deleted=done)
