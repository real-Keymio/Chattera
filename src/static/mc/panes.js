// The room's history, shown in one to three independent reading panes.
// One history, several windows: every pane renders the same messages with its own scroll position and its own
// optional focus on one participant. Nothing here calls a model or copies chat data; quoting from any pane
// goes to the single shared composer.
import { closeMenus, h, icon, ICONS, api, toast, menu, modal, confirmDialog, promptDialog, fmtTime, fmtDay, fmtSize, explain, store } from './util.js';
import { S, IDS, ALL, on, emit, label, color, sigil, hueValue, family, activeRoom, roomOf, audienceOf, audienceText, rootOf, capsOf,
    displayMessages, runningTasks, refreshRooms, openRoom } from './state.js';
import { renderMarkdown, copyText } from './markdown.js';
import { openAttachment, isImage, validUrl, fileGlyph } from './attachments.js';
import { evidenceCard, basisStrip, openMaterials } from './evidence.js';
import { transition, skin } from './skin.js';
import { mountCanvas, unmountCanvas, saveCanvas } from './canvas.js';
import { wsGet, wsSet } from './workspace.js';
import { t } from './i18n.js';

const PAGE = 120;
const host = () => document.getElementById('panes');
const folded = new Set();          // message ids the user collapsed (this page load only)
const layouts = new Map();         // channel -> { count, widths, views: [{ filter, anchor }] }
const mdCache = new Map();         // message id -> rendered markdown element (cloned into each pane)
let panes = [];
let activePane = 0;
let presets = [];

const messageOp = (id, operation, body = {}) => api(`/api/chat/message/${id}/${operation}`, body);
const isChat = msg => (msg.type || 'chat') === 'chat';
const isShare = msg => msg.metadata?.shared_from !== undefined && msg.metadata?.shared_from !== null;
/** Share attribution. Older share events stored only `shared_from`; derive the author from that message. */
function provenanceOf(msg) {
    if (!isShare(msg)) return null;
    if (msg.metadata.share_provenance) return msg.metadata.share_provenance;
    const origin = S.msgs.get(msg.metadata.shared_from);
    if (!origin) return null;
    const earlier = provenanceOf(origin);
    return { original_author: earlier?.original_author || family(origin.sender), original_message_id: earlier?.original_message_id ?? origin.id,
        human_edited: Boolean(earlier?.human_edited) || (msg.text || '').trim() !== (origin.text || '').trim(), derived: true };
}
const authorOf = msg => { const origin = provenanceOf(msg); return origin?.original_author ? family(origin.original_author) : family(msg.sender); };
const isAnswer = msg => isChat(msg) && !isShare(msg) && ALL.includes(family(msg.sender));
const answersTo = question => displayMessages(S.active).filter(m => isAnswer(m) && m.reply_to === question);
const MAX_TILES = 6;
// Arrangement by count: 1, 2 and 3 in one row; 4 = 2 + 2; 5 = 3 + 2; 6 = 3 + 3.
const ROWS = { 1: [1], 2: [2], 3: [3], 4: [2, 2], 5: [3, 2], 6: [3, 3] };
const allowed = n => Math.max(...ROWS[n]) * 300 <= host().clientWidth && ROWS[n].length * 230 <= host().clientHeight;
/** The largest arrangement not above `n` that the window can show comfortably. */
const fit = n => { for (let k = Math.min(MAX_TILES, n); k > 1; k--) if (allowed(k)) return k; return 1; };
const maxPanes = () => fit(MAX_TILES);
const opened = new Set();   // long answers the user expanded while reading in a narrow column

function layoutOf(channel) {
    if (!layouts.has(channel)) {
        const saved = wsGet('layout', channel);
        layouts.set(channel, saved?.count ? saved : { count: 1, widths: [1], views: [{}] });
    }
    return layouts.get(channel);
}
function capture(channel = S.active) {
    if (!channel || !panes.length) return;
    const layout = layoutOf(channel);
    if (layout.mode === 'canvas') {   // the tile layout is kept as it was; only the docked chat and the canvas are saved
        layout.dock = { filter: panes[0].filter || null, anchor: panes[0].anchor() };
        wsSet('layout', channel, layout); saveCanvas(); return;
    }
    const tiles = panes.filter(p => !p.floating);
    layout.views = tiles.map(p => ({ filter: p.filter || null, anchor: p.anchor() }));
    layout.widths = tiles.map(p => Number(p.el.style.flexGrow) || 1);
    layout.count = tiles.length;
    wsSet('layout', channel, layout);
}
let saveTimer = 0;
const captureSoon = () => { clearTimeout(saveTimer); saveTimer = setTimeout(() => capture(), 500); };

function markdownOf(msg) {
    let cached = mdCache.get(msg.id);
    if (!cached) { cached = renderMarkdown(msg.text || ''); mdCache.set(msg.id, cached); }
    return cached.cloneNode(true);
}

// ---------- pane ----------
class Pane {
    constructor(index, view = {}) {
        this.index = index; this.filter = IDS.includes(view.filter) ? view.filter : null; this.saved = view.anchor || null;
        this.shown = PAGE; this.unseen = 0;
        this.inner = h('div', { class: 'pane-inner' });
        this.pending = h('div', { class: 'pane-pending' });
        this.scroller = h('div', { class: 'pane-scroll' }, this.inner, this.pending);
        this.head = h('div', { class: 'pane-head' });
        this.thumb = h('div', { class: 'score-thumb' });
        this.canvas = h('canvas');
        this.score = h('div', { class: 'score', title: t('整场讨论的缩略：每条消息一格，颜色是发言者。点击或拖动可跳转。') }, this.canvas, this.thumb);
        this.jump = h('button', { class: 'pane-jump', hidden: true, onclick: () => this.toBottom(true) }, icon(ICONS.down, 14), h('span', null, t('最新')));
        this.el = h('section', { class: 'pane', dataset: { index }, style: { viewTransitionName: 'pane-' + index } }, this.head, this.scroller, this.score, this.jump);
        if (this.filter) this.el.dataset.filter = this.filter;
        this.scroller.addEventListener('scroll', () => { this.onScroll(); captureSoon(); }, { passive: true });
        for (const name of ['pointerdown', 'wheel', 'focusin']) this.el.addEventListener(name, () => { activePane = this.index; this.touched = true; }, { passive: true });
        this.el.addEventListener('click', event => {
            if (picking()) {
                const el = event.target.closest('.msg[data-id]');
                if (el && !event.target.closest('a, .acts, .fold, .code-copy')) {
                    event.preventDefault(); event.stopPropagation();
                    const msg = S.msgs.get(Number(el.dataset.id));
                    if (msg) { togglePick(msg); drawPicks(); }
                    return;
                }
            }
            const copy = event.target.closest('.code-copy');
            if (copy) copyText(copy.closest('.code-block')?.querySelector('pre')?.textContent || '');
        });
        this.setupScore();
        this.stuck = true;
        new ResizeObserver(() => { if (this.stuck) this.scroller.scrollTop = this.scroller.scrollHeight; this.drawScoreSoon(); }).observe(this.inner);
    }

    // ----- scrolling -----
    nearBottom() { const s = this.scroller; return s.scrollHeight - s.scrollTop - s.clientHeight < 140; }
    toBottom(smooth = false) {
        this.scroller.scrollTo({ top: this.scroller.scrollHeight, behavior: smooth ? 'smooth' : 'instant' });
        this.unseen = 0; this.onScroll();
    }
    onScroll() {
        const away = !this.nearBottom();
        this.stuck = !away;
        if (!away) this.unseen = 0;
        this.jump.hidden = !away;
        this.jump.classList.toggle('fresh', this.unseen > 0);
        this.jump.lastChild.textContent = this.unseen ? t('{0} 条新消息', this.unseen) : t('最新');
        this.moveThumb();
    }
    find(id) { return this.inner.querySelector(`[data-id="${id}"]`); }
    /** First element at or below the top edge: the reading position, independent of the other panes. */
    anchor() {
        if (!this.inner.isConnected) return this.saved;
        if (this.nearBottom()) return { bottom: true };
        const top = this.scroller.getBoundingClientRect().top;
        for (const el of this.inner.querySelectorAll('[data-id]')) {
            const rect = el.getBoundingClientRect();
            if (rect.bottom > top + 2) return { id: Number(el.dataset.id), offset: Math.round(rect.top - top) };
        }
        return { bottom: true };
    }
    restore(anchor) {
        if (!anchor || anchor.bottom) { this.toBottom(); return; }
        this.reveal(anchor.id);
        const el = this.find(anchor.id);
        if (!el) { this.toBottom(); return; }
        this.scroller.scrollTop += el.getBoundingClientRect().top - this.scroller.getBoundingClientRect().top - (anchor.offset || 0);
        this.onScroll();
    }
    /** Make sure an older message is within the rendered window. */
    reveal(id) {
        const list = displayMessages(S.active);
        let index = list.findIndex(m => m.id === id);
        if (index < 0) { const msg = S.msgs.get(id); index = msg ? list.findIndex(m => rootOf(m) === rootOf(msg)) : -1; }
        if (index >= 0 && index < list.length - this.shown) { this.shown = list.length - index + 20; this.render({ keep: true }); }
        return index >= 0 ? list[index].id : null;
    }
    scrollTo(id, { block = 'center', flash = true } = {}) {
        const actual = this.reveal(id);
        const el = actual != null ? this.find(actual) : null;
        if (!el) return false;
        el.classList.remove('folded', 'stub');
        el.scrollIntoView({ block, behavior: 'smooth' });
        if (flash) { el.classList.add('flash'); setTimeout(() => el.classList.remove('flash'), 1800); }
        return true;
    }
    /** The question whose turn is at the reading position. */
    currentQuestion() {
        const at = this.anchor();
        if (!at || at.bottom) { const last = displayMessages(S.active).filter(m => isChat(m) && !ALL.includes(family(m.sender)) && !isShare(m)).at(-1); return last?.id ?? null; }
        const msg = S.msgs.get(at.id);
        if (!msg) return null;
        return isAnswer(msg) ? msg.reply_to ?? null : msg.type === 'evidence' ? msg.reply_to ?? null : msg.id;
    }
    setFilter(id) {
        const question = this.currentQuestion();
        this.filter = id || null;
        if (this.filter) this.el.dataset.filter = this.filter; else delete this.el.dataset.filter;
        this.render({ keep: true });
        if (this.filter && question != null) {
            const answer = answersTo(question).find(m => authorOf(m) === this.filter);
            if (answer) this.scrollTo(answer.id, { block: 'start' });
        }
        capture();
    }

