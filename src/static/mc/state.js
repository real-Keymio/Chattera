// Single client-side state store + WebSocket connection.
// The browser is the local human's full view; model permissions are enforced by the server only.
import { TOKEN, api, store, explain, h } from './util.js';

// Stable participant ids. Display labels never replace these ids.
// Both lists are filled from the server (`/api/chat/models` → participants):
//   IDS — participants offered in this interface (active and shown in the picker), in display order;
//   ALL — every id that has ever spoken, including hidden and removed ones, so old messages keep their author.
export const IDS = ['claude', 'codex', 'antigravity', 'deepseek'];
export const ALL = [...IDS];
const LABELS = { claude: 'Claude', codex: 'Codex', antigravity: 'Gemini', deepseek: 'DeepSeek' };
const KNOWN_HUES = new Set(IDS);
export const participantOf = id => (S.models?.participants || []).find(p => p.id === id);
// One abstract shape per participant, by position: circle, triangle, diamond, hexagon, square, pentagon.
const SHAPES = ['M12 4.6a7.4 7.4 0 1 0 0 14.8 7.4 7.4 0 0 0 0-14.8z', 'M12 4.4 20.2 18.6H3.8z', 'M12 3.4 20.6 12 12 20.6 3.4 12z',
    'M12 3.8 19.1 7.9v8.2L12 20.2 4.9 16.1V7.9z', 'M5.4 5.4h13.2v13.2H5.4z', 'M12 3.8 20.2 9.8 17.1 19.4H6.9L3.8 9.8z'];

export const S = {
    username: 'user',
    settings: {},
    rooms: [],            // Room[] incl. archived
    active: store.get('room', null),   // immutable storage channel of the open room
    msgs: new Map(),      // id -> Message
    byChannel: new Map(), // channel -> Message[] ordered by id
    replaced: new Set(),  // ids superseded by a newer version
    tasks: new Map(),     // task_id -> Task
    backends: {},         // identity -> {backend: capabilities}
    verification: {},     // backend -> evidence from recorded real calls
    models: null,         // overview from /api/chat/models: profiles, catalogue, lenses, combos, search settings
    retrieving: new Map(),// user message id -> live shared-retrieval state
    connected: false,
    historyReady: false,
    unread: {},           // channel -> count
};

const listeners = {};
export const on = (name, fn) => { (listeners[name] ||= []).push(fn); };
export const emit = (name, data) => { for (const fn of listeners[name] || []) fn(data); };

export const label = id => LABELS[id] || id;
export const color = id => ALL.includes(id) ? `var(--hue-${id})` : 'var(--ink-3)';
/** Resolved colour of a participant in the current skin (for canvas drawing). */
export const hueValue = id => getComputedStyle(document.documentElement).getPropertyValue('--hue-' + id).trim() || '#8a8f98';
/** Sigil: the one visual identity mark of a participant, used everywhere. `state`: thinking | writing. */
export function sigil(id, cls = '', state = '') {
    const el = h('span', { class: 'sigil ' + cls, style: { '--hue': color(id) }, 'aria-hidden': 'true', dataset: state ? { state } : {} });
    const shape = SHAPES[Math.max(0, ALL.indexOf(id)) % SHAPES.length];
    el.innerHTML = `<svg viewBox="0 0 24 24"><path class="core" d="${ALL.includes(id) ? shape : SHAPES[4]}"/><circle class="ring" cx="12" cy="12" r="10.6"/></svg>`;
    return el;
}
export const tile = sigil;
export const lensOf = id => (S.models?.lenses || []).find(l => l.id === id);
export async function loadModels() {
    try {
        adoptModels(await api('/api/chat/models'));
    } catch (e) { /* older server: model management simply stays unavailable */ }
    return S.models;
}
/** Take a fresh overview from the server: participants, connections, catalogue. */
export function adoptModels(overview) {
    if (!overview?.profiles) return;
    S.models = overview;
    const rows = overview.participants || [];
    ALL.splice(0, ALL.length, ...rows.map(p => p.id));
    IDS.splice(0, IDS.length, ...rows.filter(p => !p.retired && !p.hidden).map(p => p.id));
    for (const p of rows) {
        LABELS[p.id] = p.label;
        if (!KNOWN_HUES.has(p.id)) document.documentElement.style.setProperty('--hue-' + p.id, p.color);
    }
    S.verification = overview.verification || S.verification;
    emit('models');
}
/** Connection, model and effort a participant would use in a room right now. Order: this chat's choice, the participant's own
 *  setting, then the connection's default. `source` says which one supplied the model. */
