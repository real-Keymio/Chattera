// Workspace canvas: a free surface next to the chat where views of the same room can be laid out side by side.
// Nothing here is a copy of the conversation. Every window is a view of something the room already has:
//   message  one message, rendered exactly as in the chat          view    a live reading pane (optionally one participant)
//   quote    a passage selected in a message, tied to its origin   pdf     one page of an attached PDF
//   source   an item of the research library                       note    the user's own working note
// Windows can be moved, resized, overlapped, pinned and maximised; the surface pans and zooms. Related windows are joined
// by a line (answer ↔ question, quote → its message, source ↔ the answer that found or cites it).
// Layout is saved per room by the server, in the data folder (see workspace.js).
import { h, icon, ICONS, menu, store, toast, TOKEN } from './util.js';
import { S, IDS, ALL, on, emit, label, color, sigil, activeRoom, roomOf } from './state.js';
import { canvasApi, scrollToMessage, copyTurn } from './panes.js';
import { loadLibrary, libraryItems, sourceBody } from './library.js';
import { wsGet, wsSet } from './workspace.js';

let root = null, world = null, links = null, hint = null, zoomLabel = null, channel = null, state = null, top = 1, focused = null;
const nodes = new Map();   // window id -> {win, el, body, pane?}
const DEFAULT = { turn: [640, 660], message: [520, 420], view: [460, 560], quote: [360, 200], pdf: [520, 680], source: [440, 420], note: [320, 220] };
const NAMES = { message: '消息', view: '对话视图', quote: '引用', pdf: 'PDF 页面', source: '资料', note: '笔记' };

const load = name => { const saved = wsGet('canvas', name); return saved?.windows ? saved : { x: 40, y: 30, k: 1, windows: [] }; };
let saveTimer = 0;
export function saveCanvas() { if (channel && state) wsSet('canvas', channel, state); }
const saveSoon = () => { clearTimeout(saveTimer); saveTimer = setTimeout(saveCanvas, 300); };
const uid = () => 'w' + Math.random().toString(36).slice(2, 9);

function applyView(animate = false) {
    world.classList.toggle('anim', animate);
    world.style.transform = `translate(${state.x}px, ${state.y}px) scale(${state.k})`;
    root.style.setProperty('--cv-x', state.x + 'px'); root.style.setProperty('--cv-y', state.y + 'px'); root.style.setProperty('--cv-k', state.k);
    zoomLabel.textContent = Math.round(state.k * 100) + '%';
    if (animate) setTimeout(() => world.classList.remove('anim'), 420);
    saveSoon();
}
function zoomAt(factor, cx, cy, animate = false) {
    const k = Math.max(.25, Math.min(2, state.k * factor));
    state.x = cx - (cx - state.x) * (k / state.k); state.y = cy - (cy - state.y) * (k / state.k); state.k = k;
    applyView(animate);
}
function fitAll() {
    if (!state.windows.length) { Object.assign(state, { x: 40, y: 30, k: 1 }); applyView(true); return; }
    const box = bounds(state.windows), view = root.getBoundingClientRect(), pad = 48;
    const k = Math.max(.25, Math.min(1, (view.width - pad * 2) / box.w, (view.height - pad * 2 - 30) / box.h));
    Object.assign(state, { k, x: (view.width - box.w * k) / 2 - box.x * k, y: (view.height - box.h * k) / 2 - box.y * k + 10 });
    applyView(true);
}
const bounds = list => {
    const x = Math.min(...list.map(w => w.x)), y = Math.min(...list.map(w => w.y));
    return { x, y, w: Math.max(...list.map(w => w.x + w.w)) - x, h: Math.max(...list.map(w => w.y + w.h)) - y };
};
function tidy() {
    // Unpinned windows flow into rows, widest row = what the viewport shows at the current zoom.
    const view = root.getBoundingClientRect(), width = Math.max(900, view.width / state.k - 80), gap = 28;
    let x = 0, y = 0, row = 0;
    for (const win of state.windows.filter(w => !w.pin)) {
        if (x && x + win.w > width) { x = 0; y += row + gap; row = 0; }
        win.x = x; win.y = y; x += win.w + gap; row = Math.max(row, win.h);
        place(win, true);
    }
    setTimeout(fitAll, 30); drawLinks(); saveSoon();
}
const toWorld = (clientX, clientY) => { const r = root.getBoundingClientRect(); return [(clientX - r.left - state.x) / state.k, (clientY - r.top - state.y) / state.k]; };