    // ----- rendering -----
    renderHead() {
        const chips = [h('button', { class: 'focus-chip' + (this.filter ? '' : ' active'), title: t('显示完整讨论'), onclick: () => this.setFilter(null) }, t('全部')),
            ...IDS.map(id => h('button', { class: 'focus-chip' + (this.filter === id ? ' active' : ''), style: { '--hue': color(id) },
                title: t('聚焦 {0}：其他模型的回答收成一行，并定位到当前问题下它的回答', label(id)), onclick: () => this.setFilter(this.filter === id ? null : id) }, sigil(id, 'xs')))];
        this.head.replaceChildren(...[h('span', { class: 'pane-tag' }, 'ABC'[this.index] || this.index + 1), h('div', { class: 'focus-chips' }, chips),
            this.filter ? h('span', null, t('聚焦 {0}', label(this.filter))) : null, h('span', { class: 'grow' }),
            h('button', { class: 'icon-btn tiny', title: t('跳到某个提问'), onclick: event => this.questionMenu(event.currentTarget) }, icon(ICONS.history, 15)),
            h('button', { class: 'icon-btn tiny' + (this.locked ? ' on' : ''), title: this.locked ? t('已固定阅读位置：新消息到来时这一栏不滚动。点击取消') : t('固定阅读位置：新消息到来时这一栏不滚动'),
                onclick: () => { this.locked = !this.locked; this.renderHead(); } }, icon(ICONS.pin, 14)),
            panes.length > 1 && !this.floating && host().dataset.mode !== 'canvas' ? h('button', { class: 'icon-btn tiny' + (host().dataset.zoom === String(this.index) ? ' on' : ''),
                title: host().dataset.zoom === String(this.index) ? t('还原多栏') : t('最大化这一栏（其他栏保留各自的位置）'), onclick: () => toggleZoom(this.index) },
                icon(ICONS.expand, 14), host().dataset.zoom === String(this.index) ? h('span', null, t('还原')) : null) : null,
            panes.length > 1 && !this.floating && host().dataset.mode !== 'canvas' ? h('button', { class: 'icon-btn tiny', title: t('关闭这个视口'), onclick: () => closePane(this.index) }, icon(ICONS.close, 14)) : null].filter(Boolean));
    }
    questionMenu(anchor) {
        const questions = displayMessages(S.active).filter(m => isChat(m) && !isAnswer(m) && !isShare(m)).slice(-40).reverse();
        menu(Object.assign(anchor, { alignRight: true }), questions.length ? [{ title: t('跳到提问') }, ...questions.map(q => ({
            label: (q.text || t('（附件）')).replace(/\s+/g, ' ').slice(0, 34), note: fmtTime(q.timestamp),
            action: () => { const answer = this.filter ? answersTo(q.id).find(m => authorOf(m) === this.filter) : null; this.scrollTo(answer?.id ?? q.id, { block: 'start' }); } }))]
            : [{ title: t('这个聊天还没有提问') }]);
    }

    render({ keep = false, restore = false, swap = false } = {}) {
        const before = keep ? this.anchor() : null;
        const room = activeRoom(), box = this.inner;
        box.replaceChildren();
        box.classList.remove('swap');
        this.renderHead();
        if (!room) { box.append(hero(t('开始一段新的讨论'), t('点击左侧“新聊天”，选择参与者，输入问题或拖入资料。'))); this.renderPending(); return; }
        const list = displayMessages(S.active);
        const start = Math.max(0, list.length - this.shown);
        if (start > 0) box.append(h('button', { class: 'load-more', onclick: () => { this.shown += PAGE; this.render({ keep: true }); } }, t('显示更早的 {0} 条（还有 {1} 条）', Math.min(PAGE, start), start)));
        if (!list.length) box.append(S.historyReady ? starter() : hero(room.name, t('正在加载历史…')));
        let day = null;
        for (let i = start; i < list.length; i++) {
            const msg = list[i], name = fmtDay(msg.timestamp);
            if (name !== day) { day = name; box.append(h('div', { class: 'day' }, h('span', null, day))); }
            this.place(msg);
        }
        this.renderNotices();
        this.renderPending();
        if (swap) {
            const kids = [...box.children].slice(-10);
            kids.forEach((el, n) => el.style.setProperty('--n', n));
            void box.offsetWidth; box.classList.add('swap');
        }
        if (restore) this.restore(this.saved); else if (before) this.restore(before); else this.toBottom();
        this.onScroll();
        this.drawScoreSoon();
    }

    /** Put a rendered message into the list, grouping consecutive answers to the same question. */
    place(msg) {
        const box = this.inner;
        if (msg.type === 'evidence') { const card = evidenceCard(msg); box.append(card); return card; }
        const el = renderMessage(msg, this);
        if (isAnswer(msg) && msg.reply_to != null) {
            let turn = box.lastElementChild;
            if (!turn?.classList.contains('turn') || turn.dataset.q !== String(msg.reply_to)) {
                turn = h('section', { class: 'turn', dataset: { q: msg.reply_to } }, h('div', { class: 'turn-head' }), h('div', { class: 'turn-body' }));
                box.append(turn);
            }
            if (this.filter && authorOf(msg) !== this.filter) {
                el.classList.add('stub');
                el.addEventListener('click', () => el.classList.remove('stub'), { once: true });
            }
            turn.querySelector('.turn-body').append(el);
            this.turnHead(turn);
        } else box.append(el);
        return el;
    }

    turnHead(turn) {
        const answers = [...turn.querySelectorAll('.turn-body > .msg')], head = turn.querySelector('.turn-head');
        turn.classList.toggle('multi', answers.length > 1);
        head.replaceChildren();
        if (answers.length < 2) return;
        const question = Number(turn.dataset.q);
        const allFolded = answers.every(a => a.classList.contains('folded'));
        head.append(...[h('span', { class: 'turn-count' }, t('{0} 个回答', answers.length)),
            h('div', { class: 'turn-jump' }, answers.map(a => h('button', { class: 'turn-chip', style: { '--hue': color(a.dataset.author) }, title: t('跳到 {0} 的回答', label(a.dataset.author)),
                onclick: () => { a.classList.remove('folded', 'stub'); a.scrollIntoView({ block: 'start', behavior: 'smooth' }); } }, sigil(a.dataset.author, 'xs'), label(a.dataset.author)))),
            !this.find(question) && S.msgs.get(question) ? h('button', { class: 'link', onclick: () => this.scrollTo(question) }, t('查看对应提问')) : null,
            h('span', { class: 'grow' }),
            h('button', { class: 'turn-compare', title: t('复制这一轮的提问和所有已完成的回答，每条前面写着是谁说的（来自原始消息，不受折叠影响）'), onclick: () => copyTurn(question) }, icon(ICONS.copy, 14), t('复制本轮')),
            h('button', { class: 'turn-compare', title: t('把这一整轮放到画布上（在画布模式下也可以按住这一行拖过去）'), onclick: () => { setMode('canvas'); setTimeout(() => emit('canvas-add', { kind: 'turn', ref: question }), 420); } }, icon(ICONS.canvas, 14), t('放到画布')),
            h('button', { class: 'turn-compare', title: t('打开并排视口，每栏定位到一个模型对这个问题的回答'), onclick: () => compareTurn(question) }, icon(ICONS.columns, 14), t('并排对照')),
            h('button', { class: 'turn-fold', onclick: () => { answers.forEach(a => setFold(a, !allFolded)); this.turnHead(turn); } }, allFolded ? t('全部展开') : t('全部折叠'))].filter(Boolean));
    }

