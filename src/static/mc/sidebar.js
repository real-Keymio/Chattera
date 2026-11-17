// Chat-style sidebar: one-click new chat, switch, search, rename/archive/restore, permanent delete.
import { h, icon, ICONS, api, toast, menu, promptDialog, confirmDialog, fmtTime, store } from './util.js';
import { S, IDS, ALL, on, emit, label, family, roomOf, openRoom, refreshRooms, lastActivity, runningTasks,
    channelMessages, reconnect, forgetChannel } from './state.js';
import { scrollToMessage } from './panes.js';
import { focusComposer } from './composer.js';
import { t } from './i18n.js';

let query = '';
let showArchived = store.get('show-archived', false);
const list = () => document.getElementById('room-list');

export async function newChat() {
    try {
        const taken = new Set(S.rooms.map(r => r.name));
        let name = t('新聊天');
        for (let n = 2; taken.has(name); n++) name = t('新聊天 {0}', n);
        const room = await api('/api/chat/rooms', { name });
        await refreshRooms();
        openRoom(room.channel);
        focusComposer();
    } catch (e) { toast(e.message, 'error'); }
}

export function renameRoom(room) {
    promptDialog(t('重命名聊天'), room.name, t('保存'), async name => {
        if (!name) throw new Error(t('名称不能为空。'));
        await api('/api/chat/rooms/' + room.id, { name }); await refreshRooms();
    }, { note: t('只改变显示名称；聊天的固定 ID、历史与权限关系不变。') });
}
export function archiveRoom(room) {
    confirmDialog(t('归档聊天'), t('归档“{0}”会停止其中正在进行的回复，并把它从列表中收起。全部历史都会保留，可随时在“已归档”中恢复。', room.name), t('归档'), async () => {
        await api('/api/chat/rooms/' + room.id, { archived: true }); await refreshRooms();
    });
}
export function deleteRoom(room) {
    confirmDialog(t('永久删除聊天'), t('“{0}”的全部消息、附件、回答记录、摘要、画布和资料库会从这台电脑上删除，无法恢复。正在进行的回复会先停止。从它建立的分支是独立的副本，不受影响。', room.name),
        t('永久删除'), async () => {
            await api('/api/chat/rooms/' + room.id + '/delete', { confirm: true });
            forgetChannel(room.channel); await refreshRooms(); toast(t('已删除“{0}”', room.name));
        }, true);
}
function emptyArchive(rooms) {
    confirmDialog(t('清空已归档'), t('{0} 个已归档聊天会被永久删除，连同它们的消息、附件、回答记录、摘要、画布和资料库，无法恢复。', rooms.length),
        t('全部永久删除'), async () => {
            await api('/api/chat/archived/delete', { confirm: true });
            rooms.forEach(r => forgetChannel(r.channel)); await refreshRooms(); toast(t('已删除 {0} 个聊天', rooms.length));
        }, true);
}
async function restoreRoom(room) {
    try {
        await api('/api/chat/rooms/' + room.id, { archived: false });
        await refreshRooms();
        reconnect();  // history of archived rooms is only delivered on (re)connect
        openRoom(room.channel);
    } catch (e) { toast(e.message, 'error'); }
}

function roomMenu(room, anchor) {
    menu(anchor, room.archived ? [
        { label: t('恢复聊天'), icon: 'archive', action: () => restoreRoom(room) },
        'sep',
        { label: t('永久删除…'), icon: 'close', danger: true, action: () => deleteRoom(room) },
    ] : [
        { label: t('重命名'), icon: 'edit', action: () => renameRoom(room) },
        { label: t('房间设置'), icon: 'sliders', action: () => { openRoom(room.channel); emit('panel', { tab: 'discussion' }); } },
        'sep',
        { label: t('归档'), icon: 'archive', action: () => archiveRoom(room) },
        { label: t('永久删除…'), icon: 'close', danger: true, action: () => deleteRoom(room) },
    ]);
}

function roomRow(room) {
    const running = runningTasks(room.channel).length, unread = S.unread[room.channel];
    const row = h('div', { class: 'room' + (room.channel === S.active ? ' active' : '') + (room.archived ? ' archived' : ''), role: 'button', tabIndex: 0,
        title: room.name, onclick: () => room.archived ? roomMenu(room, row) : openRoom(room.channel),
        onkeydown: event => { if (event.key === 'Enter') row.click(); },
        oncontextmenu: event => { event.preventDefault(); roomMenu(room, { x: event.clientX, y: event.clientY }); } },
        h('span', { class: 'room-name' }, room.forked_from ? icon(ICONS.branch, 12) : null, room.name),
        running ? h('span', { class: 'room-running', title: t('{0} 个模型正在回复', running) }) : null,
        unread ? h('span', { class: 'room-unread', title: t('{0} 条新消息', unread) }, unread > 99 ? '99+' : unread) : null,
        h('button', { class: 'icon-btn tiny room-more', title: t('更多'), 'aria-label': t('“{0}”的更多操作', room.name),
            onclick: event => { event.stopPropagation(); roomMenu(room, event.currentTarget); } }, icon(ICONS.more, 15)));
    return row;
}