function place(win, animate = false) {
    const node = nodes.get(win.id); if (!node) return;
    node.el.classList.toggle('anim', animate);
    Object.assign(node.el.style, { left: win.x + 'px', top: win.y + 'px', width: win.w + 'px', height: win.h + 'px', zIndex: win.z });
    node.el.classList.toggle('pinned', Boolean(win.pin));
    if (animate) setTimeout(() => node.el.classList.remove('anim'), 420);
}
function raise(win) {
    if (win.z !== top) win.z = ++top;
    nodes.get(win.id)?.el.style.setProperty('z-index', win.z);
    focused = win.id;
    for (const [id, node] of nodes) node.el.classList.toggle('focus', id === win.id);
    saveSoon();
}

// ---------- window contents ----------
function titleOf(win) {
    if (win.kind === 'message' || win.kind === 'quote') {
        const msg = S.msgs.get(win.ref), ai = msg && ALL.includes(msg.sender);
        return [ai ? sigil(msg.sender, 'xs') : icon(win.kind === 'quote' ? ICONS.reply : ICONS.doc, 14), h('span', null, `${win.kind === 'quote' ? '引用 · ' : ''}${msg ? (ai ? label(msg.sender) : '你') : '消息'} #${win.ref}`)];
    }
    if (win.kind === 'turn') return [icon(ICONS.users, 14), h('span', null, `一轮讨论 #${win.ref} · ${canvasApi.answers(win.ref).length} 个回答`)];
    if (win.kind === 'view') return [win.ref ? sigil(win.ref, 'xs') : icon(ICONS.columns, 14), h('span', null, win.ref ? `${label(win.ref)} 的回答` : '完整对话')];
    if (win.kind === 'pdf') return [icon(ICONS.doc, 14), h('span', null, `${win.name || 'PDF'} · 第 ${win.page || 1} 页`)];
    if (win.kind === 'source') { const item = libraryItems(roomOf(channel)).find(i => i.id === win.ref); return [icon(item?.kind === 'paper' ? ICONS.book : ICONS.globe, 14), h('span', null, item?.title || '资料')]; }
    return [icon(ICONS.edit, 14), h('span', null, '笔记')];
}
async function fill(win) {
    const node = nodes.get(win.id), body = node.body, room = roomOf(channel);
    node.el.querySelector('.cw-title').replaceChildren(...titleOf(win));
    if (win.kind === 'turn') {
        // A whole round as one window: the question, then each participant's full answer, each foldable and detachable.
        const ids = canvasApi.answers(win.ref), folded = win.folded ||= {};
        const keep = body.scrollTop;
        const part = (id, n) => {
            const msg = S.msgs.get(id), who = msg?.sender, shut = Boolean(folded[id]);
            return h('section', { class: 'cw-part' + (shut ? ' shut' : ''), style: { '--hue': ALL.includes(who) ? color(who) : 'var(--accent)' } },
                h('header', { class: 'cw-part-head', onclick: event => { if (event.target.closest('button')) return; folded[id] = !shut; saveSoon(); fill(win); } },
                    ALL.includes(who) ? sigil(who, 'xs') : null, h('strong', null, ALL.includes(who) ? label(who) : who), h('span', { class: 'hint' }, '#' + id), h('span', { class: 'grow' }),
                    h('button', { class: 'tiny-btn', title: '把这条回答拆成单独的窗口', onclick: () => addWindow({ kind: 'message', ref: id }, [win.x + win.w + 28, win.y + n * 46]) }, '拆出'),
                    h('span', { class: 'cw-chev' }, icon(ICONS.chevron, 13))),
                shut ? null : h('div', { class: 'cw-part-body' }, canvasApi.renderMessage(id) || h('p', { class: 'hint' }, '这条回答不在当前加载的历史里。')));
        };
        const all = ids.every(id => folded[id]);
        body.replaceChildren(
            h('div', { class: 'cw-turn-q' }, canvasApi.renderMessage(win.ref) || h('p', { class: 'hint' }, '提问不在当前加载的历史里。')),
            h('div', { class: 'cw-turn-bar' }, h('span', { class: 'hint' }, `${ids.length} 个回答`), h('span', { class: 'grow' }),
                h('button', { class: 'tiny-btn', onclick: () => { ids.forEach(id => { folded[id] = !all; }); saveSoon(); fill(win); } }, all ? '全部展开' : '全部折叠'),
                h('button', { class: 'tiny-btn', onclick: () => copyTurn(win.ref) }, '复制本轮')),
            ...ids.map(part));
        body.scrollTop = keep;
    } else if (win.kind === 'message') {
        const el = canvasApi.renderMessage(win.ref);
        body.replaceChildren(el || h('p', { class: 'hint' }, '这条消息不在当前加载的历史里。'));
        const msg = S.msgs.get(win.ref);
        node.el.style.setProperty('--hue', msg && ALL.includes(msg.sender) ? color(msg.sender) : 'var(--accent)');
    } else if (win.kind === 'quote') {
        const msg = S.msgs.get(win.ref);
        node.el.style.setProperty('--hue', msg && ALL.includes(msg.sender) ? color(msg.sender) : 'var(--accent)');
        body.replaceChildren(h('blockquote', { class: 'cw-quote' }, win.text), h('button', { class: 'tiny-btn', onclick: () => scrollToMessage(win.ref, 0) }, `在聊天中查看 #${win.ref}`));
    } else if (win.kind === 'view') {
        if (!node.pane) { node.pane = canvasApi.addPane({ filter: win.ref || null, anchor: win.anchor || null }); body.replaceChildren(node.pane.el); }
        node.el.style.setProperty('--hue', win.ref ? color(win.ref) : 'var(--accent)');
    } else if (win.kind === 'note') {
        if (!body.firstChild) body.append(h('textarea', { class: 'cw-note', placeholder: '在这里记下想法、待查的问题、对比结论…（只保存在这台电脑的浏览器里）', value: win.text || '',
            oninput: event => { win.text = event.target.value; saveSoon(); } }));
    } else if (win.kind === 'pdf') {
        const show = async () => {
            body.replaceChildren(h('p', { class: 'hint' }, '正在渲染页面…'));
            try {
                const address = win.item ? `/api/chat/rooms/${room.id}/library/${win.item}/page?page=${win.page || 1}` : `/api/chat/attachments/page?url=${encodeURIComponent(win.ref)}&page=${win.page || 1}`;
                const response = await fetch(address, { headers: { 'X-Session-Token': TOKEN } });
                if (!response.ok) throw new Error(win.page > 1 ? '没有这一页了。' : '这个文件无法渲染为页面。');
                const step = delta => () => { win.page = Math.max(1, (win.page || 1) + delta); saveSoon(); fill(win); };
                body.replaceChildren(h('div', { class: 'cw-pdf-bar' }, h('button', { class: 'tiny-btn', disabled: (win.page || 1) <= 1, onclick: step(-1) }, '上一页'), h('span', null, `第 ${win.page || 1} 页`), h('button', { class: 'tiny-btn', onclick: step(1) }, '下一页')),
                    h('img', { class: 'cw-pdf', src: URL.createObjectURL(await response.blob()), alt: `${win.name || 'PDF'} 第 ${win.page || 1} 页`, draggable: false }));
            } catch (e) { body.replaceChildren(h('p', { class: 'form-error' }, e.message), h('button', { class: 'tiny-btn', onclick: () => { win.page = Math.max(1, (win.page || 1) - 1); fill(win); } }, '返回上一页')); }
        };
        show();
    } else if (win.kind === 'source') {
        body.replaceChildren(h('p', { class: 'hint' }, '正在读取资料…'));
        try {
            const item = (await loadLibrary(room)).find(i => i.id === win.ref);
            body.replaceChildren(item ? sourceBody(item, room, { onchange: () => { fill(win); drawLinks(); } }) : h('p', { class: 'hint' }, '这份资料已经不在资料库里。'));
            if (item) { node.el.querySelector('.cw-title').replaceChildren(...titleOf(win)); node.el.style.setProperty('--hue', item.holders[0] ? color(item.holders[0]) : 'var(--accent)'); }
        } catch (e) { body.replaceChildren(h('p', { class: 'form-error' }, e.message)); }
        drawLinks();
    }
}