    append(msg) {
        const list = displayMessages(S.active), mine = msg.sender === S.username;
        const versioned = msg.metadata?.replaces_message_id != null || this.inner.querySelector(`[data-root="${rootOf(msg)}"]`);
        const stick = this.nearBottom() || (mine && this.index === Math.min(activePane, panes.length - 1));
        if (versioned || list.at(-1)?.id !== msg.id) { this.render({ keep: !stick }); return; }
        this.inner.querySelector('.hero')?.remove();
        const previous = list.at(-2);
        if (!previous || fmtDay(previous.timestamp) !== fmtDay(msg.timestamp)) this.inner.append(h('div', { class: 'day' }, h('span', null, fmtDay(msg.timestamp))));
        this.place(msg).classList.add('enter');
        this.renderNotices();
        if (stick) this.toBottom(); else { if (!mine) this.unseen++; this.onScroll(); }
        this.drawScoreSoon();
    }

    renderNotices() {
        this.inner.querySelectorAll('.msg-notices').forEach(n => n.replaceChildren());
        const latest = new Map();
        for (const task of S.tasks.values()) {
            if (task.channel !== S.active) continue;
            const key = task.current_id + ':' + task.identity, old = latest.get(key);
            if (!old || (old.created_at || 0) < (task.created_at || 0)) latest.set(key, task);
        }
        for (const task of latest.values()) {
            if (!STATE[task.state]) continue;
            const box = this.find(task.current_id)?.querySelector('.msg-notices');
            if (!box) continue;
            box.append(h('div', { class: 'msg-note ' + (task.state === 'failed' ? 'error' : 'muted'), style: { '--hue': color(task.identity) } },
                sigil(task.identity, 'xs'), h('span', null, h('strong', null, label(task.identity)), ` ${STATE[task.state]}`,
                    task.state === 'failed' && task.reason ? t('：') + friendlyReason(task.reason) : ''),
                h('button', { class: 'link', onclick: () => emit('panel', { tab: 'calls', task: task.task_id }) }, t('详情'))));
        }
    }

    renderPending() {
        const stick = this.nearBottom(), room = activeRoom();
        const grid = h('div', { class: 'pending-grid' });
        for (const data of S.retrieving.values()) {
            if (data.channel !== S.active) continue;
            grid.append(h('div', { class: 'pending search', dataset: { state: 'writing' } }, icon(ICONS.search, 16),
                h('span', { class: 'who' }, t('共享检索')), h('span', { class: 'pending-state' }, [data.request?.web ? t('网页') : null, data.request?.scholar ? t('学术文献') : null].filter(Boolean).join(' + ') + t(' · 检索一次，所有回答者共用')),
                wave()));
        }
        for (const task of room ? runningTasks(S.active) : []) {
            const state = task.output_started_at ? 'writing' : 'thinking';
            grid.append(h('div', { class: 'pending', dataset: { state }, style: { '--hue': color(task.identity) } },
                sigil(task.identity), h('span', { class: 'who' }, label(task.identity)),
                h('span', { class: 'pending-state' }, state !== 'writing' && task.activity ? (task.activity.kind === 'read' ? t('正在读 ') + (task.activity.text || '').replace(/^https?:\/\/(www\.)?/, '').slice(0, 46) : t('正在搜索：') + (task.activity.text || '').slice(0, 46))
                    : task.regenerated_from != null ? t('正在重新生成') : state === 'writing' ? t('正在写回答') : task.search && task.search.path !== 'none' ? t('正在思考，可联网') : t('正在思考')),
                h('span', { class: 'elapsed', dataset: { since: task.created_at || Date.now() / 1000 } }),
                task.steps ? h('span', { class: 'steps', title: t('这条回答到目前为止调用工具（搜索、阅读、记笔记等）的次数') }, t('{0} 步', task.steps)) : null,
                task.independent_mode ? badge(t('背靠背'), 'indep') : null, h('span', { class: 'grow' }),
                h('button', { class: 'icon-btn tiny', title: t('停止 {0}', label(task.identity)), onclick: () => stopTask(room, task.identity) }, icon(ICONS.stop, 11)), wave()));
        }
        this.pending.replaceChildren(...(grid.children.length ? [grid] : []));
        tick();
        if (stick) this.toBottom();
        this.drawScoreSoon();
    }

    // ----- score rail -----
    setupScore() {
        const seek = event => {
            const rect = this.score.getBoundingClientRect(), s = this.scroller;
            s.scrollTop = (event.clientY - rect.top) / rect.height * s.scrollHeight - s.clientHeight / 2;
        };
        this.score.addEventListener('pointerdown', event => {
            this.score.setPointerCapture(event.pointerId); seek(event);
            const move = e => seek(e), up = () => { this.score.removeEventListener('pointermove', move); this.score.removeEventListener('pointerup', up); };
            this.score.addEventListener('pointermove', move); this.score.addEventListener('pointerup', up);
        });
    }
    drawScoreSoon() { if (!this.scoreFrame) this.scoreFrame = requestAnimationFrame(() => { this.scoreFrame = 0; this.drawScore(); }); }
    drawScore() {
        const s = this.scroller, total = s.scrollHeight;
        const idle = total <= s.clientHeight + 4;
        this.score.classList.toggle('idle', idle);
        if (idle) return;
        const scale = window.devicePixelRatio || 1, canvas = this.canvas;
        const w = canvas.clientWidth, ht = canvas.clientHeight;
        if (!w || !ht) return;
        canvas.width = Math.round(w * scale); canvas.height = Math.round(ht * scale);
        const g = canvas.getContext('2d');
        g.setTransform(scale, 0, 0, scale, 0, 0);
        const style = getComputedStyle(document.documentElement), neutral = style.getPropertyValue('--ink-3').trim(), accent = style.getPropertyValue('--accent').trim();
        const top = s.getBoundingClientRect().top - s.scrollTop;
        const glow = skin() === 'expressive';
        for (const el of this.inner.querySelectorAll('.msg, .evi')) {
            const rect = el.getBoundingClientRect();
            const y = (rect.top - top) / total * ht, size = Math.max(1.5, rect.height / total * ht - 1);
            const ai = el.classList.contains('ai');
            g.fillStyle = el.classList.contains('evi') ? accent : ai ? hueValue(el.dataset.author) : neutral;
            g.globalAlpha = el.classList.contains('stub') ? .3 : ai ? (glow ? .95 : .8) : .45;
            const inset = ai || el.classList.contains('evi') ? 0 : w * .35;
            g.fillRect(inset, y, w - inset, size);
        }
        g.globalAlpha = 1;
        this.moveThumb();
    }
    moveThumb() {
        const s = this.scroller, rail = this.score.clientHeight;
        if (!rail || !s.scrollHeight) return;
        this.thumb.style.top = s.scrollTop / s.scrollHeight * rail + 'px';
        this.thumb.style.height = Math.max(14, s.clientHeight / s.scrollHeight * rail) + 'px';
    }
}

// ---------- pane layout ----------
function splitter(index) {
    const el = h('div', { class: 'splitter', title: t('拖动调整列宽；双击恢复等宽') });
    el.addEventListener('pointerdown', event => {
        const left = panes[index - 1], right = panes[index];
        const total = left.el.offsetWidth + right.el.offsetWidth, grow = (Number(left.el.style.flexGrow) || 1) + (Number(right.el.style.flexGrow) || 1);
        const startX = event.clientX, startLeft = left.el.offsetWidth;
        el.setPointerCapture(event.pointerId); el.classList.add('drag');
        const move = e => {
            const width = Math.max(200, Math.min(total - 200, startLeft + e.clientX - startX));
            left.el.style.flexGrow = grow * width / total; right.el.style.flexGrow = grow * (total - width) / total;
        };
        const up = () => { el.classList.remove('drag'); el.removeEventListener('pointermove', move); el.removeEventListener('pointerup', up); capture(); panes.forEach(p => p.drawScoreSoon()); };
        el.addEventListener('pointermove', move); el.addEventListener('pointerup', up);
    });
    el.addEventListener('dblclick', () => { panes.forEach(p => { p.el.style.flexGrow = 1; }); capture(); });
    return el;
}

/** Between the two rows of a 4-, 5- or 6-pane arrangement: drag to give one row more height. */
function rowSplitter(layout) {
    const el = h('div', { class: 'row-splitter', title: t('拖动调整上下两排的高度；双击恢复等高') });
    el.addEventListener('pointerdown', event => {
        const [top, bottom] = [el.previousElementSibling, el.nextElementSibling], total = top.offsetHeight + bottom.offsetHeight, startY = event.clientY, startTop = top.offsetHeight;
        el.setPointerCapture(event.pointerId); el.classList.add('drag');
        const move = e => { const height = Math.max(180, Math.min(total - 180, startTop + e.clientY - startY)); top.style.flexGrow = 2 * height / total; bottom.style.flexGrow = 2 * (total - height) / total; };
        const up = () => { el.classList.remove('drag'); el.removeEventListener('pointermove', move); el.removeEventListener('pointerup', up);
            layout.rowHeights = [Number(top.style.flexGrow) || 1, Number(bottom.style.flexGrow) || 1]; wsSet('layout', S.active, layout); panes.forEach(p => p.drawScoreSoon()); };
        el.addEventListener('pointermove', move); el.addEventListener('pointerup', up);
    });
    el.addEventListener('dblclick', () => { el.previousElementSibling.style.flexGrow = el.nextElementSibling.style.flexGrow = 1; layout.rowHeights = [1, 1]; wsSet('layout', S.active, layout); });
    return el;
}

