"""Chat folder: a working folder per chat for research-agent replies.

Participants keep files there through Chattera's own tools (list, read, write, download); no shell, nothing outside the
folder. It lives with the research library (<library folder>/chat-files/<room id>), so a library kept in a cloud-synced
folder takes it along; the local data folder is the fallback. Deleting the chat deletes its folder.
"""
import json
import os
import re
import time
from pathlib import Path

TEXT = ('.md', '.txt', '.csv', '.tsv', '.json', '.bib', '.ris', '.html', '.htm', '.xml', '.yaml', '.yml', '.tex', '.py', '.r', '.log')
WRITE_LIMIT = 4 * 1024 * 1024        # one text file written by a participant
DOWNLOAD_LIMIT = 200 * 1024 * 1024   # one downloaded file (papers, datasets)
READ_CHARS = 20000


def base(runtime):
    root = (runtime.registry.data.get('library') or {}).get('root') or os.environ.get('CHATTERA_LIBRARY_DIR')
    try:
        if root and Path(root).is_dir(): return Path(root) / 'chat-files'
    except OSError: pass
    return Path(runtime.data_dir) / 'chat-files'


def folder_for(runtime, room):
    folder = base(runtime) / room['id']
    folder.mkdir(parents=True, exist_ok=True)
    note = folder / '_chat.txt'
    try:
        text = 'Chattera chat: ' + room['name'] + '\nroom id: ' + room['id'] + '\n'
        if not note.exists() or note.read_text('utf-8') != text: note.write_text(text, 'utf-8')
    except OSError: pass
    return folder


def inside(folder, relative):
    """A path the participant named, kept inside the chat folder."""
    relative = str(relative or '').replace('\\', '/').strip().lstrip('/')
    if not relative or len(relative) > 240 or any(part in ('', '.', '..') for part in relative.split('/')) or ':' in relative \
            or re.search(r'[<>"|?*\x00-\x1f]', relative) or relative.startswith('_chat.txt'):
        raise ValueError('Use a relative path inside the chat folder, e.g. notes/summary.md')
    path = (Path(folder) / relative).resolve()
    if Path(folder).resolve() not in path.parents: raise ValueError('Path is outside the chat folder')
    return path


def listing(folder):
    folder = Path(folder)
    rows = []
    for path in sorted(folder.rglob('*')):
        if path.is_file() and path.name != '_chat.txt':
            stat = path.stat()
            rows.append(dict(path=path.relative_to(folder).as_posix(), bytes=stat.st_size, modified=round(stat.st_mtime)))
            if len(rows) >= 500: break
    return rows


def run_tool(name, args, snapshot, service):
    """(result for the model, audit summary)."""
    space = snapshot.get('workspace') or {}
    folder = Path(space['path'])
    folder.mkdir(parents=True, exist_ok=True)
    if name == 'workspace_list':
        rows = listing(folder)
        return dict(folder='chat folder', files=rows, note='Files you and other participants kept for this chat.'), dict(files=len(rows))
    path = inside(folder, args.get('path'))
    if name == 'workspace_write':
        content = args.get('content', '')
        if path.suffix.lower() not in TEXT: raise ValueError('Write text files only (.md, .txt, .csv, .json, .bib, …)')
        data = content.encode('utf-8')
        if len(data) > WRITE_LIMIT: raise ValueError('File too large for one write; split it')
        path.parent.mkdir(parents=True, exist_ok=True)
        if args.get('append') and path.exists(): data = path.read_bytes() + data
        temp = path.with_name(path.name + '.tmp'); temp.write_bytes(data); os.replace(temp, path)
        return dict(saved=path.relative_to(folder).as_posix(), bytes=len(data)), dict(path=path.relative_to(folder).as_posix(), written=True)
    if name == 'workspace_read':
        if not path.is_file(): raise ValueError('No such file in the chat folder')
        offset = max(0, int(args.get('offset') or 0))
        if path.suffix.lower() == '.pdf':
            if service is None: raise ValueError('PDF reading unavailable')
            text = service._pdf(path.read_bytes())['text']
        else:
            text = path.read_bytes()[:20_000_000].decode('utf-8', 'replace')
        part = text[offset:offset + READ_CHARS]
        return dict(path=path.relative_to(folder).as_posix(), text=part, coverage=dict(offset=offset, returned_characters=len(part), total_characters=len(text),
                    complete=offset + len(part) >= len(text)), evidence='file in the chat folder; external content stays unverified'), \
               dict(path=path.relative_to(folder).as_posix(), characters=len(part))
    if name == 'workspace_download':
        if not snapshot.get('research_allowed'): raise ValueError('Downloads are off in private context')
        url = str(args.get('url', '')).strip()
        if not url.startswith('https://'): raise ValueError('HTTPS URL required')
        from chat_evidence import http_get
        final, kind, body = http_get(url, '*/*', 120, DOWNLOAD_LIMIT)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + '.tmp'); temp.write_bytes(body); os.replace(temp, path)
        return dict(saved=path.relative_to(folder).as_posix(), bytes=len(body), content_type=kind, source=final), \
               dict(path=path.relative_to(folder).as_posix(), url=final, written=True)
    raise ValueError('Unknown chat-folder tool')