function mount(win, fresh = false) {
    const button = (name, title, fn, cls = '') => h('button', { class: 'cw-btn ' + cls, title, 'aria-label': title, onclick: event => { event.stopPropagation(); fn(); } }, icon(ICONS[name], 13));
    const body = h('div', { class: 'cw-body' });
    const head = h('header', { class: 'cw-head' }, h('span', { class: 'cw-title' }),
        button('pin', '固定位置：整理画布时不移动它', () => { win.pin = !win.pin; place(win); saveSoon(); }, 'pin'),
        h('button', { class: 'cw-btn', title: '层级与更多', 'aria-label': '更多', onclick: event => { event.stopPropagation(); menu(Object.assign(event.currentTarget, { alignRight: true }), [
            { label: '移到最上层', action: () => raise(win) },
            { label: '移到最下层', action: () => { win.z = Math.min(...state.windows.map(w => w.z || 1)) - 1; place(win); saveSoon(); } },
            { label: '恢复默认大小', action: () => { [win.w, win.h] = DEFAULT[win.kind] || [420, 320]; place(win, true); setTimeout(drawLinks, 430); saveSoon(); } },
            win.kind === 'turn' ? { label: '复制本轮回答', icon: 'copy', action: () => copyTurn(win.ref) } : null,
            (win.kind === 'message' || win.kind === 'quote' || win.kind === 'turn') ? { label: '在聊天中定位', icon: 'down', action: () => scrollToMessage(win.ref, 0) } : null]); } }, icon(ICONS.more, 13)),
        button('expand', '放大到整个视野 / 还原', () => maximise(win)),
        button('close', '从画布移除（不影响聊天内容）', () => remove(win)));
    const el = h('section', { class: 'cw' + (fresh ? ' fresh' : ''), dataset: { kind: win.kind, id: win.id } }, head, body,
        h('i', { class: 'cw-edge e', dataset: { edge: 'e' } }), h('i', { class: 'cw-edge s', dataset: { edge: 's' } }), h('i', { class: 'cw-edge w', dataset: { edge: 'w' } }), h('i', { class: 'cw-resize', title: '拖动调整大小', dataset: { edge: 'se' } }));
    nodes.set(win.id, { win, el, body });
    world.append(el);
    place(win);
    el.addEventListener('pointerdown', () => raise(win), true);
    head.addEventListener('pointerdown', event => {
        if (event.button || event.target.closest('button')) return;
        const start = [event.clientX, event.clientY, win.x, win.y];
        head.setPointerCapture(event.pointerId); el.classList.add('dragging');
        const move = e => { win.x = start[2] + (e.clientX - start[0]) / state.k; win.y = start[3] + (e.clientY - start[1]) / state.k; place(win); drawLinks(); };
        const up = () => { el.classList.remove('dragging'); head.removeEventListener('pointermove', move); head.removeEventListener('pointerup', up); saveSoon(); };
        head.addEventListener('pointermove', move); head.addEventListener('pointerup', up);
    });
    head.addEventListener('dblclick', event => { if (!event.target.closest('button')) maximise(win); });
    for (const grip of el.querySelectorAll('[data-edge]')) grip.addEventListener('pointerdown', event => {
        event.stopPropagation(); event.preventDefault();
        const start = [event.clientX, event.clientY, win.w, win.h, win.x], edge = grip.dataset.edge;
        grip.setPointerCapture(event.pointerId); el.classList.add('sizing');
        const move = e => {
            const dx = (e.clientX - start[0]) / state.k, dy = (e.clientY - start[1]) / state.k;
            if (edge.includes('e')) win.w = Math.max(240, start[2] + dx);
            if (edge === 'w') { win.w = Math.max(240, start[2] - dx); win.x = start[4] + start[2] - win.w; }
            if (edge.includes('s')) win.h = Math.max(120, start[3] + dy);
            place(win); drawLinks();
        };
        const up = () => { el.classList.remove('sizing'); grip.removeEventListener('pointermove', move); grip.removeEventListener('pointerup', up); nodes.get(win.id)?.pane?.drawScoreSoon?.(); saveSoon(); };
        grip.addEventListener('pointermove', move); grip.addEventListener('pointerup', up);
    });
    fill(win);
    return el;
}
function maximise(win) {
    const view = root.getBoundingClientRect();
    if (win.back) { Object.assign(state, win.back); delete win.back; applyView(true); return; }
    win.back = { x: state.x, y: state.y, k: state.k };
    const k = Math.max(.25, Math.min(1.6, (view.width - 60) / win.w, (view.height - 70) / win.h));
    Object.assign(state, { k, x: (view.width - win.w * k) / 2 - win.x * k, y: (view.height - win.h * k) / 2 - win.y * k + 12 });
    raise(win); applyView(true);
}
function remove(win) {
    const node = nodes.get(win.id);
    if (node?.pane) canvasApi.dropPane(node.pane);
    node?.el.classList.add('leaving');
    setTimeout(() => node?.el.remove(), 180);
    nodes.delete(win.id);
    state.windows = state.windows.filter(w => w !== win);
    hint.hidden = state.windows.length > 0;
    drawLinks(); saveSoon();
}
/** Put a view on the canvas. `at`: world coordinates of its top-left corner; default is a free spot in the current view. */
export function addWindow(spec, at) {
    if (!root) return;
    const same = state.windows.find(w => w.kind === spec.kind && w.ref === spec.ref && spec.kind !== 'note' && spec.kind !== 'quote' && (spec.kind !== 'pdf' || w.page === (spec.page || 1)));
    if (same) { raise(same); nodes.get(same.id).el.classList.add('fresh'); setTimeout(() => nodes.get(same.id)?.el.classList.remove('fresh'), 500); return same; }
    const [w, h0] = DEFAULT[spec.kind] || [420, 320], view = root.getBoundingClientRect();
    const n = state.windows.length;
    const [x, y] = at || toWorld(view.left + 60 + (n % 6) * 34, view.top + 70 + (n % 6) * 30);
    const win = { id: uid(), w, h: h0, z: ++top, ...spec, x: Math.round(x), y: Math.round(y) };
    state.windows.push(win);
    mount(win, true);
    hint.hidden = true;
    raise(win); drawLinks(); saveSoon();
    return win;
}