function build({ swap = false } = {}) {
    const box = host(), layout = layoutOf(S.active), canvasMode = layout.mode === 'canvas';
    const count = canvasMode ? 1 : fit(layout.count || 1);
    unmountCanvas();
    box.replaceChildren();
    box.dataset.count = count; box.dataset.mode = canvasMode ? 'canvas' : 'tile'; delete box.dataset.zoom;
    panes = [];
    let i = 0;
    (canvasMode ? [1] : ROWS[count]).forEach((size, r, rows) => {
        const row = canvasMode ? box : h('div', { class: 'pane-row', style: { flexGrow: rows.length > 1 ? layout.rowHeights?.[r] || 1 : 1 } });
        if (!canvasMode) { if (r) box.append(rowSplitter(layout)); box.append(row); }
        for (let c = 0; c < size; c++, i++) {
            const pane = new Pane(i, (canvasMode ? layout.dock : layout.views?.[i]) || {});
            pane.el.style.flexGrow = layout.widths?.[i] || 1;
            panes.push(pane);
            if (c) row.append(splitter(i));
            row.append(pane.el);
        }
    });
    activePane = Math.min(activePane, count - 1);
    for (const pane of panes) pane.render({ restore: true, swap });
    if (canvasMode) {
        // The chat stays docked on the left; the canvas takes the rest. Both are the same room.
        panes[0].el.style.flex = `0 0 ${Math.round(Math.max(.22, Math.min(.6, layout.dockWidth || .36)) * 100)}%`;
        const grip = h('div', { class: 'splitter', title: t('拖动调整聊天与画布的宽度') });
        grip.addEventListener('pointerdown', event => {
            grip.setPointerCapture(event.pointerId);
            const move = e => { const r = box.getBoundingClientRect(); layout.dockWidth = Math.max(.22, Math.min(.6, (e.clientX - r.left) / r.width)); panes[0].el.style.flex = `0 0 ${Math.round(layout.dockWidth * 100)}%`; };
            const up = () => { grip.removeEventListener('pointermove', move); grip.removeEventListener('pointerup', up); wsSet('layout', S.active, layout); panes[0].drawScoreSoon(); };
            grip.addEventListener('pointermove', move); grip.addEventListener('pointerup', up);
        });
        box.append(grip);
        mountCanvas(box, S.active);
    }
    setTimeout(() => panes.forEach(p => { if (p.saved && !p.saved.bottom && !p.touched) p.restore(p.saved); }), 260);
    renderLayoutSeg();
}

export const paneCount = () => panes.length;
export function setPaneCount(count) {
    count = Math.max(1, Math.min(MAX_TILES, count));
    if (fit(count) !== count) { toast(t('窗口现在放不下 {0} 个视口，已改为 {1} 个。', count, fit(count))); count = fit(count); }
    if (count === panes.length && layoutOf(S.active).mode !== 'canvas') return;
    capture();
    const layout = layoutOf(S.active);
    layout.mode = 'tile';
    const base = layout.views[Math.min(activePane, layout.views.length - 1)] || {};
    layout.views = Array.from({ length: count }, (_, i) => layout.views[i] || { anchor: base.anchor });
    layout.widths = Array(count).fill(1);
    layout.count = count;
    wsSet('layout', S.active, layout);
    transition(() => build());
}
/** 'tile' (1–6 columns) or 'canvas' (chat docked beside a free surface). Reading positions and the draft are kept. */
export function setMode(mode) {
    const layout = layoutOf(S.active);
    if ((layout.mode || 'tile') === mode) return;
    capture();
    if (mode === 'canvas') layout.dock = layout.dock || layout.views?.[Math.min(activePane, (layout.views?.length || 1) - 1)] || {};
    layout.mode = mode;
    wsSet('layout', S.active, layout);
    transition(() => build());
}
export const paneMode = () => layoutOf(S.active).mode || 'tile';
// ----- copying with who said what, and exporting a part of the chat -----
/** "用户" for the person, the participant's name for a model. */
const speaker = msg => ALL.includes(authorOf(msg)) ? label(authorOf(msg)) : t('用户');
function messageText(msg) {
    const files = (msg.attachments || []).map(a => a.name || t('附件'));
    return (msg.text || '') + (files.length ? (msg.text ? '\n' : '') + t('（附件：{0}）', files.join(t('、'))) : '');
}
/** Plain text for pasting anywhere: each message under its speaker, in the order they were written. */
export function transcript(messages) {
    return messages.map(m => `${speaker(m)}${t('：')}\n${messageText(m).trim()}`).join('\n\n') + '\n';
}
const turnOf = question => [S.msgs.get(question), ...answersTo(question)].filter(Boolean);
export function turnMarkdown(question) { return transcript(turnOf(question)); }
async function clip(text, done) {
    try { await navigator.clipboard.writeText(text); }
    catch (e) { const area = h('textarea', { value: text, style: { position: 'fixed', opacity: 0 } }); document.body.append(area); area.select(); document.execCommand('copy'); area.remove(); }
    toast(done);
}
export async function copyTurn(question) {
    await clip(turnMarkdown(question), t('已复制本轮：1 个提问、{0} 个回答', answersTo(question).length));
}
const two = n => String(n).padStart(2, '0');
const stampOf = (date, sep = '-') => `${date.getFullYear()}${sep}${two(date.getMonth() + 1)}${sep}${two(date.getDate())}`;
/** A Markdown file of the chosen messages, with the chat's name, the time of export and who took part. */
export function exportMarkdown(messages) {
    const room = activeRoom(), now = new Date(), names = [...new Set(messages.map(speaker))];
    const head = [`# ${room?.name || t('聊天记录')}`, '',
        `- ${t('导出时间：{0} {1}:{2}', stampOf(now), two(now.getHours()), two(now.getMinutes()))}`,
        `- ${t('参与者：{0}', names.join(t('、')))}`,
        `- ${t('共 {0} 条消息（#{1}–#{2}）', messages.length, messages[0]?.id, messages.at(-1)?.id)}`, '', '---', ''];
    const body = messages.map(m => `**${speaker(m)}${t('：')}**\n\n${messageText(m).trim()}\n`);
    const text = head.join('\n') + '\n' + body.join('\n---\n\n');
    const name = `${(room?.name || 'chat').replace(/[\\/:*?"<>|\s]+/g, '-').slice(0, 60)}-${stampOf(now, '')}-${two(now.getHours())}${two(now.getMinutes())}.md`;
    const url = URL.createObjectURL(new Blob([text], { type: 'text/markdown;charset=utf-8' }));
    h('a', { href: url, download: name }).click(); setTimeout(() => URL.revokeObjectURL(url), 60000);
    toast(t('已导出 {0}', name));
}

// Selecting messages: click messages (a question takes its answers with it), then copy or export them together.
const picked = new Set();
let pickBar = null;
const pickedMessages = () => displayMessages(S.active).filter(m => picked.has(m.id)).sort((a, b) => a.id - b.id);
export const picking = () => Boolean(pickBar);
export function startPicking(first) {
    if (!pickBar) {
        pickBar = h('div', { class: 'pick-bar', role: 'toolbar' });
        document.body.append(pickBar);
        host().classList.add('picking');
    }
    if (first) togglePick(first, true);
    drawPicks();
}
export function stopPicking() {
    picked.clear(); pickBar?.remove(); pickBar = null;
    host().classList.remove('picking'); drawPicks();
}
function togglePick(msg, on) {
    const group = isAnswer(msg) || !answersTo(msg.id).length ? [msg] : turnOf(msg.id);
    const add = on ?? !picked.has(msg.id);
    for (const m of group) add ? picked.add(m.id) : picked.delete(m.id);
}
function drawPicks() {
    for (const el of document.querySelectorAll('.pane .msg[data-id]')) el.classList.toggle('picked', picked.has(Number(el.dataset.id)));
    if (!pickBar) return;
    const chosen = pickedMessages(), empty = !chosen.length;
    pickBar.replaceChildren(
        h('span', { class: 'pick-count' }, empty ? t('点选消息：点提问会连同它的回答一起选中') : t('已选 {0} 条', chosen.length)),
        h('button', { class: 'btn small', onclick: () => { displayMessages(S.active).filter(isChat).forEach(m => picked.add(m.id)); drawPicks(); } }, t('全选')),
        h('button', { class: 'btn small', disabled: empty, onclick: () => { picked.clear(); drawPicks(); } }, t('清空')),
        h('span', { class: 'grow' }),
        h('button', { class: 'btn small', disabled: empty, onclick: () => clip(transcript(pickedMessages()), t('已复制 {0} 条消息', pickedMessages().length)) }, icon(ICONS.copy, 14), t('复制')),
        h('button', { class: 'btn small primary', disabled: empty, onclick: () => exportMarkdown(pickedMessages()) }, icon(ICONS.doc, 14), t('导出 Markdown')),
        h('button', { class: 'btn small', onclick: stopPicking }, t('完成')));
}
on('room', () => { if (pickBar) stopPicking(); });
document.addEventListener('keydown', event => { if (event.key === 'Escape' && pickBar && !document.querySelector('dialog[open]')) stopPicking(); });
/** Maximise one pane over the others, or go back. Every pane keeps its own reading position, including the one that grows. */
function toggleZoom(index, land) {
    const box = host(), pane = panes[index], back = box.dataset.zoom === String(index);
    const hold = back ? pane.beforeZoom : pane.anchor();
    if (!back) pane.beforeZoom = hold;
    if (back) delete box.dataset.zoom; else box.dataset.zoom = index;
    panes.forEach(p => { p.el.classList.toggle('zoomed', !back && p === pane); p.renderHead(); p.drawScoreSoon(); });
    requestAnimationFrame(() => { if (land != null && !back) pane.scrollTo(land, { block: 'start', flash: false }); else if (hold) pane.restore(hold); });
}
/** What the canvas needs from the reading panes: the same message rendering, and live panes it can host in a window. */
export const canvasApi = {
    renderMessage(id) { const msg = S.msgs.get(id); return msg && panes[0] ? renderMessage(msg, panes[0]) : null; },
    answers(question) { return answersTo(question).map(m => m.id); },
    addPane(view) { const pane = new Pane(panes.length, view); pane.floating = true; pane.el.classList.add('floating'); panes.push(pane); pane.render({ restore: true }); return pane; },
    dropPane(pane) { panes = panes.filter(p => p !== pane); panes.forEach((p, i) => { p.index = i; p.el.dataset.index = i; }); activePane = Math.min(activePane, panes.length - 1); },
};
function closePane(index) {
    capture();
    const layout = layoutOf(S.active);
    layout.views.splice(index, 1); layout.widths = Array(layout.views.length).fill(1); layout.count = layout.views.length;
    wsSet('layout', S.active, layout);
    transition(() => build());
}
/** Side-by-side reading of one question: each pane focuses one model and lands on its answer. */
export function compareTurn(question) {
    const authors = [...new Set(answersTo(question).map(authorOf))];
    const count = fit(Math.min(authors.length, MAX_TILES));
    if (count < 2) { toast(authors.length < 2 ? t('这个问题只有一个回答。') : t('窗口太窄，放不下并排视口。')); return; }
    const layout = layoutOf(S.active);
    layout.mode = 'tile';
    layout.count = count; layout.widths = Array(count).fill(1);
    layout.views = authors.slice(0, count).map(author => ({ filter: author, anchor: { id: answersTo(question).find(m => authorOf(m) === author).id, offset: 6 } }));
    wsSet('layout', S.active, layout);
    transition(() => build());
    if (authors.length > count) toast(t('共有 {0} 个回答，当前显示 {1} 栏；在栏顶点选其他参与者可以换成它的回答。', authors.length, count));
}