export function effectiveModel(room, id) {
    const own = participantOf(id);
    const backend = room?.owners?.[id] || own?.connection || 'legacy';
    const profile = (S.models?.profiles || []).find(p => p.name === backend);
    const saved = room?.model_settings?.[id]?.backend === backend ? room.model_settings[id] : {};
    const mine = own?.connection === backend ? own : {};
    const model = saved.model || mine.model || profile?.model || null;
    const entry = (S.models?.catalog?.[backend] || []).find(m => m.id === model) || null;
    return { backend, profile, entry, model, modelLabel: entry?.label || model, effort: saved.effort || mine.effort || profile?.effort || null,
        roomSet: Boolean(saved.model || saved.effort), source: saved.model ? 'room' : mine.model ? 'participant' : 'connection' };
}
/** 低 / 中 / 高 … for display; the value sent to the provider is never translated. */
export const effortName = level => level ? (S.models?.effort_labels?.[String(level).toLowerCase()] || level) : '';
export const isAI = sender => ALL.includes(family(sender));
export const family = sender => String(sender || '').toLowerCase().replace(/-\d+$/, '');
export const roomOf = channel => S.rooms.find(r => r.channel === channel);
export const activeRoom = () => roomOf(S.active);
export const audienceOf = msg => msg?.metadata?.visibility || 'public';
export const rootOf = msg => msg.metadata?.version_root ?? msg.id;
export const audienceText = scope => scope === 'public' ? '公开' : '仅 ' + scope.map(label).join('、') + ' 可见';

/** Backend capabilities of an identity in a room, or null when unknown (server stays authoritative). */
export function capsOf(room, id) {
    const backend = room?.owners?.[id] || participantOf(id)?.connection || 'legacy';
    const caps = S.backends[id]?.[backend] || null;
    // "legacy" is the original project's terminal-agent route; Chattera starts no such agents, so nothing answers there.
    // Treat it as "no model chosen yet" rather than as a working connection.
    if (!caps && Object.keys(S.backends || {}).length)   // connections are known and this participant has none
        return { backend, caps: { available: false, unset: true, images: false, documents: false, tools: false, private: false, independent: false } };
    return { backend, caps: caps && backend === 'legacy' ? { ...caps, available: false, unset: true } : caps };
}

export function channelMessages(channel) { return S.byChannel.get(channel) || []; }
/** Effective (non-superseded) messages in display order: a new version takes the place of its root. */
export function displayMessages(channel) {
    return channelMessages(channel).filter(m => !S.replaced.has(m.id))
        .sort((a, b) => rootOf(a) - rootOf(b) || a.id - b.id);
}
export function lastActivity(channel) {
    const list = channelMessages(channel);
    return list.length ? list[list.length - 1].timestamp : (roomOf(channel)?.created_at || 0);
}
export function runningTasks(channel) {
    return [...S.tasks.values()].filter(t => t.channel === channel && t.state === 'running');
}

function addMessage(msg) {
    if (S.msgs.has(msg.id)) return false;
    S.msgs.set(msg.id, msg);
    const channel = msg.channel || 'general';
    let list = S.byChannel.get(channel);
    if (!list) S.byChannel.set(channel, list = []);
    if (list.length && list[list.length - 1].id > msg.id) { list.push(msg); list.sort((a, b) => a.id - b.id); }
    else list.push(msg);
    const old = msg.metadata?.replaces_message_id;
    if (old !== undefined && old !== null) S.replaced.add(old);
    return true;
}

// ---------- rooms ----------
export async function refreshRooms() { setRooms(await api('/api/chat/rooms')); }
function setRooms(rooms) {
    S.rooms = rooms;
    const open = rooms.filter(r => !r.archived);
    if (!open.some(r => r.channel === S.active)) {
        S.active = open.sort((a, b) => lastActivity(b.channel) - lastActivity(a.channel))[0]?.channel || null;
        store.set('room', S.active);
        emit('room', S.active);
    }
    emit('rooms');
}
export function openRoom(channel) {
    if (channel === S.active) return;
    emit('leave', S.active);
    S.active = channel;
    store.set('room', channel);
    delete S.unread[channel];
    emit('room', channel);
    emit('rooms');
    loadTasks(channel);
}
export async function loadTasks(channel = S.active) {
    const room = roomOf(channel);
    if (!room) return;
    try {
        for (const task of await api(`/api/chat/rooms/${room.id}/tasks`)) S.tasks.set(task.task_id, task);
        emit('tasks', channel);
    } catch (e) { /* status restore is best-effort; live events still arrive */ }
}
export async function loadVerification() {
    try { S.verification = await api('/api/chat/verification'); } catch (e) { S.verification = {}; }
    return S.verification;
}
export async function loadBackends() {
    try { S.backends = await api('/api/chat/backends'); emit('backends'); } catch (e) { /* server validates anyway */ }
}