// ---------- relations between windows ----------
function related(a, b) {
    const ma = (a.kind === 'message' || a.kind === 'quote') ? S.msgs.get(a.ref) : null, mb = b.kind === 'message' ? S.msgs.get(b.ref) : null;
    if (a.kind === 'turn' && mb && (mb.id === a.ref || mb.reply_to === a.ref || mb.metadata?.generated_from === a.ref)) return 'reply';
    if (a.kind === 'pdf' && a.item && b.kind === 'source' && b.ref === a.item) return 'file';
    if (a.kind === 'message' && mb && ma) {
        const up = m => [m.reply_to, m.metadata?.generated_from].filter(v => v != null);
        if (up(ma).includes(mb.id) || up(mb).includes(ma.id)) return 'reply';
    }
    if (a.kind === 'quote' && b.kind === 'message' && a.ref === b.ref) return 'quote';
    if (a.kind === 'source' && mb) {
        const item = libraryItems(roomOf(channel)).find(i => i.id === a.ref);
        if (item?.citations.some(c => c.message_id === mb.id)) return 'cite';
        if (item?.finders.some(f => f.message_id === mb.id)) return 'found';
    }
    if (a.kind === 'pdf' && mb && (mb.attachments || []).some(att => att.url === a.ref)) return 'file';
    return null;
}
function drawLinks() {
    if (!links) return;
    const out = [], list = state.windows;
    for (const a of list) for (const b of list) {
        if (a === b) continue;
        const kind = related(a, b);
        if (!kind) continue;
        const ax = a.x + a.w / 2, ay = a.y + a.h / 2, bx = b.x + b.w / 2, by = b.y + b.h / 2;
        const horizontal = Math.abs(bx - ax) > Math.abs(by - ay);
        const [x1, y1] = horizontal ? [bx > ax ? a.x + a.w : a.x, ay] : [ax, by > ay ? a.y + a.h : a.y];
        const [x2, y2] = horizontal ? [bx > ax ? b.x : b.x + b.w, by] : [bx, by > ay ? b.y : b.y + b.h];
        const bend = horizontal ? `C ${(x1 + x2) / 2} ${y1}, ${(x1 + x2) / 2} ${y2}, ${x2} ${y2}` : `C ${x1} ${(y1 + y2) / 2}, ${x2} ${(y1 + y2) / 2}, ${x2} ${y2}`;
        out.push(`<path class="${kind}" d="M ${x1} ${y1} ${bend}"/><circle class="${kind}" cx="${x2}" cy="${y2}" r="3.5"/>`);
    }
    links.innerHTML = out.join('');
}