const glyph = (rows, cls = '') => h('span', { class: 'lg ' + cls }, rows.map(n => h('span', null, Array.from({ length: n }, () => h('i')))));
function renderLayoutSeg() {
    const seg = document.getElementById('layout-seg'), canvasMode = paneMode() === 'canvas', tiles = panes.filter(p => !p.floating).length;
    const pick = event => {
        const grid = h('div', { class: 'lg-grid' }, Array.from({ length: MAX_TILES }, (_, i) => i + 1).map(n => h('button', { class: 'lg-opt' + (!canvasMode && n === tiles ? ' active' : ''), disabled: !allowed(n) && n > 1,
            title: allowed(n) || n === 1 ? t('{0} 个视口（Ctrl {1}）', n, n) : t('窗口现在放不下这种排列'), onclick: () => { closeMenus(); setPaneCount(n); } }, glyph(ROWS[n]), h('em', null, n))),
            h('button', { class: 'lg-opt wide' + (canvasMode ? ' active' : ''), title: t('自由画布：聊天停靠在左边，右边自由摆放回答、整轮讨论、PDF 页面、资料和笔记'), onclick: () => { closeMenus(); setMode('canvas'); } }, icon(ICONS.canvas, 18), h('em', null, t('自由画布'))));
        menu(Object.assign(event.currentTarget, { alignRight: true }), [{ title: t('阅读布局') }, { node: grid }]);
    };
    seg.replaceChildren(h('button', { class: 'lg-btn', title: t('阅读布局：1–6 个视口或自由画布'), 'aria-haspopup': 'menu', onclick: pick },
        canvasMode ? icon(ICONS.canvas, 16) : glyph(ROWS[tiles] || [1]), icon(ICONS.chevron, 12)));
}

// ---------- message rendering ----------
function hero(title, text) { return h('div', { class: 'hero' }, h('h2', null, title), h('p', null, text)); }
function starter() {
    const ideas = [
        [t('精读一篇论文'), t('我上传了一篇论文。请先概括它的研究问题、方法和主要结论，再指出你认为最薄弱的一环。')],
        [t('评审实验设计'), t('下面是我的实验设计。请从变量控制、样本量、测量效度和可能的混淆因素几个方面提出具体意见：\n\n')],
        [t('查一查最新进展'), t('这个方向最近一两年有哪些值得注意的进展？请基于检索到的资料回答，并标注来源：\n\n'), true],
        [t('一起想点子'), t('我想探索一个还不成熟的想法，请先帮我把它讲清楚，再提出三个值得追问的方向：\n\n')],
    ];
    return h('div', { class: 'hero' },
        h('div', { class: 'hero-orbit' }, IDS.map((id, i) => Object.assign(sigil(id, 'lg'), { style: `--hue:${color(id)};--i:${i * 4 / IDS.length}` }))),
        h('h2', null, t('今天想讨论什么？')),
        h('p', null, t('在下方选择参与者，直接输入问题；也可以拖入论文、图片或数据文件，或打开联网，让每位模型各自查资料。')),
        h('div', { class: 'starters' }, ideas.map(([name, text, search]) => h('button', { class: 'starter', onclick: () => emit('compose', { text, search: !!search }) }, name))));
}
function wave() { return h('span', { class: 'wave', 'aria-hidden': 'true' }, Array.from({ length: 22 }, (_, k) => h('i', { style: { '--k': (k * 7) % 11 } }))); }
function setFold(el, value) {
    el.classList.toggle('folded', value);
    const id = Number(el.dataset.id);
    value ? folded.add(id) : folded.delete(id);
    const button = el.querySelector('.fold');
    if (button) { button.title = value ? t('展开') : t('折叠'); button.setAttribute('aria-expanded', String(!value)); }
}
function badge(text, kind = '', iconName = null, title = '') {
    return h('span', { class: 'badge ' + kind, title }, iconName ? icon(ICONS[iconName], 12) : null, text);
}
function stateBadges(msg, scope) {
    const meta = msg.metadata || {}, out = [];
    if (scope !== 'public') out.push(badge(audienceText(scope), 'lock', 'lock', t('只有你和列出的模型有权读取这条消息')));
    if (meta.independent_group && scope !== 'public' && (meta.task_id || meta.source_task_id)) {
        out.push(badge(t('背靠背 · 尚未公开'), 'indep', null, t('其他模型看不到这条回答；确认后可公开分享')));
    } else if (meta.independent_mode) out.push(badge(meta.independent_mode === 'strict' ? t('严格背靠背提问') : t('背靠背提问'), 'indep'));
    if (meta.evidence) out.push(badge([meta.evidence.web ? t('网页') : null, meta.evidence.scholar ? t('学术') : null].filter(Boolean).join(' + ') + t('检索'), '', 'search', t('这条提问请求了共享检索')));
    if (meta.forked_from) out.push(badge(t('来自分支前的历史'), '', 'branch', t('这条消息是建立分支时从原聊天复制过来的')));
    if ((meta.version || 1) > 1 || meta.revision_event) {
        const kind = meta.revision_event === 'edit' ? t('已编辑') : meta.revision_event === 'restore' ? t('已恢复旧版本')
            : meta.regenerated_from !== undefined || meta.replaces_message_id != null ? t('重新生成') : t('新版本');
        out.push(h('button', { class: 'badge version', title: t('查看全部版本'), onclick: () => versionsDialog(msg) }, icon(ICONS.history, 12), `${kind} · v${meta.version || 1}`));
    }
    return out;
}