def tool_specs():
    return [('workspace_list', "List the files in this chat's folder (kept by you and other participants for this chat).", {}, []),
            ('workspace_read', "Read a file from this chat's folder (text, Markdown, CSV, JSON or PDF). offset continues a long file.",
             {'path': {'type': 'string'}, 'offset': {'type': 'integer'}}, ['path']),
            ('workspace_write', "Write a text file (.md, .txt, .csv, .json, .bib…) into this chat's folder: notes, tables, drafts. "
             "append=true adds to the end. The user can open these files.",
             {'path': {'type': 'string'}, 'content': {'type': 'string'}, 'append': {'type': 'boolean'}}, ['path', 'content'])]


def download_spec():
    return ('workspace_download', "Download a file (e.g. an open-access PDF or a dataset) from an HTTPS URL into this chat's folder, to read later with workspace_read.",
            {'url': {'type': 'string'}, 'path': {'type': 'string'}}, ['url', 'path'])


def written(directory):
    """Files a finished reply wrote or downloaded, from its tool audit log."""
    seen = {}
    log = Path(directory) / 'tool-events.jsonl'
    if not log.exists(): return []
    for line in log.read_text('utf-8').splitlines():
        try: event = json.loads(line)
        except ValueError: continue
        info = event.get('summary') or {}
        if event.get('status') == 'success' and info.get('written') and info.get('path'): seen[info['path']] = True
    return list(seen)[:60]


def install(runtime, app_module):
    from fastapi import Request
    from fastapi.responses import FileResponse, JSONResponse
    api = app_module.app

    def room_folder(room_id):
        return folder_for(runtime, runtime.rooms.get(room_id))

    @api.get('/api/chat/rooms/{room_id}/files')
    async def files(room_id: str):
        try:
            folder = room_folder(room_id)
        except ValueError as exc: return JSONResponse({'error': str(exc)}, status_code=404)
        return dict(folder=str(folder), files=listing(folder))

    @api.get('/api/chat/rooms/{room_id}/files/raw')
    async def raw(room_id: str, path: str):
        try:
            target = inside(room_folder(room_id), path)
            if not target.is_file(): raise ValueError('No such file')
        except ValueError as exc: return JSONResponse({'error': str(exc)}, status_code=404)
        inline = target.suffix.lower() in ('.pdf', '.txt', '.md', '.csv', '.json', '.png', '.jpg', '.jpeg', '.webp', '.svg', '.html', '.htm')
        media = 'text/plain; charset=utf-8' if target.suffix.lower() in ('.md', '.csv', '.tsv', '.bib', '.ris', '.tex', '.py', '.r', '.log', '.yaml', '.yml', '.html', '.htm', '.svg', '.xml') else None
        return FileResponse(target, media_type=media, filename=target.name, content_disposition_type='inline' if inline else 'attachment',
                            headers={'X-Content-Type-Options': 'nosniff', 'Content-Security-Policy': "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'"})

    @api.post('/api/chat/rooms/{room_id}/files/open')
    async def open_folder(room_id: str):
        """Open the chat folder in the file manager of this computer (Chattera only runs locally)."""
        try:
            folder = room_folder(room_id)
            if os.name == 'nt': os.startfile(str(folder))
            return dict(folder=str(folder))
        except (ValueError, OSError) as exc: return JSONResponse({'error': str(exc)}, status_code=400)