// ---------- surface ----------
const turnsOf = name => (S.byChannel.get(name) || []).filter(m => (m.type || 'chat') === 'chat' && !ALL.includes(m.sender) && canvasApi.answers(m.id).length > 0);
function addMenu(anchor) {
    const room = roomOf(channel);
    const pdfs = [];
    for (const msg of S.byChannel.get(channel) || []) for (const att of msg.attachments || []) if (/\.pdf$/i.test(att.name || att.url) && !pdfs.some(p => p.url === att.url)) pdfs.push(att);
    menu(anchor, [
        { title: '放到画布上' },
        { label: '一条笔记', icon: 'edit', action: () => addWindow({ kind: 'note', text: '' }) },
        ...turnsOf(channel).slice(-6).reverse().map(q => ({ label: `整轮：${(q.text || '').replace(/\s+/g, ' ').slice(0, 22)}`, icon: 'users', hint: '提问和它的全部回答，作为一个窗口', action: () => addWindow({ kind: 'turn', ref: q.id }) })),
        { label: '完整对话视图', icon: 'columns', hint: '一个独立滚动的阅读窗口', action: () => addWindow({ kind: 'view', ref: null }) },
        ...IDS.map(id => ({ label: `${label(id)} 的回答`, hint: '只看这位参与者的回答，独立滚动', action: () => addWindow({ kind: 'view', ref: id }) })),
        pdfs.length ? 'sep' : null, pdfs.length ? { title: 'PDF 页面' } : null,
        ...pdfs.slice(-8).map(att => ({ label: att.name || 'PDF', icon: 'doc', action: () => addWindow({ kind: 'pdf', ref: att.url, name: att.name, page: 1 }) })),
        'sep', { label: '资料库里的文献…', icon: 'book', hint: '打开房间的资料页，把条目拖到画布上', action: () => emit('panel', { tab: 'library' }) },
        { node: h('p', { class: 'menu-help' }, '也可以直接从左边的聊天里拖：按住一条消息的标题栏拖过来，或选中一段文字拖过来。') },
    ]);
}