function renderMessage(msg, pane) {
    if (!isChat(msg)) return h('div', { class: 'sys', dataset: { id: msg.id } }, explain(String(msg.text || '')) || `[${msg.type}]`);
    const meta = msg.metadata || {}, shared = isShare(msg), prov = provenanceOf(msg);
    const author = authorOf(msg), ai = ALL.includes(author), scope = audienceOf(msg);
    const el = h('article', { class: 'msg ' + (ai ? 'ai' : 'user') + (shared ? ' shared' : '') + (scope !== 'public' ? ' private' : ''),
        dataset: { id: msg.id, root: rootOf(msg), author: ai ? author : msg.sender }, style: ai ? { '--hue': color(author) } : null });
    if (picked.has(msg.id)) el.classList.add('picked');
    const stamp = h('time', { class: 'msg-time', title: `#${msg.id} · ${new Date(msg.timestamp * 1000).toLocaleString('zh-CN')}` }, fmtTime(msg.timestamp));
    const acts = actions(msg, { ai, shared, scope, author, el, pane });
    const body = h('div', { class: 'msg-body' }, markdownOf(msg));
    const atts = msg.attachments?.length ? h('div', { class: 'msg-atts' }, msg.attachments.map(a => attachment(a, msg, author))) : null;
    const notices = h('div', { class: 'msg-notices' });

    if (shared) {
        const who = msg.sender === S.username ? t('你') : msg.sender;
        el.append(h('div', { class: 'share-banner' }, icon(ICONS.share, 13),
            h('span', null, t('{0}公开分享了 {1}', who, ai ? label(author) + t(' 的回答') : t('一条私聊消息'))),
            prov?.original_message_id != null ? h('button', { class: 'link', onclick: () => pane.scrollTo(prov.original_message_id) }, t('原消息 #{0}', prov.original_message_id)) : null,
            prov?.human_edited ? badge(t('经人工编辑，非逐字原文'), 'warn') : null,
            !prov ? badge(t('旧记录：缺少来源信息'), 'warn') : null, h('span', { class: 'grow' }), stamp));
    }
    if (ai) {
        const task = S.tasks.get(meta.task_id || meta.source_task_id);
        const model = task ? (task.actual_model && task.actual_model !== 'UNKNOWN' ? task.actual_model : task.requested_model) : null;
        const preview = h('span', { class: 'preview' }, (msg.text || '').replace(/\s+/g, ' ').slice(0, 110));
        const fold = h('button', { class: 'fold icon-btn tiny', title: t('折叠'), 'aria-expanded': 'true', onclick: event => { event.stopPropagation(); setFold(el, !el.classList.contains('folded')); const turn = el.closest('.turn'); if (turn) pane.turnHead(turn); } }, icon(ICONS.chevron, 14));
        el.append(h('header', { class: 'byline' }, sigil(author), h('span', { class: 'who' }, label(author)),
            shared ? null : stamp,
            !shared && model ? h('span', { class: 'model-tag', title: t('这次回答实际使用的模型{0}', meta.backend ? t(' · 连接 ') + meta.backend : '') }, model) : null,
            stateBadges(msg, shared ? 'public' : scope), preview, h('span', { class: 'grow' }), acts, shared ? null : fold));
        if (msg.reply_to != null && shared) el.append(replyRef(msg, pane));
        el.append(body);
        if (atts) el.append(atts);
        const saved = S.tasks.get(meta.task_id)?.workspace_files;
        if (saved?.length) el.append(savedFiles(saved));
        if (meta.generated_from != null && S.replaced.has(meta.generated_from)) {
            el.append(h('div', { class: 'msg-note warn' }, t('这条回答针对的是提问的旧版本（#{0}），不是基于编辑后的内容生成。', meta.generated_from)));
        }
        if (!shared && (msg.text || '').length > 900) {
            el.classList.add('long');
            if (opened.has(msg.id)) el.classList.add('unclamp');
            const flip = h('button', { onclick: event => { event.stopPropagation(); opened.has(msg.id) ? opened.delete(msg.id) : opened.add(msg.id);
                el.classList.toggle('unclamp', opened.has(msg.id)); flip.textContent = opened.has(msg.id) ? t('收起') : t('展开全文'); } }, opened.has(msg.id) ? t('收起') : t('展开全文'));
            el.append(h('div', { class: 'preview-bar' }, h('span', null, t('缩略预览')), flip,
                pane && !pane.floating && panes.length > 1 ? h('button', { title: t('把这一栏放大到整个阅读区并定位到这条回答；点栏顶的“还原”回到多栏和原来的位置'),
                    onclick: event => { event.stopPropagation(); opened.add(msg.id); el.classList.add('unclamp'); flip.textContent = t('收起'); if (host().dataset.zoom !== String(pane.index)) toggleZoom(pane.index, msg.id); } }, icon(ICONS.expand, 13), t('放大阅读')) : null));
        }
        const basis = shared ? null : basisStrip(msg, author);
        if (basis) el.append(basis);
        el.append(notices);
        if (folded.has(msg.id)) setFold(el, true);
    } else {
        const badges = stateBadges(msg, scope), targets = !shared ? (meta.targets || []).filter(x => ALL.includes(x)) : [];
        if (targets.length) el.style.setProperty('--strand', `linear-gradient(90deg, ${targets.map(color).join(', ')}${targets.length === 1 ? ', transparent' : ''})`);
        if (!shared) el.append(h('div', { class: 'u-meta' }, badges, stamp));
        if (msg.reply_to != null) el.append(replyRef(msg, pane));
        el.append(body);
        if (atts) el.append(atts);
        el.append(h('div', { class: 'u-foot' },
            targets.length ? h('span', { class: 'u-targets', title: t('这条消息点名的回答者：') + targets.map(label).join(t('、')) }, h('span', { class: 'arrow' }, '→'), targets.map(id => sigil(id, 'xs'))) : null, acts), notices);
    }
    return el;
}

function replyRef(msg, pane) {
    const parent = S.msgs.get(msg.reply_to);
    const quote = msg.metadata?.quote_text;
    const who = parent ? (ALL.includes(family(parent.sender)) ? label(family(parent.sender)) : parent.sender) : `#${msg.reply_to}`;
    const text = quote || parent?.text || '';
    return h('button', { class: 'reply-ref', title: t('跳转到被引用的消息'), onclick: () => pane.scrollTo(msg.reply_to) },
        h('span', { class: 'reply-who' }, (quote ? t('引用 ') : t('回复 ')) + who),
        h('span', { class: 'reply-text' }, text.length > 200 ? text.slice(0, 200) + '…' : text));
}

function attachment(att, msg, author) {
    if (!validUrl(att)) return null;
    const context = { messageId: msg.id, sender: author, siblings: msg.attachments };
    if (isImage(att)) {
        return h('button', { class: 'att-image', title: att.name || t('图片'), onclick: () => openAttachment(att, context) },
            h('img', { src: att.url, alt: att.name || t('图片'), loading: 'lazy' }));
    }
    return h('button', { class: 'att-file', title: t('查看附件内容'), onclick: () => openAttachment(att, context) },
        fileGlyph(att), h('span', { class: 'att-name' }, att.name || t('附件')), h('span', { class: 'att-size' }, fmtSize(att.size)));
}

/** Text the user has selected inside one message of one pane. */
function selectedQuote(within = null) {
    const selection = window.getSelection();
    if (!selection || selection.isCollapsed || selection.rangeCount !== 1) return null;
    const range = selection.getRangeAt(0);
    const start = range.startContainer;
    const body = (start.nodeType === Node.ELEMENT_NODE ? start : start.parentElement)?.closest('.msg-body');
    const message = body?.closest('.msg[data-id]');
    if (!message || !body.contains(range.endContainer) || (within && message !== within) ||
        [...body.querySelectorAll('button')].some(b => range.intersectsNode(b))) return null;
    const quote = selection.toString();
    return quote.trim() ? { message, quote, range } : null;
}

/** One click: another participant responds to this answer. The composer is prepared, nothing is sent yet. */
function askResponse(msg, author, responder, el, instruction) {
    const quote = selectedQuote(el);
    const source = quote && quote.quote.length <= 1500 ? quote.quote : '';
    const same = responder === author;
    emit('compose', {
        reply: { id: msg.id, sender: author, text: source || msg.text || '', quote: source, action: true },
        text: instruction || (same ? t('请就你的这条回答继续展开，重点说明还没有解决的问题。')
            : t('请回应 {0} 的这条回答：哪些你同意，哪些不同意，依据是什么？', label(author))),
        once: [responder],
    });
}

/** The question a message belongs to, when that round has at least one answer. */
const turnQuestion = msg => {
    const question = isAnswer(msg) ? msg.reply_to : msg.id;
    return question != null && S.msgs.get(question) && answersTo(question).length ? question : null;
};
function actions(msg, { ai, shared, scope, author, el, pane }) {
    const meta = msg.metadata || {}, room = activeRoom();
    const button = (name, title, fn) => h('button', { class: 'act', title, 'aria-label': title, onclick: event => { event.stopPropagation(); fn(event); } }, icon(ICONS[name], 15));
    const bar = h('div', { class: 'acts' });
    if (scope !== 'public') bar.append(h('button', { class: 'act text', onclick: () => shareDialog(msg, author, ai) }, icon(ICONS.share, 14), t('公开分享')));
    if (ai) {
        const others = IDS.filter(id => id !== author && capsOf(room, id).caps?.available !== false && (scope === 'public' || scope.includes(id)));
        if (others.length) bar.append(h('span', { class: 'respond-row' }, h('span', null, t('请回应')),
            others.map(id => h('button', { class: 'respond-chip', style: { '--hue': color(id) }, title: t('让 {0} 回应这条回答（会带上引用并选好回答者，可直接发送或补充问题）', label(id)),
                onclick: event => { event.stopPropagation(); askResponse(msg, author, id, el); } }, sigil(id, 'xs')))));
    }
    bar.append(button('copy', t('复制'), () => copyText(msg.text || '')));
    bar.append(button('reply', t('引用这条消息'), () => emit('compose', { reply: { id: msg.id, sender: author, text: msg.text || '' } })));
    if (ai && !shared && (meta.task_id || meta.source_task_id)) bar.append(button('redo', t('重新生成'), () => regenerate(msg, author)));
    if (!ai) bar.append(button('edit', t('编辑（保留旧版本）'), () => editDialog(msg)));
    bar.append(button('more', t('更多'), event => {
        const task = meta.task_id || meta.source_task_id;
        menu(Object.assign(event.currentTarget, { alignRight: true }), [
            ...(ai && presets.length ? [{ title: t('请模型回应（预填到输入框，不会自动发送）') },
                ...presets.map(p => ({ label: t(p.label), hint: p.hint ? t(p.hint) : '', action: () => usePreset(msg, author, p, el) })), 'sep'] : []),
            turnQuestion(msg) != null ? { label: t('复制本轮'), icon: 'copy', hint: t('提问和所有回答，每条前面写着是谁说的'), action: () => copyTurn(turnQuestion(msg)) } : null,
            { label: t('选择多条…'), icon: 'check', hint: t('选中几条或几轮消息，一起复制或导出为 Markdown 文件'), action: () => startPicking(msg) },
            ai && msg.reply_to != null && answersTo(msg.reply_to).length > 1 ? { label: t('并排对照这个问题的回答'), icon: 'columns', action: () => compareTurn(msg.reply_to) } : null,
            task ? { label: t('这次回答的依据'), icon: 'doc', action: () => openMaterials(task, author) } : null,
            scope === 'public' ? { label: t('固定到房间背景'), icon: 'pin', hint: t('之后每次提问都会带上这段内容；选中一段文字可以只固定那一段'), action: () => pinMessage(msg, el) } : null,
            { label: t('放到画布'), icon: 'canvas', hint: t('在自由画布上打开这条消息的视图，便于和其他内容并排比较'), action: () => { setMode('canvas'); setTimeout(() => emit('canvas-add', { kind: 'message', ref: msg.id }), 420); } },
            { label: t('从这里建立分支'), icon: 'branch', hint: t('新建一个聊天，带上到这条消息为止的历史'), action: () => forkFrom(msg) },
            'sep',
            { label: t('版本历史'), icon: 'history', action: () => versionsDialog(msg) },
            task ? { label: t('调用记录'), icon: 'info', action: () => emit('panel', { tab: 'calls', task }) } : null,
            { label: t('复制消息 ID（#{0}）', msg.id), icon: 'copy', action: () => copyText(String(msg.id)) },
        ]);
    }));
    return bar;
}

