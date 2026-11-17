// Small DOM/network helpers shared by every Chattera UI module.
import { t } from './i18n.js';
export const TOKEN = window.__SESSION_TOKEN__ || '';

/** h('div', {class:'x', onclick: fn}, child, 'text') — never parses HTML. */
export function h(tag, props, ...kids) {
    const el = document.createElement(tag);
    for (const [key, value] of Object.entries(props || {})) {
        if (value === null || value === undefined || value === false) continue;
        if (key === 'class') el.className = value;
        else if (key === 'dataset') Object.assign(el.dataset, value);
        else if (key === 'style' && typeof value === 'object') {
            for (const [name, v] of Object.entries(value)) name.startsWith('--') ? el.style.setProperty(name, v) : (el.style[name] = v);
        }
        else if (key.startsWith('on')) el.addEventListener(key.slice(2), value);
        else if (key in el && key !== 'list') el[key] = value;
        else el.setAttribute(key, value === true ? '' : value);
    }
    append(el, kids);
    return el;
}

function append(el, kids) {
    for (const kid of kids) {
        if (kid === null || kid === undefined || kid === false) continue;
        if (Array.isArray(kid)) append(el, kid);
        else el.append(kid.nodeType ? kid : String(kid));
    }
}

export function icon(path, size = 16) {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('width', size); svg.setAttribute('height', size);
    svg.setAttribute('aria-hidden', 'true');
    for (const d of [].concat(path)) {
        const p = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        p.setAttribute('d', d); svg.append(p);
    }
    return svg;
}