export function unmountCanvas() {
    if (!root) return;
    saveCanvas();
    for (const node of nodes.values()) if (node.pane) canvasApi.dropPane(node.pane);
    nodes.clear(); root = world = links = null; channel = null; state = null;
}
export function mountCanvas(box, name) {
    channel = name; state = load(name); top = Math.max(1, ...state.windows.map(w => w.z || 1));
    world = h('div', { class: 'cv-world' });
    links = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); links.setAttribute('class', 'cv-links');
    world.append(links);
    zoomLabel = h('button', { class: 'cv-zoom', title: '恢复 100%', onclick: () => { const r = root.getBoundingClientRect(); zoomAt(1 / state.k, r.width / 2, r.height / 2, true); } });
    hint = h('div', { class: 'cv-hint' }, h('strong', null, '自由画布'), h('p', null, '把左边聊天里的回答拖到这里并排比较：按住消息的标题栏拖动，或选中一段文字拖过来。'),
        h('p', null, '资料库的文献、PDF 的某一页和你自己的笔记也可以放上来。拖动空白处平移，Ctrl + 滚轮缩放。'));
    const tool = (name, title, fn, text) => h('button', { class: 'cv-tool', title, onclick: fn }, name ? icon(ICONS[name], 15) : null, text || null);
    const bar = h('div', { class: 'cv-bar' },
        tool('plus', '添加笔记、对话视图、PDF 页面或资料', event => addMenu(event.currentTarget), '添加'),
        h('span', { class: 'cv-sep' }),
        tool(null, '缩小', () => { const r = root.getBoundingClientRect(); zoomAt(1 / 1.2, r.width / 2, r.height / 2, true); }, '−'), zoomLabel,
        tool(null, '放大', () => { const r = root.getBoundingClientRect(); zoomAt(1.2, r.width / 2, r.height / 2, true); }, '+'),
        tool('eye', '看到全部窗口', fitAll, '全览'), tool('columns', '整理：把没有固定的窗口排整齐', tidy, '整理'));
    root = h('div', { class: 'canvas', tabIndex: 0 }, world, hint, bar, h('div', { class: 'cv-drop' }));
    box.append(root);
    for (const win of state.windows) mount(win);
    hint.hidden = state.windows.length > 0;
    applyView();
    loadLibrary(roomOf(name)).then(drawLinks).catch(() => {});
    drawLinks();

    root.addEventListener('pointerdown', event => {
        const free = event.button === 1 || (spaceDown && event.button === 0 && !event.target.closest('textarea, input'));
        if (!free && event.target !== root && event.target !== world && event.target !== links && !event.target.closest('.cv-hint')) return;
        if (event.button > 1) return;
        if (free) { event.preventDefault(); event.stopPropagation(); }
        const start = [event.clientX, event.clientY, state.x, state.y];
        root.setPointerCapture(event.pointerId); root.classList.add('panning');
        const move = e => { state.x = start[2] + e.clientX - start[0]; state.y = start[3] + e.clientY - start[1]; applyView(); };
        const up = () => { root.classList.remove('panning'); root.removeEventListener('pointermove', move); root.removeEventListener('pointerup', up); };
        root.addEventListener('pointermove', move); root.addEventListener('pointerup', up);
    }, true);
    root.addEventListener('wheel', event => {
        const r = root.getBoundingClientRect();
        if (event.ctrlKey || event.metaKey) { event.preventDefault(); zoomAt(Math.exp(-event.deltaY * .0016), event.clientX - r.left, event.clientY - r.top); return; }
        // Over a window whose content can still scroll that way, let it scroll; otherwise the wheel moves the surface.
        let el = event.target.closest('.cw') ? event.target : null;
        for (; el && el !== root; el = el.parentElement) {
            const s = getComputedStyle(el).overflowY;
            if ((s === 'auto' || s === 'scroll') && el.scrollHeight > el.clientHeight + 1 && (event.deltaY < 0 ? el.scrollTop > 0 : el.scrollTop + el.clientHeight < el.scrollHeight - 1)) return;
        }
        event.preventDefault();
        state.x -= event.deltaX; state.y -= event.deltaY; applyView();
    }, { passive: false });
    root.addEventListener('keydown', event => {
        if (event.target.closest('textarea, input')) return;
        const win = state.windows.find(w => w.id === focused);
        if ((event.key === 'Delete' || event.key === 'Backspace') && win) { event.preventDefault(); remove(win); }
        if (event.key === 'Escape' && win?.back) maximise(win);
    });
    // drop from the chat, the library or the desktop selection
    root.addEventListener('dragover', event => {
        if (![...event.dataTransfer.types].some(t => t === 'application/x-multichat' || t === 'text/plain')) return;
        event.preventDefault(); event.dataTransfer.dropEffect = 'copy';
        const r = root.getBoundingClientRect(), ghost = root.querySelector('.cv-drop');
        ghost.style.transform = `translate(${event.clientX - r.left - 30}px, ${event.clientY - r.top - 16}px)`; root.classList.add('dropping');
    });
    root.addEventListener('dragleave', event => { if (!root.contains(event.relatedTarget)) root.classList.remove('dropping'); });
    root.addEventListener('drop', event => {
        root.classList.remove('dropping');
        let spec = null;
        try { spec = JSON.parse(event.dataTransfer.getData('application/x-multichat') || 'null'); } catch (e) { spec = null; }
        const text = event.dataTransfer.getData('text/plain');
        if (!spec && text?.trim() && dragOrigin != null) spec = { kind: 'quote', ref: dragOrigin, text: text.trim().slice(0, 4000) };
        if (!spec) return;
        event.preventDefault();
        const [x, y] = toWorld(event.clientX, event.clientY);
        addWindow(spec, [x - 30, y - 16]);
    });
}

