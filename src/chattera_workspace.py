"""Per-room workspace layout (tile arrangement and free-canvas windows), kept in the data folder.

A workspace only describes how the room is laid out on screen: which panes, which windows, their positions and the
ids of the messages / sources they show. It holds no message text and is not part of the history, so it is never
sent to a model. Files: <data>/workspaces/<room id>.json.
"""
import json
import os
import time
from pathlib import Path

from fastapi import Request
from fastapi.responses import JSONResponse

KINDS = ('layout', 'canvas')
MAX_BYTES = 512 * 1024


def install(runtime, app_module, data_dir):
    folder = Path(data_dir) / 'workspaces'
    folder.mkdir(parents=True, exist_ok=True)
    api = app_module.app

    def path_of(room):
        return folder / (room['id'] + '.json')

    def read(room):
        try:
            return json.loads(path_of(room).read_text('utf-8'))
        except (OSError, ValueError):
            return {}

    @api.get('/api/chat/workspaces')
    async def workspaces():
        """Every room's saved layout, keyed by the room's storage channel (what the interface uses)."""
        result = {}
        for room in runtime.rooms.list():
            value = read(room)
            if value:
                result[room['channel']] = {k: value[k] for k in KINDS if k in value}
        return result

    @api.post('/api/chat/rooms/{room_id}/workspace')
    async def save(room_id: str, request: Request):
        try:
            room = runtime.rooms.get(room_id)
        except ValueError:
            return JSONResponse({'error': 'Unknown room'}, status_code=404)
        body = await request.json()
        kind, value = body.get('kind'), body.get('value')
        if kind not in KINDS or not (value is None or isinstance(value, dict)):
            return JSONResponse({'error': 'Invalid workspace'}, status_code=400)
        if len(json.dumps(value)) > MAX_BYTES:
            return JSONResponse({'error': 'Workspace layout too large'}, status_code=413)
        with runtime.rooms.lock:
            current = read(room)
            if value is None: current.pop(kind, None)
            else: current[kind] = value
            current.update(room=room['id'], updated_at=time.time())
            target = path_of(room)
            temp = target.with_suffix('.' + os.urandom(6).hex() + '.tmp')
            temp.write_text(json.dumps(current, ensure_ascii=False), 'utf-8')
            temp.replace(target)
        return {'ok': True}