function group(ts) {
    const days = (Date.now() / 1000 - ts) / 86400;
    const today = new Date(); today.setHours(0, 0, 0, 0);
    if (ts * 1000 >= today.getTime()) return t('今天');
    if (days < 7) return t('近 7 天');
    if (days < 30) return t('近 30 天');
    return t('更早');
}

function snippet(text, q) {
    const at = text.toLowerCase().indexOf(q), start = Math.max(0, at - 18);
    const part = text.slice(start, at + q.length + 50).replace(/\s+/g, ' ');
    const offset = at - start;
    return [start > 0 ? '…' : '', part.slice(0, offset), h('mark', null, part.slice(offset, offset + q.length)), part.slice(offset + q.length)];
}

function renderSearch(box) {
    const q = query.toLowerCase();
    const rooms = S.rooms.filter(r => r.name.toLowerCase().includes(q));
    if (rooms.length) box.append(h('div', { class: 'room-group' }, t('聊天')), ...rooms.map(roomRow));
    const hits = [];
    for (const room of S.rooms.filter(r => !r.archived)) {
        for (const msg of channelMessages(room.channel)) {
            if ((msg.type || 'chat') !== 'chat' || S.replaced.has(msg.id) || !String(msg.text || '').toLowerCase().includes(q)) continue;
            hits.push({ room, msg });
        }
    }
    hits.sort((a, b) => b.msg.id - a.msg.id);
    if (hits.length) box.append(h('div', { class: 'room-group' }, t('消息（{0}）', hits.length > 60 ? t('最近 60 / ') + hits.length : hits.length)));
    for (const { room, msg } of hits.slice(0, 60)) {
        const who = ALL.includes(family(msg.sender)) ? label(family(msg.sender)) : msg.sender;
        box.append(h('button', { class: 'hit', onclick: () => { openRoom(room.channel); setTimeout(() => scrollToMessage(msg.id), 60); } },
            h('span', { class: 'hit-top' }, h('span', { class: 'hit-room' }, room.name), h('span', null, fmtTime(msg.timestamp))),
            h('span', { class: 'hit-text' }, h('b', null, who + t('：')), snippet(String(msg.text), q))));
    }
    if (!rooms.length && !hits.length) box.append(h('p', { class: 'sb-empty' }, t('没有匹配的聊天或消息。')));
    box.append(h('p', { class: 'sb-empty' }, t('搜索范围：聊天名称，以及未归档聊天中已加载的消息。归档聊天需恢复后才能搜索其内容。')));
}

export function renderSidebar() {
    const box = list();
    box.replaceChildren();
    if (query) { renderSearch(box); return; }
    const open = S.rooms.filter(r => !r.archived).sort((a, b) => lastActivity(b.channel) - lastActivity(a.channel));
    const archived = S.rooms.filter(r => r.archived);
    let current = null;
    for (const room of open) {
        const g = group(lastActivity(room.channel));
        if (g !== current) { current = g; box.append(h('div', { class: 'room-group' }, g)); }
        box.append(roomRow(room));
    }
    if (!open.length) box.append(h('p', { class: 'sb-empty' }, t('还没有聊天。点击上方“新聊天”开始。')));
    if (archived.length) {
        box.append(h('button', { class: 'room-group toggle' + (showArchived ? ' open' : ''), 'aria-expanded': String(showArchived),
            onclick: () => { showArchived = !showArchived; store.set('show-archived', showArchived); renderSidebar(); } },
            icon(ICONS.chevron, 12), t('已归档（{0}）', archived.length)));
        if (showArchived) box.append(...archived.map(roomRow),
            h('button', { class: 'room-purge', onclick: () => emptyArchive(archived) }, t('清空已归档…')));
    }
}

export function initSidebar() {
    const app = document.getElementById('app');
    const setCollapsed = value => { app.classList.toggle('sb-collapsed', value); store.set('sb-collapsed', value); };
    setCollapsed(store.get('sb-collapsed', window.innerWidth < 900));
    document.getElementById('sb-collapse').addEventListener('click', () => setCollapsed(true));
    document.getElementById('sb-open').addEventListener('click', () => setCollapsed(false));
    document.getElementById('new-chat').addEventListener('click', newChat);
    const search = document.getElementById('search');
    search.addEventListener('input', () => { query = search.value.trim(); renderSidebar(); });
    search.addEventListener('keydown', event => { if (event.key === 'Escape') { search.value = ''; query = ''; renderSidebar(); } });
    document.addEventListener('keydown', event => {
        if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); setCollapsed(false); search.focus(); search.select(); }
        if ((event.ctrlKey || event.metaKey) && event.shiftKey && event.key.toLowerCase() === 'o') { event.preventDefault(); newChat(); }
    });
    on('room', () => { if (window.innerWidth < 900) setCollapsed(true); });
    on('rooms', renderSidebar);
    on('conn', () => {
        const el = document.getElementById('conn');
        el.textContent = S.connected ? '' : t('正在重新连接…');
        el.classList.toggle('off', !S.connected);
    });
    renderSidebar();
}
export { roomOf };