// ---------- dragging things onto the canvas ----------
// Done with pointer events rather than the browser's drag-and-drop: it starts reliably, never fights text selection,
// and shows what is being carried. A message is picked up by its title line, a whole round by its summary bar,
// a library source by its row, and a passage by pressing on text already selected inside one message (it becomes a
// quote card tied to that message).
let dragOrigin = null, spaceDown = false, carry = null;
/** The selected passage under the pointer, if it lies inside one message of the docked chat. */
function selectionAt(event) {
    const sel = getSelection();
    if (!sel || sel.isCollapsed || !sel.rangeCount) return null;
    // The message is where the selection starts: a triple-click selection ends at the start of the next block, which
    // can lie outside the message, so the range's common ancestor is not reliable.
    const range = sel.getRangeAt(0), node = range.startContainer;
    const msg = (node.nodeType === 1 ? node : node.parentElement)?.closest?.('#panes[data-mode=canvas] > .pane .msg');
    const text = sel.toString().trim();
    if (!msg || !text) return null;
    const inside = [...range.getClientRects()].some(r => event.clientX >= r.left - 3 && event.clientX <= r.right + 3 && event.clientY >= r.top - 3 && event.clientY <= r.bottom + 3);
    return inside ? { msg, text: text.slice(0, 4000) } : null;
}
addEventListener('keydown', event => { if (event.code === 'Space' && root && !event.target.closest?.('textarea, input, [contenteditable]')) { spaceDown = true; root.classList.add('space'); if (root.contains(event.target) || event.target === document.body) event.preventDefault(); } });
addEventListener('keyup', event => { if (event.code === 'Space') { spaceDown = false; root?.classList.remove('space'); } });
document.addEventListener('pointerdown', event => {
    if (event.button !== 0 || !root || spaceDown) return;
    const row = event.target.closest?.('.lib-head');
    if (!row && event.target.closest?.('button, a, input, select, textarea')) return;
    const bar = event.target.closest?.('#panes[data-mode=canvas] > .pane .turn.multi > .turn-head');
    const head = event.target.closest?.('#panes[data-mode=canvas] > .pane .msg > .byline, #panes[data-mode=canvas] > .pane .msg > .u-meta');
    let spec = null, name = '';
    const picked = !row && !bar && !head ? selectionAt(event) : null;
    if (picked) { spec = { kind: 'quote', ref: Number(picked.msg.dataset.id), text: picked.text }; name = '引用 · ' + picked.text.slice(0, 28) + (picked.text.length > 28 ? '…' : ''); }
    else if (bar) { spec = { kind: 'turn', ref: Number(bar.closest('.turn').dataset.q) }; name = `整轮讨论 · ${canvasApi.answers(spec.ref).length} 个回答`; }
    else if (head) { const msg = head.closest('.msg'); spec = { kind: 'message', ref: Number(msg.dataset.id) }; name = `${ALL.includes(msg.dataset.author) ? label(msg.dataset.author) : '提问'} #${msg.dataset.id}`; }
    else if (row) { const item = row.closest('.lib-item'); spec = { kind: 'source', ref: item.dataset.id }; name = row.querySelector('strong')?.textContent.slice(0, 40) || '资料'; }
    if (spec) carry = { spec, name, x: event.clientX, y: event.clientY, ghost: null };
}, true);
function startCarry(x, y) {
    carry.ghost = h('div', { class: 'cv-carry' }, icon(ICONS[{ turn: 'users', source: 'book', quote: 'reply' }[carry.spec.kind] || 'doc'], 14), carry.name);
    carry.ghost.style.transform = `translate(${x + 12}px, ${y + 10}px)`;
    document.body.append(carry.ghost); document.body.classList.add('cv-carrying');
    getSelection()?.removeAllRanges();
}
document.addEventListener('pointermove', event => {
    if (!carry) return;
    if (!carry.ghost) {
        if (Math.hypot(event.clientX - carry.x, event.clientY - carry.y) < 7) return;
        startCarry(event.clientX, event.clientY);
    }
    carry.ghost.style.transform = `translate(${event.clientX + 12}px, ${event.clientY + 10}px)`;
    const r = root.getBoundingClientRect(), over = event.clientX >= r.left && event.clientX <= r.right && event.clientY >= r.top && event.clientY <= r.bottom;
    root.classList.toggle('dropping', over); carry.over = over;
    if (over) root.querySelector('.cv-drop').style.transform = `translate(${event.clientX - r.left - 30}px, ${event.clientY - r.top - 16}px)`;
});
const finishCarry = event => {
    if (!carry) return;
    const done = carry; carry = null;
    if (!done.ghost) return;
    done.ghost.remove(); document.body.classList.remove('cv-carrying'); root?.classList.remove('dropping');
    // the click that follows a drag must not also toggle the row or follow a link
    addEventListener('click', e => { e.stopPropagation(); e.preventDefault(); }, { capture: true, once: true });
    setTimeout(() => {}, 0);
    if (done.over && root && event.type === 'pointerup') { const [x, y] = toWorld(event.clientX, event.clientY); addWindow(done.spec, [x - 30, y - 16]); }
};
document.addEventListener('pointerup', finishCarry);
document.addEventListener('pointercancel', finishCarry);
document.addEventListener('dragstart', event => {
    // Pressing on a selection makes the browser start its own drag of that text after a few pixels, often before the
    // carry has begun; that native drag would cancel the pointer stream and never reach the canvas. Refuse it and
    // start the carry right away instead.
    if (carry?.spec.kind === 'quote') { event.preventDefault(); if (!carry.ghost) startCarry(event.clientX, event.clientY); return; }
    const msg = event.target.closest?.('#panes .msg');
    if (msg) dragOrigin = Number(msg.dataset.id);
});

on('canvas-add', spec => { if (root) addWindow(spec); });
on('message', msg => { if (root && msg.channel === channel) for (const { win } of nodes.values()) if (win.kind === 'turn' && (msg.reply_to === win.ref || msg.metadata?.replaces_message_id != null)) fill(win); });
on('message', msg => { if (root && msg.channel === channel) for (const { win } of nodes.values()) if (win.kind === 'message' && (msg.metadata?.replaces_message_id === win.ref)) { win.ref = msg.id; fill(win); } });
for (const name of ['models']) on(name, () => { if (root) for (const { win } of nodes.values()) if (win.kind === 'message') fill(win); });
on('library', () => { if (root) { for (const { win } of nodes.values()) if (win.kind === 'source') fill(win); drawLinks(); } });