export const ICONS = {
    expand: ['M4 9V4h5', 'M20 15v5h-5', 'M4 4l6 6', 'M20 20l-6-6'], canvas: ['M3 5h18v14H3z', 'M7 9h6v5H7z', 'M15 12h3v4h-3z'],
    plus: 'M12 5v14M5 12h14', close: 'M6 6l12 12M18 6 6 18', more: ['M5 12h.01', 'M12 12h.01', 'M19 12h.01'],
    copy: ['M9 9h10v10H9z', 'M5 15V5h10'], reply: ['M9 14 4 9l5-5', 'M4 9h10a6 6 0 0 1 6 6v4'],
    redo: ['M20 11a8 8 0 1 0-2.3 5.7', 'M20 4v7h-7'], share: ['M4 12v7h16v-7', 'M12 15V4', 'm8 8 4-4 4 4'],
    edit: ['M4 20h4L19 9l-4-4L4 16z'], lock: ['M6 11h12v9H6z', 'M8 11V8a4 4 0 0 1 8 0v3'],
    clip: 'M20 11.5 12 19.5a5 5 0 0 1-7-7l8.5-8.5a3.3 3.3 0 0 1 4.7 4.7L9.7 17.2a1.7 1.7 0 0 1-2.4-2.4l7.5-7.5',
    send: ['M12 19V5', 'm5 12 7-7 7 7'], stop: 'M7 7h10v10H7z', sliders: ['M4 7h10M18 7h2M4 17h4M12 17h8', 'M16 5v4M10 15v4'],
    chevron: 'm6 9 6 6 6-6', history: ['M4 12a8 8 0 1 0 2.5-5.8', 'M4 4v5h5', 'M12 8v4l3 2'],
    archive: ['M4 7h16v3H4z', 'M6 10v9h12v-9', 'M10 14h4'], info: ['M12 11v6', 'M12 7h.01', 'M3 12a9 9 0 1 0 18 0 9 9 0 0 0-18 0'],
    search: ['M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14z', 'm20 20-4-4'], globe: ['M3 12a9 9 0 1 0 18 0 9 9 0 0 0-18 0', 'M3 12h18', 'M12 3c2.8 3 2.8 15 0 18M12 3c-2.8 3-2.8 15 0 18'],
    book: ['M5 4h11a3 3 0 0 1 3 3v13H8a3 3 0 0 1-3-3z', 'M5 17a3 3 0 0 1 3-3h11'], pin: ['M9 4h6l-1 6 3 3v2H7v-2l3-3z', 'M12 15v5'],
    branch: ['M7 4v10a4 4 0 0 0 4 4h6', 'M7 4a1.5 1.5 0 1 0 0 .01', 'm14 15 3 3-3 3'], columns: ['M4 5h16v14H4z', 'M9.5 5v14M14.5 5v14'],
    lens: ['M12 5a7 7 0 1 0 0 14 7 7 0 0 0 0-14z', 'M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z'], tool: ['M14 6a4 4 0 0 0-5 5L4 16v4h4l5-5a4 4 0 0 0 5-5l-3 3-2-2z'],
    doc: ['M7 3h7l4 4v14H7z', 'M14 3v4h4', 'M10 12h5M10 16h5'], cpu: ['M8 8h8v8H8z', 'M4 10h4M4 14h4M16 10h4M16 14h4M10 4v4M14 4v4M10 16v4M14 16v4'],
    key: ['M14 10a4 4 0 1 0-3.5 4L12 15.5V18h2.5v2H17v-3l-3.2-3.2A4 4 0 0 0 14 10z'], check: 'm5 12 5 5 9-10', down: ['M12 5v14', 'm5 12 7 7 7-7'],
    gear: ['M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z', 'M19 12a7 7 0 0 0-.1-1.2l2-1.5-2-3.4-2.3.9a7 7 0 0 0-2-1.2L14.2 3h-4l-.4 2.6a7 7 0 0 0-2 1.2l-2.3-.9-2 3.4 2 1.5a7 7 0 0 0 0 2.4l-2 1.5 2 3.4 2.3-.9a7 7 0 0 0 2 1.2l.4 2.6h4l.4-2.6a7 7 0 0 0 2-1.2l2.3.9 2-3.4-2-1.5c.1-.4.1-.8.1-1.2z'],
    spark: ['M12 3v5M12 16v5M3 12h5M16 12h5', 'm6 6 3 3M15 15l3 3M18 6l-3 3M9 15l-3 3'],
    users: ['M16 19v-1a4 4 0 0 0-8 0v1', 'M12 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6'], eye: ['M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z', 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6'],
};

export async function api(path, body, method) {
    const response = await fetch(path, {
        method: method || (body === undefined ? 'GET' : 'POST'),
        headers: { 'X-Session-Token': TOKEN, ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    let value = null;
    try { value = await response.json(); } catch (e) { /* non-JSON error body */ }
    if (!response.ok) throw new Error(explain(value?.error) || t('请求失败（{0}）', response.status));
    return value;
}

// Server validation messages are English and terse; give the user a next step.
const EXPLAIN = [
    [/Every reply target must be in the audience/, t('回答者必须包含在可见范围内。请调整“谁可以看到”或回答者。')],
    [/private reference requires the same or narrower audience/, t('引用的是私聊内容，只能发给相同或更小的受众。请改用私聊范围，或先对原消息使用“公开分享”。')],
    [/Independent answers require at least two models/, t('独立回答至少需要选择两个模型。')],
    [/Private\/independent discussion requires configured fresh-call backends/, t('私聊与独立回答需要所选模型使用新版后端（非旧版 CLI 包装）。请在房间设置 → 参与者中检查。')],
    [/Selected backend unavailable|Backend is not configured|API backend disabled/, t('所选模型的后端当前不可用。请在房间设置 → 参与者中检查后端与密钥配置。')],
    [/does not support image input/, t('所选模型的当前后端不支持图片输入。请移除图片或改选支持图片的模型。')],
    [/cannot consume (R9 )?attachments/, t('所选模型的当前后端无法读取附件。请改选其他模型或后端。')],
    [/Referenced message unavailable/, t('引用的消息不可用（可能不在本聊天中）。')],
    [/already replying in this room/, t('该模型正在本聊天中回复，请等待完成或先停止。')],
    [/Select (stable model identities|reply targets|a valid message audience)/, t('回答者或可见范围无效，请重新选择。')],
    [/Overrides must name explicit reply targets/, t('“本次模型设置”只能用于本次选择的回答者。')],
    [/Restore this room before changing messages/, t('该聊天已归档，请先恢复再操作消息。')],
    [/Only human messages can be edited/, t('只有你自己的消息可以编辑。')],
    [/This version was replaced/, t('该版本已被替换，请刷新后再编辑。')],
    [/Regeneration requires a recorded fresh-call reply/, t('这条回复没有可用的调用记录，无法重新生成。请重新提问。')],
    [/no frozen audience snapshot|no attribution; send a new question/, t('这条旧回复缺少冻结快照，无法精确重新生成。请重新提问。')],
    [/Wait for or cancel the remaining independent answers/, t('同组独立回答尚未全部完成。请等待或停止其余回答后再公开。')],
    [/Summary sources have been superseded/, t('摘要引用的来源消息已被编辑或替换，请更新覆盖范围。')],
    [/History is preserved; archive this room instead/, t('历史记录只追加、不清空。如需隐藏，请归档该聊天。')],
    [/exceed(s)? .*budget|exceeds configured budget/, t('输入超出该模型的上下文容量。请缩短内容、减少附件，或在房间设置 → 上下文中调整。')],
    [/Stop and disconnect the legacy wrapper/, t('请先停止并断开旧版 CLI 包装进程，再切换后端。')],
    [/Unsupported backend|belongs to a different stable identity/, t('这个连接不能用于该参与者。')],
    [/Unknown participant|participant has been removed/, t('这位参与者已被移除或不存在。')],
    [/Connection is switched off or has no key/, t('这位参与者使用的连接已停用或还没有密钥。请在 设置 → 模型与服务 里检查。')],
    [/Effort not in explicitly configured model capabilities/, t('所选推理强度不在这个模型支持的档位里，请重新选择。')],
    [/Unsupported OpenAI reasoning mapping|OpenAI effort mapping must be explicitly configured/, t('这个连接没有开启“推理强度参数”，不能设置推理强度。可在连接的高级设置里开启（需服务商支持 reasoning_effort）。')],
    [/response did not match protocol|response schema/, t('服务商返回的内容不是所选协议的格式。请检查连接的协议与服务地址是否对应。')],
    [/API HTTP status 401|API HTTP status 403/, t('服务商拒绝了密钥（401/403）。请检查密钥是否正确、是否有该模型的权限。')],
    [/API HTTP status 404/, t('服务商返回 404：服务地址或模型 ID 不对。')],
    [/API HTTP status 429/, t('服务商返回 429：请求过于频繁或额度不足。')],
    [/API HTTP status 5\d\d/, t('服务商暂时出错（5xx），稍后再试。')],
    [/connect timeout|transport failure|connect \(DNS/, t('连不上服务地址。请检查地址、网络或代理。')],
    [/response timeout/, t('服务商在限定时间内没有答复。')],
    [/API backend disabled or key unavailable/, t('这个连接已停用，或还没有保存密钥。')],
    [/API cumulative request cap reached/, t('这个连接已达到你设置的累计请求上限。可在连接的高级设置里调高或取消上限。')],
    [/Retrieved evidence does not fit/, t('检索到的资料放不进这个模型的输入容量。可在房间设置 → 上下文调大容量，或关闭检索后重试。')],
    [/Unknown research lens/, t('所选分析框架不存在，可能已被删除。请重新选择。')],
    [/Invalid API key format/, t('Key 格式不正确：至少 8 个字符，且不能包含空白。')],
    [/Explicit provider model ID required/, t('需要填写模型 ID。')],
    [/API requires HTTPS|base URL must not contain/, t('服务地址需为 https:// 开头，且不含账号、查询参数。')],
    [/Still used by/, t('仍有聊天在使用这个连接，请先把那些聊天切换到其他连接。')],
];
export function explain(text) {
    if (!text) return '';
    for (const [pattern, message] of EXPLAIN) if (pattern.test(text)) return t('{0}（{1}）', message, text);
    return text;
}

/** A modal <dialog> sits in the browser's top layer, above every z-index. Anything that must stay visible over it
 *  (notifications, menus) is promoted to the top layer too, and re-promoted so that it is the newest entry. */
function raise(el) {
    if (!el.showPopover) return;
    el.popover = 'manual';
    try { if (el.matches(':popover-open')) el.hidePopover(); el.showPopover(); } catch (e) { /* not connected yet */ }
}

export function toast(message, kind = 'info', ms) {
    const host = document.getElementById('toasts');
    const el = h('div', { class: 'toast ' + kind, role: kind === 'error' ? 'alert' : 'status' }, h('span', null, message));
    const drop = () => { el.remove(); if (!host.children.length && host.matches?.(':popover-open')) host.hidePopover(); };
    if (kind === 'error') el.append(h('button', { class: 'toast-x', 'aria-label': t('关闭'), onclick: drop }, '×'));
    host.append(el);
    raise(host);
    setTimeout(drop, ms || (kind === 'error' ? 9000 : 4200));
}

const layer = () => document.getElementById('layer');
// While a modal dialog is open everything outside it is inert, even what is drawn above it: a menu opened from inside
// one must live in it to be clickable (still a popover in the top layer, so it is drawn above the dialog).
const menuHost = () => [...document.querySelectorAll('dialog')].filter(d => d.open && d.matches(':modal')).at(-1) || layer();

/** Popup menu anchored to an element or a point. items: {label, icon, action, danger, disabled, hint} | 'sep' | {title}. */
export function menu(anchor, items) {
    closeMenus();
    const el = h('div', { class: 'menu', role: 'menu' });
    for (const item of items.filter(Boolean)) {
        if (item === 'sep') { el.append(h('div', { class: 'menu-sep' })); continue; }
        if (item.title) { el.append(h('div', { class: 'menu-title' }, item.title)); continue; }
        if (item.node) { el.append(item.node); continue; }
        el.append(h('button', {
            class: 'menu-item' + (item.danger ? ' danger' : '') + (item.checked ? ' checked' : ''), role: 'menuitem',
            disabled: item.disabled, title: item.hint || '',
            onclick: () => { if (!item.keepOpen) closeMenus(); item.action?.(); },
        }, item.icon ? icon(ICONS[item.icon] || item.icon) : null, h('span', { class: 'menu-label' }, item.label),
            item.note ? h('span', { class: 'menu-note' }, item.note) : null));
    }
    menuHost().append(el);
    raise(el);
    place(el, anchor);
    setTimeout(() => document.addEventListener('mousedown', outside, true));
    document.addEventListener('keydown', escape, true);
    return el;
}
function outside(event) { if (!event.target.closest('.menu')) closeMenus(); }
function escape(event) { if (event.key === 'Escape') closeMenus(); }
export function closeMenus() {
    document.querySelectorAll('#layer > .menu, dialog > .menu').forEach(m => m.remove());
    document.removeEventListener('mousedown', outside, true);
    document.removeEventListener('keydown', escape, true);
}
export function place(el, anchor) {
    const rect = anchor.getBoundingClientRect ? anchor.getBoundingClientRect()
        : { left: anchor.x, right: anchor.x, top: anchor.y, bottom: anchor.y };
    const { offsetWidth: w, offsetHeight: ht } = el;
    let left = anchor.alignRight ? rect.right - w : rect.left;
    let top = rect.bottom + 6;
    if (top + ht > window.innerHeight - 8) top = Math.max(8, rect.top - ht - 6);
    left = Math.max(8, Math.min(left, window.innerWidth - w - 8));
    el.style.left = left + 'px'; el.style.top = top + 'px';
}

/** Modal dialog. Returns {el, body, close, error}. actions: [{label, primary, danger, action}] — action may throw. */
export function modal({ title, body, actions = [], wide = false, onclose }) {
    const dialog = h('dialog', { class: 'modal' + (wide ? ' wide' : '') });
    const errorEl = h('p', { class: 'form-error', role: 'alert', hidden: true });
    const close = () => { dialog.close(); dialog.remove(); onclose?.(); };
    const error = text => { errorEl.textContent = text || ''; errorEl.hidden = !text; };
    const content = h('div', { class: 'modal-body' }, body);
    const foot = h('div', { class: 'modal-foot' }, errorEl, h('span', { class: 'grow' }),
        h('button', { class: 'btn', onclick: close }, actions.length ? t('取消') : t('关闭')),
        actions.map(a => actionButton(a, close, error)));
    dialog.append(h('div', { class: 'modal-head' }, h('h2', null, title),
        h('button', { class: 'icon-btn', onclick: close, 'aria-label': t('关闭') }, icon(ICONS.close))), content, foot);
    dialog.addEventListener('cancel', event => { event.preventDefault(); close(); });
    dialog.addEventListener('mousedown', event => { if (event.target === dialog) close(); });
    layer().append(dialog);
    dialog.showModal();
    return { el: dialog, body: content, close, error };
}

export function actionButton(a, close, error) {
    const button = h('button', { class: 'btn' + (a.primary ? ' primary' : '') + (a.danger ? ' danger' : '') }, a.label);
    button.addEventListener('click', async () => {
        button.disabled = true; error?.('');
        try { const keep = await a.action(); if (keep !== false) close?.(); }
        catch (e) { error ? error(e.message) : toast(e.message, 'error'); }
        finally { button.disabled = false; }
    });
    return button;
}

export function confirmDialog(title, text, label, action, danger = false) {
    return modal({ title, body: h('p', { class: 'modal-text' }, text), actions: [{ label, primary: !danger, danger, action }] });
}

export function promptDialog(title, value, label, action, { placeholder = '', maxLength = 80, note = '' } = {}) {
    const input = h('input', { class: 'input', value, placeholder, maxLength });
    const dialog = modal({
        title, body: [input, note ? h('p', { class: 'hint' }, note) : null],
        actions: [{ label, primary: true, action: () => action(input.value.trim()) }],
    });
    input.addEventListener('keydown', event => {
        if (event.key === 'Enter' && !event.isComposing) dialog.el.querySelector('.btn.primary').click();
    });
    input.focus(); input.select();
    return dialog;
}

export function fmtTime(ts) {
    const d = new Date(ts * 1000), now = new Date();
    const hm = d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false });
    if (d.toDateString() === now.toDateString()) return hm;
    const sameYear = d.getFullYear() === now.getFullYear();
    return d.toLocaleDateString('zh-CN', sameYear ? { month: 'numeric', day: 'numeric' } : { year: 'numeric', month: 'numeric', day: 'numeric' }) + ' ' + hm;
}
export function fmtDay(ts) {
    const d = new Date(ts * 1000), now = new Date();
    const days = Math.round((new Date(now.toDateString()) - new Date(d.toDateString())) / 86400000);
    if (days === 0) return t('今天');
    if (days === 1) return t('昨天');
    return d.toLocaleDateString('zh-CN', { year: 'numeric', month: 'long', day: 'numeric' });
}
export const fmtNum = n => (typeof n === 'number' ? n.toLocaleString('en-US') : 'UNKNOWN');
export function fmtSize(size) {
    if (!Number.isFinite(size)) return '';
    if (size < 1024) return `${size} B`;
    return size < 1048576 ? `${(size / 1024).toFixed(1)} KB` : `${(size / 1048576).toFixed(1)} MB`;
}

export const store = {
    get(key, fallback = null) {
        try { const v = localStorage.getItem('mc-' + key); return v === null ? fallback : JSON.parse(v); }
        catch (e) { return fallback; }
    },
    set(key, value) {
        try { value === null ? localStorage.removeItem('mc-' + key) : localStorage.setItem('mc-' + key, JSON.stringify(value)); }
        catch (e) { /* storage unavailable: preferences simply do not persist */ }
    },
};