function usePreset(msg, author, preset, el) {
    const quote = selectedQuote(el);
    const source = quote && quote.quote.length <= 1500 ? quote.quote : '';
    const values = { author: label(author), message_id: String(msg.id), quote: (source || msg.text || '').slice(0, 250) };
    emit('compose', {
        reply: { id: msg.id, sender: author, text: source || msg.text || '', quote: source, action: true },
        text: t(preset.instruction).replace(/\{(author|message_id|quote)\}/g, (_, key) => values[key]),
        once: preset.target === 'author' ? [author] : null, search: preset.research ? 'scholar' : false,
        hint: preset.target === 'author' ? '' : t('请选择由哪些模型来回应，然后发送。'),
    });
}

async function pinMessage(msg, el) {
    const room = activeRoom(), quote = selectedQuote(el);
    try {
        await api(`/api/chat/rooms/${room.id}/pins`, { message_id: msg.id, text: quote?.quote || '' });
        await refreshRooms(); toast(t('已固定到房间背景'));
    } catch (e) { toast(e.message, 'error', 7000); }
}
function forkFrom(msg) {
    const room = activeRoom();
    promptDialog(t('从这条消息建立分支'), (room.name.slice(0, 50) + t(' · 分支')).slice(0, 80), t('建立分支'), async name => {
        const created = await api(`/api/chat/rooms/${room.id}/fork`, { message_id: msg.id, name });
        await refreshRooms(); openRoom(created.channel); toast(t('已建立分支：这里的讨论不会影响原聊天'));
    }, { note: t('新聊天会带上到这条消息为止的历史（各版本只保留当前生效的一版）、附件副本、固定内容和模型设置。原聊天不变。') });
}

// ---------- task status ----------
const STATE = { failed: t('没有回答'), cancelled: t('已停止') };

/** Turn a recorded failure into something actionable. The raw reason stays available under “调用记录”. */
export function friendlyReason(reason = '') {
    const status = /API HTTP status (\d{3})/.exec(reason);
    if (status) {
        const code = +status[1], params = /provider-referenced parameters: (.+)$/.exec(reason);
        const text = code === 401 || code === 403 ? t('服务商拒绝了认证，请检查这个连接的 Key 是否有效、是否有权使用该模型')
            : code === 404 ? t('接口地址或模型 ID 不存在，请检查连接的地址和模型名称')
            : code === 429 ? t('被服务商限流，或账户额度不足') : code === 400 || code === 422 ? t('请求被服务商拒绝，参数或模型不被接受')
            : code >= 500 ? t('服务商或中转网关出错，可稍后重试') : t('服务商返回了错误');
        return t('{0}（HTTP {1}{2}）。', text, code, params ? t('，涉及参数：') + params[1] : '');
    }
    const stage = /stage=([^;]+); protocol=(\S+)/.exec(reason);
    if (stage) {
        const s = stage[1];
        const text = s.startsWith('connect timeout') ? t('连接服务商超时，没有建立连接') : s.startsWith('response timeout') ? t('已连上服务商，但没有在限时内返回')
            : s.startsWith('connect') ? t('无法连接服务商（DNS、网络或 TLS）') : s.startsWith('transport') ? t('传输过程中断')
            : s.startsWith('response body') ? t('服务商返回的内容不是 JSON，地址或协议可能不匹配') : t('服务商返回的 JSON 与所选协议不符，协议设置可能不对');
        return t('{0}（协议 {1}）。', text, stage[2]);
    }
    const cli = /(\w+) CLI exit (-?\d+)/.exec(reason);
    const said = /; CLI said: (.*)$/.exec(reason)?.[1];
    if (cli && said && /authenticat|log ?in|oauth|api key|credential/i.test(said)) return t('{0} CLI 没有登录或登录已失效，没有写入回复。请先在终端里登录这个 CLI（Claude Code：claude auth login），再重试。CLI 的提示：{1}', cli[1], said);
    if (cli && said) return t('{0} CLI 异常退出（代码 {1}），没有写入回复。CLI 的提示：{2}', cli[1], cli[2], said);
    if (cli) return t('{0} CLI 异常退出（代码 {1}），没有写入回复。可能是订阅登录失效、模型 ID 不被接受或 CLI 已更新，可在终端里手动运行一次确认。', cli[1], cli[2]);
    if (/anthropic-workspace-id/.test(reason)) return t('Anthropic 要求带上工作区 ID：这把 Key 没有绑定到工作区。在连接的“兼容性”里填写工作区 ID，或改用绑定了工作区的 Key。');
    if (/API cumulative request cap/.test(reason)) return t('被本机设置的 API 累计请求上限拦截（不是服务商余额不足）。');
    if (/incomplete or unsupported stop state/.test(reason)) return t('回答在完成前被截断（通常是达到了单次输出上限）。可在设置 → 模型与服务里调高这个连接的输出上限。');
    if (/per-task request\/tool round limit|tool-call limit/.test(reason)) return t('模型连续调用工具的次数达到上限，没有给出最终回答。可以重试，或把问题拆小。');
    const limit = /Reply time limit reached \((\d+) min\)/.exec(reason);
    if (limit) return t('这条回答达到了时长上限（{0} 分钟）。可以重试，或在设置 → 检索与工具里调长“每条回答最长时长”，也可以设为不限。', limit[1]);
    if (/API cancelled or total deadline|CLI total timeout|timed? ?out/i.test(reason)) return t('超过了这次调用的总时限。可以重试、缩短输入，或在设置 → 模型与服务里调大超时。');
    if (/server restart recovery/.test(reason)) return t('服务重启时这次调用被中断，请重新提问。');
    if (/does not support image/.test(reason)) return t('这个连接不支持图片输入。');
    if (/did not return a (successful|completed) final result|turn failed/.test(reason)) return t('CLI 运行结束但没有给出最终回答，可重试一次。');
    if (/API transport or response schema failure/.test(reason)) return t('API 调用失败（网络传输或返回格式问题；这条较早的记录没有保存具体阶段）。');
    return explain(reason);
}

/** Files a reply saved in the chat folder; they open in a new tab. */
function savedFiles(paths) {
    const room = activeRoom();
    return h('div', { class: 'msg-files' }, h('span', { class: 'msg-files-head' }, icon(ICONS.doc, 13), t('存进了聊天文件夹')),
        paths.map(path => h('a', { class: 'file-chip', href: room ? `/api/chat/rooms/${room.id}/files/raw?path=${encodeURIComponent(path)}` : '#', target: '_blank', rel: 'noopener', title: path }, path)),
        room ? h('button', { class: 'link', onclick: () => api(`/api/chat/rooms/${room.id}/files/open`, {}).catch(e => toast(e.message, 'error')) }, t('打开文件夹')) : null);
}

function tick() {
    for (const el of document.querySelectorAll('.elapsed[data-since]')) {
        const seconds = Math.max(0, Math.round(Date.now() / 1000 - Number(el.dataset.since)));
        el.textContent = seconds < 60 ? t('{0} 秒', seconds) : t('{0} 分 {1} 秒', Math.floor(seconds / 60), seconds % 60);
    }
}
export async function stopTask(room, model) {
    try { await api(`/api/chat/rooms/${room.id}/stop`, model ? { model } : {}); }
    catch (e) { toast(e.message, 'error'); }
}

