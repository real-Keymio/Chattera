// Per-room workspace layout (tile arrangement, free-canvas windows), saved by the server in the data folder.
// Loaded once before the panes start, then read synchronously; writes are debounced. Layouts saved by an earlier
// version in this browser are moved to the server the first time a room is opened, then removed from the browser.
// Light per-viewer state (theme, reading position inside a pane) stays in the browser.
import { api, store, TOKEN } from './util.js';

const cache = {};          // channel -> { layout, canvas }
const timers = new Map();  // kind:channel -> timeout
const pending = new Map(); // kind:channel -> body not yet sent

export async function loadWorkspaces() {
    try { Object.assign(cache, await api('/api/chat/workspaces')); } catch (e) { /* server unreachable: layouts start fresh */ }
}

const url = channel => `/api/chat/rooms/${encodeURIComponent(channel)}/workspace`;

function send(key, keepalive = false) {
    const body = pending.get(key);
    if (!body) return;
    pending.delete(key);
    const done = fetch(url(body.channel), { method: 'POST', keepalive, headers: { 'X-Session-Token': TOKEN, 'Content-Type': 'application/json' },
        body: JSON.stringify({ kind: body.kind, value: body.value }) });
    if (body.migrated) done.then(r => { if (r.ok) store.set(body.kind + '-' + body.channel, null); }).catch(() => {});
}

export function wsSet(kind, channel, value, migrated = false) {
    if (!channel) return;
    (cache[channel] ||= {})[kind] = value;
    const key = kind + ':' + channel;
    pending.set(key, { kind, channel, value, migrated });
    clearTimeout(timers.get(key));
    timers.set(key, setTimeout(() => send(key), 400));
}

export function wsGet(kind, channel) {
    const saved = cache[channel]?.[kind];
    if (saved != null) return saved;
    const old = store.get(kind + '-' + channel);   // from the browser-only versions
    if (old != null) wsSet(kind, channel, old, true);
    return old;
}

addEventListener('pagehide', () => { for (const key of [...pending.keys()]) send(key, true); });