// ---------- websocket ----------
let ws = null, retry = null, pending = null;

export function connect() {
    clearTimeout(retry);
    S.historyReady = false;
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    ws = new WebSocket(`${proto}://${location.host}/ws?token=${encodeURIComponent(TOKEN)}`);
    ws.onmessage = event => handle(JSON.parse(event.data));
    ws.onopen = () => { S.connected = true; emit('conn'); };
    ws.onerror = () => ws.close();
    ws.onclose = event => {
        if (event.code === 4003) { location.reload(); return; }  // server restarted: new session token
        S.connected = false; S.historyReady = false; emit('conn');
        failPending('连接已断开，消息尚未确认送达。草稿已保留，重连后请检查是否已发送。');
        retry = setTimeout(connect, 2000);
    };
}
/** Re-fetch history (e.g. after restoring an archived room, whose history is only sent on connect). */
export function reconnect() { if (ws) { ws.onclose = null; ws.close(); } S.connected = false; emit('conn'); connect(); }

function handle(event) {
    switch (event.type) {
        case 'settings':
            S.settings = event.data || {};
            S.username = S.settings.username || 'user';
            if (Array.isArray(S.settings.rooms)) setRooms(S.settings.rooms);
            emit('settings');
            // Participants or connections may have changed (here or in another window).
            if (S.models) { clearTimeout(handle.refresh); handle.refresh = setTimeout(() => { loadModels(); loadBackends(); }, 250); }
            break;
        case 'history':
            for (const msg of event.messages) addMessage(msg);
            break;
        case 'history_complete':
            S.historyReady = true;
            emit('history');
            emit('rooms');
            loadTasks();
            break;
        case 'message': {
            const msg = event.data;
            if (!addMessage(msg)) break;
            if (pending && msg.channel === pending.channel && msg.sender === S.username && !msg.metadata?.revision_event
                && msg.metadata?.shared_from === undefined) {
                const done = pending; clearTimeout(done.timer); pending = null; done.resolve(msg);
            }
            if (S.historyReady && msg.channel !== S.active && (msg.type || 'chat') === 'chat' && msg.sender !== S.username && !msg.metadata?.forked_from) {
                S.unread[msg.channel] = (S.unread[msg.channel] || 0) + 1;
            }
            emit('message', msg);
            emit('rooms');
            break;
        }
        case 'chat_library':
            emit('library', event.data.room_id);
            break;
        case 'chat_evidence':
            if (event.data.state === 'running') S.retrieving.set(event.data.message_id, event.data);
            else S.retrieving.delete(event.data.message_id);
            emit('retrieving', event.data);
            break;
        case 'chat_task':
            S.tasks.set(event.data.task_id, event.data);
            emit('tasks', event.data.channel);
            emit('rooms');
            break;
        case 'error':
            if (pending) failPending(explain(event.text));
            else emit('error', explain(event.text));
            break;
        default: break;
    }
}

function failPending(text) {
    if (!pending) return;
    const failed = pending; clearTimeout(failed.timer); pending = null; failed.reject(new Error(text));
}

/**
 * Send a chat message and wait for the server's echo of the stored message.
 * The server has no ACK frame; its own broadcast of the saved user message is the confirmation,
 * and an `error` frame (validation refused before storing) is the rejection. The composer keeps the
 * draft until this resolves.
 */
export function sendMessage(payload, wait = 30000) {
    if (!ws || ws.readyState !== WebSocket.OPEN) return Promise.reject(new Error('正在重新连接，消息尚未发送。'));
    if (pending) return Promise.reject(new Error('上一条消息仍在发送中。'));
    return new Promise((resolve, reject) => {
        pending = { channel: payload.channel, resolve, reject };
        pending.timer = setTimeout(() => failPending('没有收到服务器确认。消息可能未被保存；草稿已保留，请检查后重试。'), wait);
        ws.send(JSON.stringify(payload));
    });
}
export function sendRaw(payload) { if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify(payload)); }