// ---------- message operations ----------
function editDialog(msg) {
    const input = h('textarea', { class: 'input area', value: msg.text || '', 'aria-label': t('消息内容') });
    modal({
        title: t('编辑消息'), wide: true,
        body: [input, h('p', { class: 'hint' }, t('保存后生成新版本，旧版本仍可查看和恢复。已有的模型回答仍然对应原提问，保存不会自动唤醒模型；需要新回答请再次发送提问。'))],
        actions: [{ label: t('保存新版本'), primary: true, action: () => messageOp(msg.id, 'edit', { text: input.value }) }],
    });
    input.focus();
}
function shareDialog(msg, author, ai) {
    const input = h('textarea', { class: 'input area', value: msg.text || '', 'aria-label': t('将要公开的内容') });
    modal({
        title: t('公开分享这条私有消息'), wide: true,
        body: [
            h('p', { class: 'modal-text' }, t('原作者 '), ai ? [sigil(author, 'xs'), ' ', h('strong', null, label(author))] : h('strong', null, msg.sender), t(' · 当前{0}', audienceText(audienceOf(msg)))),
            input,
            h('ul', { class: 'hint list' },
                h('li', null, t('确认后会新增一条公开的“分享”消息，所有模型之后都可以读取；原私有消息及其可见范围不变。')),
                h('li', null, t('分享内容保留原作者身份，作为被引用的内容而不是你的新指令。如果你修改了文本，会标注“经人工编辑”。')),
                h('li', null, t('分享不会自动唤醒任何模型，也不会带上原消息的附件。'))),
        ],
        actions: [{ label: t('确认公开'), primary: true, action: () => messageOp(msg.id, 'share', { confirm: true, text: input.value }) }],
    });
}
function regenerate(msg, author) {
    confirmDialog(t('重新生成这条回答'), t('{0} 将使用原提问时冻结的输入快照（含当时的检索资料与分析框架）和当时的模型设置重新回答，不会包含之后产生的消息或摘要。新回答会替换当前显示的版本，旧版本可在“版本历史”中查看和恢复。', label(author)),
        t('重新生成'), () => messageOp(msg.id, 'regenerate'));
}
async function versionsDialog(msg) {
    let versions;
    try { versions = await messageOp(msg.id, 'versions'); } catch (e) { toast(e.message, 'error'); return; }
    const replaced = new Set(versions.map(v => v.metadata?.replaces_message_id).filter(v => v != null));
    const dialog = modal({ title: t('版本历史（共 {0} 个）', versions.length), wide: true, body: [] });
    for (const version of [...versions].reverse()) {
        const current = !replaced.has(version.id);
        const event = version.metadata?.revision_event;
        dialog.body.append(h('section', { class: 'version' + (current ? ' current' : '') },
            h('div', { class: 'version-head' },
                h('strong', null, `v${version.metadata?.version || 1}`), h('span', null, `#${version.id} · ${fmtTime(version.timestamp)}`),
                event ? badge(event === 'edit' ? t('人工编辑') : t('恢复'), '') : null,
                current ? badge(t('当前生效'), 'ok') : h('button', {
                    class: 'btn small', onclick: async click => {
                        click.currentTarget.disabled = true;
                        try { await messageOp(version.id, 'restore'); dialog.close(); toast(t('已恢复为新版本')); }
                        catch (e) { dialog.error(e.message); click.currentTarget.disabled = false; }
                    },
                }, t('恢复此版本'))),
            h('div', { class: 'version-text' }, version.text)));
    }
    dialog.body.append(h('p', { class: 'hint' }, t('恢复会追加一个新版本，不会删除任何历史版本，也不会重新调用模型。')));
}

// ---------- navigation ----------
export function scrollToMessage(id, paneIndex = activePane) {
    const msg = S.msgs.get(Number(id));
    if (!msg) { toast(t('这条消息不在已加载的历史中。')); return; }
    if (msg.channel !== S.active) return;
    panes[Math.min(paneIndex, panes.length - 1)]?.scrollTo(msg.id);
}
/** Latest answer of a participant, in the pane the user last touched. */
export function jumpToLatest(id) {
    const answer = displayMessages(S.active).filter(m => isAnswer(m) && authorOf(m) === id).at(-1);
    if (!answer) { toast(t('{0} 在这个聊天里还没有回答。', label(id))); return; }
    scrollToMessage(answer.id);
}
export function focusParticipant(id) { panes[Math.min(activePane, panes.length - 1)]?.setFilter(id); }

// ---------- selection quote ----------
function setupSelectionQuote() {
    const button = h('button', { class: 'quote-float', type: 'button', hidden: true }, icon(ICONS.reply, 14), t('引用'));
    document.body.append(button);
    const position = () => {
        const selected = selectedQuote();
        button.hidden = !selected;
        if (!selected) return;
        const rect = selected.range.getBoundingClientRect();
        if (rect.bottom <= 0 || rect.top >= window.innerHeight) { button.hidden = true; return; }
        button.disabled = selected.quote.length > 1500;
        button.title = button.disabled ? t('引用最多 1500 字，请缩短选区。') : t('把选中内容引用到输入框（不会自动发送）');
        const top = rect.top - button.offsetHeight - 8;
        button.style.top = (top >= 4 ? top : Math.min(rect.bottom + 8, window.innerHeight - button.offsetHeight - 4)) + 'px';
        button.style.left = Math.max(4, Math.min(rect.left, window.innerWidth - button.offsetWidth - 4)) + 'px';
    };
    button.addEventListener('mousedown', event => event.preventDefault());  // keep the selection until click reads it
    button.addEventListener('click', () => {
        const selected = selectedQuote();
        if (!selected || selected.quote.length > 1500) return;
        const msg = S.msgs.get(Number(selected.message.dataset.id));
        if (!msg) return;
        emit('compose', { reply: { id: msg.id, sender: selected.message.dataset.author, text: selected.quote, quote: selected.quote } });
        window.getSelection().removeAllRanges();
        button.hidden = true;
    });
    document.addEventListener('selectionchange', position);
    document.addEventListener('scroll', position, { passive: true, capture: true });
    window.addEventListener('resize', position);
}

// ---------- wiring ----------
export function initPanes() {
    setupSelectionQuote();
    setInterval(tick, 1000);
    build();
    on('leave', channel => capture(channel));
    on('room', () => { mdCache.clear(); transition(() => build({ swap: true })); });
    on('history', () => build());
    on('message', msg => {
        mdCache.delete(msg.metadata?.replaces_message_id);
        if (!S.historyReady || msg.channel !== S.active) return;
        for (const pane of panes) pane.append(msg);
    });
    on('tasks', channel => {
        if (channel !== S.active) return;
        for (const pane of panes) {
            pane.renderNotices(); pane.renderPending();
            // A finished call brings the model tag and the "依据" strip for an answer that is already on screen.
            for (const task of S.tasks.values()) {
                if (task.channel !== S.active || task.state !== 'completed' || task.reply_id == null) continue;
                const el = pane.find(task.reply_id);
                if (el && !el.dataset.audited) {
                    el.dataset.audited = '1';
                    const msg = S.msgs.get(task.reply_id);
                    if (msg && !el.querySelector('.basis')) { const fresh = renderMessage(msg, pane); fresh.className = el.className; fresh.classList.remove('enter'); el.replaceWith(fresh); fresh.dataset.audited = '1'; }
                }
            }
        }
    });
    on('goto-message', id => scrollToMessage(id, 0));
    on('retrieving', data => { if (data.channel === S.active) for (const pane of panes) pane.renderPending(); });
    on('before-skin', () => capture());
    on('skin', () => {
        // Text reflows when the skin changes; put every pane back on the message it was reading (twice: fonts settle late).
        const again = () => { const layout = layoutOf(S.active); panes.forEach((p, i) => { p.restore(layout.views[i]?.anchor); p.drawScoreSoon(); }); };
        requestAnimationFrame(again); setTimeout(again, 320);
    });
    // Names and marks come from the participant list: when it changes, redraw what shows them.
    let namesShown = '';
    on('models', () => {
        const names = (S.models?.participants || []).map(p => p.id + '=' + p.label + (p.retired ? '-' : '') + (p.hidden ? '~' : '')).join('|');
        // Also on the first list when the history was drawn before it arrived: until then only the four default ids count as participants.
        if (names !== namesShown && (namesShown || S.historyReady)) { capture(); panes.forEach(p => p.render({ restore: true })); }
        namesShown = names;
        panes.forEach(p => p.renderHead());
    });
    let resizeTimer = 0;
    window.addEventListener('resize', () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => { if (panes.length > maxPanes()) { capture(); build(); } panes.forEach(p => p.drawScoreSoon()); }, 150); });
    window.addEventListener('beforeunload', () => capture());
    fetch('/static/discussion-actions.json').then(r => r.ok ? r.json() : []).then(value => {
        if (Array.isArray(value)) presets = value.filter(p => p && typeof p.label === 'string' && typeof p.instruction === 'string'
            && p.instruction.length <= 1000 && ['author', 'choose'].includes(p.target));
    }).catch(() => { /* quick prompts are optional */ });
}

// A pane whose reading position is locked stays where it is when messages arrive or the list is redrawn.
for (const name of ['append', 'render']) {
    const original = Pane.prototype[name];
    Pane.prototype[name] = function (...args) {
        const hold = this.locked ? this.anchor() : null;
        const out = original.apply(this, args);
        if (hold) this.restore(hold);
        return out;
    };
}
