// Composer: one shared input for every reading pane.
// Who answers (targets) and who may read (visibility) are separate, visible choices. Default path: public + normal
// discussion. Private / independent / retrieval / lens / per-send model settings are opt-in and return to the defaults
// after a confirmed send. The draft is kept until the server echoes the stored message.
import { h, icon, ICONS, api, menu, closeMenus, store, fmtSize, TOKEN, explain } from './util.js';
import { S, IDS, ALL, on, emit, label, color, sigil, activeRoom, roomOf, capsOf, audienceOf, audienceText, runningTasks,
    sendMessage, channelMessages, lensOf, effectiveModel, effortName } from './state.js';
import { stopTask } from './panes.js';
import { ACCEPT, inspect, summarize, openAttachment, isImage, fileGlyph } from './attachments.js';
import { t } from './i18n.js';

const drafts = new Map();
const targetSets = new Map();
const parse = new Map();   // attachment url -> {state: 'parsing'|'ready'|'error', info, error}
let els = {};

function draft(channel = S.active) {
    if (!drafts.has(channel)) {
        const keep = store.get('search-keep-' + channel);
        drafts.set(channel, { text: store.get('draft-' + channel, ''), attachments: [], uploading: 0, reply: null,
            audience: 'public', custom: [], mode: '', overrides: {}, error: '', hint: '', sending: false,
            once: null, search: keep?.mode ? { web: true, scholar: false, ...keep, query: '' } : { mode: '', web: true, scholar: false, query: '' }, keepSearch: Boolean(keep?.mode), lens: null });
    }
    return drafts.get(channel);
}
function standing(channel = S.active) {
    if (!targetSets.has(channel)) {
        // A chat without its own saved choice starts with the participants that can answer here (their connection is
        // usable), so a fresh install never pre-selects participants that have no model yet. Explicit choices are kept.
        const room = roomOf(channel), known = Object.keys(S.backends || {}).length > 0;
        const usable = id => !room || capsOf(room, id).caps?.available === true;
        const own = store.get('targets-' + channel);
        let saved = own || (known ? (store.get('targets-last') || []).filter(usable) : store.get('targets-last'));
        if (!own && known && !saved?.length) saved = IDS.filter(usable);
        if (!saved?.length) saved = IDS.slice(0, 4);
        const value = new Set(saved.filter(id => IDS.includes(id)));
        if (own || known) targetSets.set(channel, value); else return value;   // decide again once connections are known
    }
    return targetSets.get(channel);
}
/** Reply targets of this send: a one-shot set (e.g. "let Codex respond") or the room's standing choice. */
const targets = (d = draft()) => d.once || standing();
const chosenIds = (d = draft()) => IDS.filter(id => targets(d).has(id));
function saveTargets() {
    const list = IDS.filter(id => standing().has(id));
    store.set('targets-' + S.active, list); store.set('targets-last', list);
}
function scope(d = draft()) {
    if (d.audience === 'public') return 'public';
    return IDS.filter(id => (d.audience === 'targets' ? targets(d) : new Set(d.custom)).has(id));
}
const names = ids => ids.map(label).join(t('、'));
/** Going online for this send: '' (off) | independent | shared | hybrid. */
const searching = d => Boolean(d.search.mode);
const sharing = d => d.search.mode === 'shared' || d.search.mode === 'hybrid';
const MODES = { independent: [t('独立检索'), t('各查各的')], shared: [t('共享检索'), t('同一份资料')], hybrid: [t('混合检索'), t('共同基准 + 各自扩展')] };
const SEARCH_NAMES = { claude: t('Claude Code 官方搜索'), codex: t('Codex 官方搜索'), deepseek: t('DeepSeek 官方搜索'), google: t('Google 搜索'), web_search_tool: t('服务商官方搜索') };
/** How one participant would search by itself in this chat: its connection's official search, else Chattera's. */
function searchPath(room, id) {
    const now = effectiveModel(room, id), kind = now.profile?.search_kind;
    if (kind) return SEARCH_NAMES[kind] || t('官方搜索');
    return capsOf(room, id).caps?.tools ? t('Chattera 检索工具') : t('Chattera 代为检索');
}

// ---------- validation mirrors the server so problems show before sending; the server stays authoritative ----------
function problems() {
    const d = draft(), room = activeRoom(), list = [], chosen = chosenIds(d), visible = scope(d);
    if (!room) return [t('请先新建或选择一个聊天。')];
    if (visible !== 'public' && !visible.length) list.push(t('私聊需要至少选择一个可以看到消息的模型。'));
    if (visible !== 'public') {
        const outside = chosen.filter(id => !visible.includes(id));
        if (outside.length) list.push(t('{0} 被选为回答者，但不在可见范围内。请把它加入可见范围或取消选择。', names(outside)));
    }
    const parent = d.reply ? S.msgs.get(d.reply.id) : null, parentScope = parent ? audienceOf(parent) : 'public';
    if (parentScope !== 'public' && (visible === 'public' || visible.some(id => !parentScope.includes(id)))) {
        list.push(t('引用的是私聊内容（{0}），只能发给相同或更小的范围。请调整可见范围，或先对原消息使用“公开分享”。', audienceText(parentScope)));
    }
    if (d.mode && chosen.length < 2) list.push(t('独立回答需要至少选择两个回答者。'));
    if (d.reply?.action && !chosen.length) list.push(t('请模型回应需要至少选择一个回答者。'));
    if (d.reply?.action && d.text.trim().length > 1000) list.push(t('这类快捷提问的指令最多 1000 字。'));
    if (d.search.mode === 'independent' && visible !== 'public') list.push(t('私聊中不能让模型自行联网检索：私聊内容不会发给搜索服务。可以改用共享检索并自己填写检索词。'));
    if (sharing(d) && visible !== 'public' && !d.search.query.trim()) list.push(t('私聊中使用检索时，请填写检索词：系统不会自动把私聊内容发给搜索服务。'));
    const failed = d.attachments.filter(a => parse.get(a.url)?.state === 'error');
    if (failed.length) list.push(t('“{0}”无法解析，模型读不到它的内容。请移除后再发送。', failed[0].name));
    const files = [...d.attachments, ...(parent?.attachments || [])];
    const involved = visible === 'public' ? chosen : [...new Set([...chosen, ...visible])];
    for (const id of involved) {
        const { backend, caps } = capsOf(room, id);
        if (!caps) continue;
        if (chosen.includes(id) && caps.unset) list.push(t('{0} 还没有设置模型：在 设置 → 模型与服务 里为它选择连接，或这次不选它。', label(id)));
        else if (chosen.includes(id) && !caps.available) list.push(t('{0} 的连接（{1}）当前不可用，请在房间 → 模型里检查。', label(id), backend));
        if ((visible !== 'public' && !caps.private) || (d.mode && !caps.independent)) list.push(t('{0} 的连接（{1}）不支持私聊/独立回答。', label(id), backend));
        if (!chosen.includes(id) || !files.length) continue;
        if (!caps.documents) list.push(t('{0} 的连接（{1}）无法读取附件。请取消选择它或移除附件。', label(id), backend));
        else if (!caps.images && files.some(isImage)) list.push(t('{0} 的连接（{1}）不支持图片，它看不到图片。请取消选择它或移除图片。', label(id), backend));
    }
    if (d.attachments.filter(isImage).length > 12) list.push(t('一条消息最多 12 张图片。'));
    return list;
}

// ---------- sending ----------
async function send() {
    const d = draft(), channel = S.active, text = d.text.trim();
    if (d.sending || d.uploading || (!text && !d.attachments.length)) return;
    if (d.attachments.some(a => parse.get(a.url)?.state === 'parsing')) { d.hint = t('附件还在解析，完成后即可发送。'); sync(); return; }
    const issues = problems();
    if (issues.length) { d.error = issues[0]; sync(); return; }
    const chosen = chosenIds(d);
    const overrides = {};
    for (const id of chosen) {
        const o = d.overrides[id] || {}, value = {};
        if (o.model) value.model = o.model;
        if (o.effort) value.effort = o.effort;
        if (Object.keys(value).length) overrides[id] = value;
    }
    const payload = { type: 'message', text, sender: S.username, channel, targets: chosen,
        attachments: d.attachments.map(({ name, url, size, media_type, kind }) => ({ name, url, size, media_type, kind })),
        visibility: scope(d), independent_mode: d.mode, model_overrides: overrides };
    if (d.reply) payload.reply_to = d.reply.id;
    if (d.reply?.action) payload.action_instruction = text;
    if (d.reply?.quote) payload.quote_text = d.reply.quote;
    if (d.search.mode === 'independent') payload.evidence = { mode: 'independent' };
    else if (searching(d)) payload.evidence = { mode: d.search.mode, web: d.search.web || !d.search.scholar, scholar: d.search.scholar, ...(d.search.query.trim() ? { query: d.search.query.trim() } : {}) };
    if (d.lens) payload.lens = { '*': d.lens };
    const first = !channelMessages(channel).some(m => m.sender === S.username && (m.type || 'chat') === 'chat');
    d.sending = true; d.error = ''; d.hint = ''; sync();
    try {
        await sendMessage(payload, d.attachments.length ? 120000 : 30000);
        // Confirmed stored: only now clear the draft and return per-send options to the defaults.
        Object.assign(d, { text: '', attachments: [], reply: null, audience: 'public', custom: [], mode: '', overrides: {}, once: null, lens: null,
            search: d.keepSearch ? { ...d.search, query: '' } : { mode: '', web: true, scholar: false, query: '' } });
        store.set('draft-' + channel, null);
        els.box.classList.remove('sent'); void els.box.offsetWidth; els.box.classList.add('sent');
        if (first) autoTitle(channel, text);
    } catch (e) {
        d.error = e.message;  // draft, audience and attachments are intentionally kept
    } finally {
        d.sending = false;
        if (channel === S.active) { sync(true); els.input.focus(); }
    }
}

// A fresh "新聊天" takes its name from the first question. Display name only; ID/channel never change.
async function autoTitle(channel, text) {
    const room = roomOf(channel);
    if (!room || !/^(新聊天|New chat)( \d+)?$/.test(room.name)) return;
    const title = text.replace(/\s+/g, ' ').trim().slice(0, 28);
    if (!title) return;
    try { await api('/api/chat/rooms/' + room.id, { name: title }); } catch (e) { /* keep the default name */ }
}

// ---------- attachments ----------
async function addFiles(files) {
    const d = draft(), channel = S.active, room = activeRoom();
    if (!room) return;
    for (const file of files) {
        if (d.attachments.length + d.uploading >= 12) { d.error = t('每条消息最多 12 个附件。'); break; }
        if (file.size > 50 * 1024 * 1024) { d.error = t('{0} 超过 50 MB 上限。', file.name); continue; }
        if (/\.doc$/i.test(file.name)) { d.error = t('{0}：旧版 .doc 无法可靠解析，请在 Word 中另存为 DOCX 或 PDF 后再上传。', file.name); continue; }
        d.uploading++; sync();
        try {
            const form = new FormData();
            form.append('file', file); form.append('display_name', file.name);
            const response = await fetch('/api/upload', { method: 'POST', headers: { 'X-Session-Token': TOKEN }, body: form });
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || t('上传失败'));
            d.attachments.push(data);
            parse.set(data.url, { state: 'parsing' });
            inspect(data, room).then(info => parse.set(data.url, { state: 'ready', info }))
                .catch(error => parse.set(data.url, { state: 'error', error: explain(error.message) }))
                .finally(() => { if (channel === S.active) sync(); });
        } catch (e) { d.error = t('{0}：{1}', file.name, /Unsupported file type/.test(e.message) ? t('不支持这种文件。可上传图片（PNG/JPEG/WebP）和文档（PDF、DOCX、TXT、MD、RTF、HTML、CSV、JSON、XML、TeX）。') : explain(e.message)); }
        finally { d.uploading--; if (channel === S.active) sync(); }
    }
}

function attachmentChip(att, index, d) {
    const status = parse.get(att.url);
    const line = !status ? fmtSize(att.size) : status.state === 'parsing' ? t('正在解析…（扫描件会做 OCR，可能需要一会儿）')
        : status.state === 'error' ? t('解析失败：{0}', status.error) : summarize(status.info);
    return h('div', { class: 'c-att ' + (status?.state || '') },
        h('button', { class: 'c-att-open', title: t('预览'), onclick: () => openAttachment(att, { siblings: d.attachments }) },
            isImage(att) ? h('img', { src: att.url, alt: '' }) : fileGlyph(att),
            h('span', { class: 'c-att-text' }, h('span', { class: 'att-name' }, att.name), h('span', { class: 'att-status' }, line))),
        h('button', { class: 'icon-btn tiny', title: t('移除附件'), 'aria-label': t('移除附件'), onclick: () => { d.attachments.splice(index, 1); d.error = ''; sync(); } }, icon(ICONS.close, 12)));
}

/** How each chosen model will actually receive the pending attachments. */
function delivery(d, room, chosen) {
    if (!d.attachments.length || !chosen.length) return null;
    const images = d.attachments.filter(isImage), docs = d.attachments.filter(a => !isImage(a));
    const parts = [];
    if (images.length) {
        const see = chosen.filter(id => capsOf(room, id).caps?.images);
        if (see.length) parts.push(t('{0} 以原生图片读取', names(see)));
    }
    if (docs.length) {
        const ocr = docs.some(a => parse.get(a.url)?.info?.ocr);
        parts.push(t('文档以提取的文字提供{0}，模型看不到其中的图表', ocr ? t('（部分页面为 OCR，可能有误）') : ''));
    }
    return parts.length ? h('div', { class: 'c-note' }, parts.join(t('；')) + t('。')) : null;
}

// ---------- popovers ----------
function audienceMenu(anchor) {
    const d = draft();
    const reopen = () => audienceMenu(anchor);
    const custom = new Set(d.audience === 'custom' ? d.custom : chosenIds(d));
    menu(anchor, [
        { title: t('谁可以看到这条消息') },
        { label: t('公开'), note: t('所有模型之后都可读取（默认）'), checked: d.audience === 'public', action: () => { d.audience = 'public'; d.error = ''; sync(); } },
        { label: t('仅回答者可见'), note: t('私聊：只有本次选中的回答者可读'), checked: d.audience === 'targets', action: () => { d.audience = 'targets'; d.error = ''; sync(); } },
        'sep', { title: t('或指定可见的模型') },
        ...IDS.map(id => ({
            label: label(id), checked: d.audience === 'custom' && custom.has(id), keepOpen: true,
            action: () => {
                custom.has(id) ? custom.delete(id) : custom.add(id);
                d.audience = 'custom'; d.custom = IDS.filter(x => custom.has(x)); d.error = ''; sync(); reopen();
            },
        })),
        'sep', { node: h('p', { class: 'menu-help' }, t('回答者 ≠ 可见范围：只让 Claude 回答一条公开消息，其他模型之后仍可读到它。私聊发送成功后，下一条自动恢复公开。')) },
    ]);
}

function modeMenu(anchor) {
    const d = draft();
    const set = value => () => { d.mode = value; d.error = ''; sync(); };
    menu(anchor, [
        { title: t('回答方式（仅本次发送）') },
        { label: t('普通讨论'), note: t('回答公开，后续可互相讨论（默认）'), checked: d.mode === '', action: set('') },
        { label: t('独立回答'), note: t('各模型互不可见；由你确认后再公开'), checked: d.mode === 'normal', action: set('normal') },
        'sep', { title: t('高级') },
        { label: t('严格独立'), note: t('同上，且不带入任何既往私聊'), checked: d.mode === 'strict', action: set('strict') },
        'sep', { node: h('p', { class: 'menu-help' }, t('同一条消息点名多个模型时，它们总是并发回答、互相看不到本轮答案；要让模型互评，在回答上点“请回应”即可。')) },
    ]);
}

function webMenu(anchor) {
    const d = draft(), room = activeRoom(), reopen = () => webMenu(anchor);
    const keep = () => store.set('search-keep-' + S.active, d.keepSearch && d.search.mode ? { mode: d.search.mode, web: d.search.web, scholar: d.search.scholar } : null);
    const pick = mode => () => { d.search.mode = mode; if (sharing(d) && !d.search.web && !d.search.scholar) d.search.web = true; d.error = ''; keep(); sync(); reopen(); };
    const toggle = key => () => { d.search[key] = !d.search[key]; if (!d.search.web && !d.search.scholar) d.search[key === 'web' ? 'scholar' : 'web'] = true; keep(); sync(); reopen(); };
    const provider = S.models?.search?.web_provider || 'duckduckgo', chosen = chosenIds(d);
    const routes = h('div', { class: 'route-list' }, chosen.length ? chosen.map(id => h('div', { class: 'route', style: { '--hue': color(id) } }, sigil(id, 'xs'), h('span', null, label(id)), h('em', null, searchPath(room, id))))
        : h('p', { class: 'menu-help' }, t('选择回答者后，这里会列出每位各自使用的搜索方式。')));
    menu(anchor, [
        { title: t('联网方式') },
        { label: t('不联网'), checked: !d.search.mode, keepOpen: true, action: pick('') },
        ...Object.entries(MODES).map(([mode, [name, note]]) => ({ label: name, note, checked: d.search.mode === mode, keepOpen: true, action: pick(mode),
            hint: { independent: t('每个模型用自己连接的官方搜索（没有时用 Chattera 检索），自己决定搜什么、读什么。不会事先给大家同一批结果。'),
                shared: t('系统检索一次，把同一份资料交给本轮所有回答者。适合对同一批材料做受控比较。'), hybrid: t('先给所有回答者一份共同的基准资料，再让每个模型自行搜索和扩展。') }[mode] })),
        ...(sharing(d) ? ['sep', { title: t('共同资料来自') },
            { label: t('网页搜索'), icon: 'globe', note: { duckduckgo: 'DuckDuckGo', tavily: 'Tavily', brave: 'Brave' }[provider], checked: d.search.web, keepOpen: true, action: toggle('web') },
            { label: t('学术文献'), icon: 'book', note: 'OpenAlex · Europe PMC · arXiv', checked: d.search.scholar, keepOpen: true, action: toggle('scholar') }] : []),
        ...(d.search.mode && d.search.mode !== 'shared' ? ['sep', { title: t('各自的搜索方式') }, { node: routes }] : []),
        'sep',
        { label: t('之后的提问也保持'), checked: d.keepSearch, keepOpen: true, action: () => { d.keepSearch = !d.keepSearch; keep(); reopen(); } },
        { node: h('p', { class: 'menu-help' }, t('每个回答下方都能看到它实际搜了什么、读了哪些网页。不同模型找到不同来源是正常的；私聊内容不会发给搜索服务。')) },
    ]);
}

function lensMenu(anchor) {
    const d = draft(), room = activeRoom(), lenses = S.models?.lenses || [];
    const shared = lensOf(room?.lens || 'open');
    const per = Object.entries(room?.lens_by_model || {}).map(([id, lens]) => t('{0}：{1}', label(id), lensOf(lens)?.name || lens));
    menu(anchor, [
        { title: t('研究视角（仅本次发送，应用于所有回答者）') },
        { label: t('沿用房间设置'), note: (shared?.name || t('开放科学讨论')) + (per.length ? t(' · 另有按模型的设置') : ''), checked: !d.lens, action: () => { d.lens = null; sync(); } },
        'sep',
        ...lenses.map(lens => ({ label: lens.name, hint: lens.summary || '', checked: d.lens === lens.id, action: () => { d.lens = lens.id; sync(); } })),
        'sep', { node: h('p', { class: 'menu-help' }, t('视角是分析方法，不是结论，也不要求固定的回答格式。要让不同模型用不同视角、或查看实际生效的内容，请打开房间 → 讨论。')) },
    ]);
}

/** Participants shown as chips. With many, only the pinned ones and whoever is currently chosen; the rest are one click away. */
const CHIP_LIMIT = 6;
const pinnedIds = () => new Set((store.get('who-pins') || IDS.slice(0, 4)).filter(id => IDS.includes(id)));
function chipIds(chosen) {
    if (IDS.length <= CHIP_LIMIT) return IDS;
    const pins = pinnedIds();
    return IDS.filter(id => pins.has(id) || chosen.has(id));
}

function whoMenu(anchor) {
    const d = draft(), room = activeRoom();
    const search = h('input', { class: 'input small', type: 'search', placeholder: t('搜索参与者或模型'), 'aria-label': t('搜索参与者') });
    const list = h('div', { class: 'who-list', role: 'group', 'aria-label': t('回答者') });
    const set = () => targets(d);
    const changed = () => { if (!d.once) saveTargets(); d.error = ''; sync(); draw(); };
    const draw = () => {
        const q = search.value.trim().toLowerCase(), pins = pinnedIds(), chosen = set();
        const rows = IDS.map(id => ({ id, now: effectiveModel(room, id), caps: capsOf(room, id).caps }))
            .filter(r => !q || `${label(r.id)} ${r.now.modelLabel || ''} ${r.now.profile?.label || ''}`.toLowerCase().includes(q));
        list.replaceChildren(...rows.map(({ id, now, caps }) => {
            const active = chosen.has(id), off = caps && !caps.available;
            return h('div', { class: 'who-item' + (active ? ' on' : '') + (off ? ' off' : ''), style: { '--hue': color(id) } },
                h('button', { class: 'who-main', role: 'checkbox', 'aria-checked': String(active), onclick: () => { active ? chosen.delete(id) : chosen.add(id); changed(); } },
                    h('span', { class: 'who-check' }, active ? icon(ICONS.check, 13) : null), sigil(id, 'xs'),
                    h('span', { class: 'who-text' }, h('strong', null, label(id)),
                        h('small', null, [now.modelLabel, now.effort ? effortName(now.effort) : null, off ? t('连接不可用') : null].filter(Boolean).join(' · ') || t('未设置模型')))),
                IDS.length > CHIP_LIMIT ? h('button', { class: 'who-pin' + (pins.has(id) ? ' on' : ''), title: pins.has(id) ? t('不再固定在输入区') : t('固定在输入区，方便随时点选'), 'aria-pressed': String(pins.has(id)),
                    onclick: () => { pins.has(id) ? pins.delete(id) : pins.add(id); store.set('who-pins', [...pins]); sync(); draw(); } }, icon(ICONS.pin, 14)) : null);
        }), ...(rows.length ? [] : [h('p', { class: 'menu-help' }, t('没有匹配的参与者。'))]));
    };
    search.addEventListener('input', draw);
    search.addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); list.querySelector('.who-main')?.click(); } });
    draw();   // before placing the menu, so its real height is known
    menu(anchor, [{ title: d.once ? t('本次由谁回答') : t('谁来回答（可多选）') }, { node: h('div', { class: 'who-menu' },
        IDS.length > 5 ? search : null, list,
        h('div', { class: 'who-foot' },
            h('button', { class: 'tiny-btn', onclick: () => { const chosen = set(); IDS.forEach(id => { if (capsOf(room, id).caps?.available !== false) chosen.add(id); }); changed(); } }, t('全选')),
            h('button', { class: 'tiny-btn', onclick: () => { set().clear(); changed(); } }, t('清空')),
            h('span', { class: 'grow' }),
            h('button', { class: 'tiny-btn', onclick: () => { closeMenus(); emit('open-settings', 'models'); } }, t('添加或管理模型…')))) }]);
    if (IDS.length > 5) search.focus();
}

function overridesMenu(anchor) {
    const d = draft(), room = activeRoom(), chosen = chosenIds(d);
    const panel = h('div', { class: 'override-panel' }, h('p', { class: 'menu-help', style: { margin: '4px 0 8px' } }, t('只影响本次发送，发送后恢复。选“沿用”即使用房间里的设置。')));
    if (!chosen.length) panel.append(h('p', { class: 'menu-help' }, t('请先选择回答者。')));
    for (const id of chosen) {
        const value = d.overrides[id] ||= {};
        const now = effectiveModel(room, id), catalog = S.models?.catalog?.[now.backend] || [];
        const current = catalog.find(m => m.id === (value.model || now.model));
        const adjustable = !current?.no_effort && !(now.profile?.kind === 'api' && now.profile.protocol === 'openai-chat' && now.profile.reasoning_parameter !== 'reasoning_effort');
        const levels = adjustable ? current?.effort_values || (now.profile?.kind === 'cli' ? now.profile.effort_values : null) || [] : [];
        const model = h('select', { class: 'input small', 'aria-label': t('{0} 的模型', label(id)), onchange: event => {
                value.model = event.target.value; delete value.effort;
                // A different model may not accept the effort in force: choose one it declares.
                const next = catalog.find(m => m.id === value.model), allowed = next?.effort_values || [];
                if (value.model && allowed.length && !allowed.includes(now.effort)) value.effort = allowed.includes(next.default_effort) ? next.default_effort : allowed.includes('medium') ? 'medium' : allowed[allowed.length >> 1];
                sync(); overridesMenu(anchor); } },
            h('option', { value: '' }, t('沿用 · {0}', now.modelLabel || t('默认'))), catalog.filter(m => m.id !== now.model).map(m => h('option', { value: m.id, selected: value.model === m.id }, m.label || m.id)));
        const effort = h('select', { class: 'input small', 'aria-label': t('{0} 的推理强度', label(id)), disabled: !levels.length, onchange: event => { value.effort = event.target.value; sync(); } },
            h('option', { value: '' }, levels.length ? t('沿用 · {0}', value.model ? t('默认') : effortName(now.effort) || t('默认')) : t('不可调')), levels.map(l => h('option', { value: l, selected: value.effort === l }, effortName(l))));
        panel.append(h('div', { class: 'override-row' }, h('div', { class: 'override-who' }, sigil(id, 'xs'), label(id)), model, effort));
    }
    panel.append(h('div', { class: 'card-actions' }, h('button', { class: 'btn small', onclick: () => { d.overrides = {}; closeMenus(); sync(); } }, t('恢复沿用')),
        h('button', { class: 'btn small', onclick: () => { closeMenus(); emit('panel', { tab: 'models' }); } }, t('房间里的模型设置…'))));
    menu(anchor, [{ title: t('本次使用的模型与推理强度') }, { node: panel }]);
}

// ---------- rendering ----------
function sync(resetInput = false) {
    const d = draft(), room = activeRoom(), visible = scope(d), chosen = targets(d);
    if (resetInput || els.input.dataset.channel !== String(S.active)) {
        els.input.value = d.text; els.input.dataset.channel = String(S.active); resize();
    }
    els.box.classList.toggle('private', visible !== 'public');
    els.box.classList.toggle('disabled', !room);
    els.input.disabled = !room;
    const ids = chosenIds(d);
    // The dock's edge carries the hues of whoever will answer.
    els.box.style.setProperty('--ring', ids.length ? `linear-gradient(90deg, ${ids.map(color).join(', ')}${ids.length === 1 ? ', ' + color(ids[0]) : ''})` : 'linear-gradient(90deg, var(--line-2), var(--line-2))');
    els.box.classList.toggle('live', Boolean(d.text.trim()) && ids.length > 0);

    const ctx = [];
    if (d.reply) {
        const parent = S.msgs.get(d.reply.id);
        const who = ALL.includes(d.reply.sender) ? label(d.reply.sender) : d.reply.sender;
        ctx.push(h('div', { class: 'c-reply' }, icon(ICONS.reply, 14),
            h('span', { class: 'c-reply-who' }, `${d.reply.quote ? t('引用选段') : d.reply.action ? t('回应') : t('引用')} ${who} #${d.reply.id}`),
            parent && audienceOf(parent) !== 'public' ? h('span', { class: 'badge lock' }, icon(ICONS.lock, 11), t('私聊内容')) : null,
            h('span', { class: 'c-reply-text' }, d.reply.text.length > 140 ? d.reply.text.slice(0, 140) + '…' : d.reply.text),
            h('button', { class: 'icon-btn tiny', title: t('取消引用'), onclick: () => { d.reply = null; d.once = null; sync(); } }, icon(ICONS.close, 13))));
    }
    if (d.attachments.length || d.uploading) {
        ctx.push(h('div', { class: 'c-atts' }, d.attachments.map((att, i) => attachmentChip(att, i, d)),
            d.uploading ? h('div', { class: 'c-att parsing' }, h('span', { class: 'c-att-text' }, h('span', { class: 'att-status' }, t('正在上传 {0} 个文件…', d.uploading)))) : null));
        const note = delivery(d, room, ids);
        if (note) ctx.push(note);
    }
    if (d.search.mode === 'independent') {
        ctx.push(h('div', { class: 'c-reply c-tool c-web' }, icon(ICONS.globe, 14), h('span', { class: 'c-reply-who', style: { color: 'inherit' } }, t('独立检索')),
            h('span', { class: 'c-routes' }, ids.length ? ids.map(id => h('span', { class: 'c-route', style: { '--hue': color(id) }, title: t('{0} 将使用：{1}', label(id), searchPath(room, id)) }, sigil(id, 'xs'), searchPath(room, id)))
                : t('各模型用自己的搜索能力分别查证')),
            h('button', { class: 'icon-btn tiny', title: t('这次不联网'), onclick: () => { d.search.mode = ''; sync(); } }, icon(ICONS.close, 13))));
    } else if (searching(d)) {
        const what = [d.search.web ? t('网页') : null, d.search.scholar ? t('学术文献') : null].filter(Boolean).join(' + ');
        const query = h('input', { placeholder: visible === 'public' ? t('共同资料的检索词（留空则根据问题自动拟定）') : t('检索词（私聊必填）'), value: d.search.query, maxLength: 300, 'aria-label': t('检索词'),
            oninput: event => { d.search.query = event.target.value; d.error = ''; } });
        ctx.push(h('div', { class: 'c-reply c-tool c-web' }, icon(ICONS.search, 14), h('span', { class: 'c-reply-who', style: { color: 'inherit' } }, d.search.mode === 'hybrid' ? t('混合：先共享{0}，再各自扩展', what) : t('共享检索{0}', what)), query,
            h('button', { class: 'icon-btn tiny', title: t('这次不联网'), onclick: () => { d.search.mode = ''; sync(); } }, icon(ICONS.close, 13))));
    }
    if (visible !== 'public') {
        ctx.push(h('div', { class: 'c-private' }, icon(ICONS.lock, 13),
            visible.length ? t('私聊：{0}。其他模型读不到这条消息及其回答；发送成功后恢复公开。', audienceText(visible)) : t('私聊：尚未选择可见的模型。')));
    }
    if (d.mode) ctx.push(h('div', { class: 'c-private indep' }, d.mode === 'strict' ? t('严格独立回答：各模型互不可见，且不带入既往私聊；答案由你确认后才公开。') : t('独立回答：各模型互不可见；答案由你确认后才公开。')));
    const issues = room ? problems() : [];
    const filled = d.text.trim() || d.attachments.length;
    const text = d.error || (filled ? issues[0] : '') || '';
    if (text) ctx.push(h('div', { class: 'c-error', role: 'alert' }, h('span', null, text), d.error ? h('button', { class: 'icon-btn tiny', title: t('关闭'), onclick: () => { d.error = ''; sync(); } }, icon(ICONS.close, 12)) : null));
    else if (d.hint) ctx.push(h('div', { class: 'c-note' }, d.hint));
    else if (room && !chosen.size && filled) ctx.push(h('div', { class: 'c-note' }, sharing(d) ? t('未选择回答者：只执行检索并把结果记录到聊天中。') : t('未选择回答者：消息只会保存到聊天中，不会有模型回复。')));
    // Keep the query field's focus and caret while the context row is rebuilt.
    const typing = document.activeElement?.closest?.('.c-tool') ? document.activeElement.selectionStart : null;
    els.context.replaceChildren(...ctx);
    els.context.hidden = !ctx.length;
    if (typing !== null) { const field = els.context.querySelector('.c-tool input'); if (field) { field.focus(); field.setSelectionRange(typing, typing); } }

    const shown = chipIds(chosen), hiddenChosen = IDS.length - shown.length;
    els.targets.replaceChildren(...shown.map(id => {
        const { caps } = capsOf(room, id), active = chosen.has(id), off = caps && !caps.available, now = effectiveModel(room, id);
        return h('button', {
            class: 'who-chip' + (active ? ' active' : '') + (off ? ' off' : ''), 'aria-pressed': String(active), style: { '--hue': color(id) },
            title: t('{0} {1} 作为回答者\n模型 {2}{3} · {4}{5}', active ? t('取消选择') : t('选择'), label(id), d.overrides[id]?.model || now.modelLabel || t('默认'), (d.overrides[id]?.effort || now.effort) ? t(' · 推理强度 ') + effortName(d.overrides[id]?.effort || now.effort) : '', now.profile?.label || now.backend, off ? t('（不可用）') : '')
                + (caps ? t('\n图片 {0}  文档 {1}  自行检索与读取 {2}', caps.images ? '✓' : '✗', caps.documents ? '✓' : '✗', caps.tools ? '✓' : '✗') : ''),
            onclick: () => { active ? chosen.delete(id) : chosen.add(id); if (!d.once) saveTargets(); d.error = ''; sync(); },
        }, sigil(id, 'xs'), h('span', null, label(id)));
    }), h('button', { class: 'who-more', title: t('选择回答者；搜索、固定常用的参与者，或添加新的模型'), 'aria-label': t('选择回答者'), 'aria-haspopup': 'menu',
        onclick: event => whoMenu(event.currentTarget) }, hiddenChosen > 0 ? t('共 {0} 位', IDS.length) : null, icon(ICONS.chevron, 13)), ...(d.once ? [h('button', { class: 'once-note', title: t('恢复为这个聊天平时的回答者'), onclick: () => { d.once = null; sync(); } }, t('仅本次 · 恢复'))] : []));

    const overrideCount = ids.filter(id => Object.values(d.overrides[id] || {}).some(Boolean)).length;
    els.search.classList.toggle('on', searching(d));
    els.search.replaceChildren(icon(ICONS.globe, 14), searching(d) ? MODES[d.search.mode][0] : t('联网'));
    els.search.dataset.mode = d.search.mode;
    const lens = d.lens ? lensOf(d.lens) : null, roomLens = room?.lens ? lensOf(room.lens) : null;
    els.lens.classList.toggle('on', Boolean(d.lens));
    els.lens.replaceChildren(icon(ICONS.lens, 14), lens ? lens.name : roomLens ? roomLens.name : t('视角'));
    els.lens.title = t('研究视角：') + (lens ? t('本次使用“{0}”', lens.name) : t('沿用房间设置（{0}）', roomLens?.name || t('开放科学讨论')));
    els.audience.replaceChildren(icon(visible === 'public' ? ICONS.eye : ICONS.lock, 14), visible === 'public' ? t('公开') : (visible.length ? t('仅 ') + names(visible) : t('私聊')));
    els.audience.classList.toggle('on', visible !== 'public');
    els.mode.replaceChildren(d.mode === 'strict' ? t('严格独立') : d.mode === 'normal' ? t('独立回答') : t('讨论'));
    els.mode.classList.toggle('on', Boolean(d.mode));
    els.overrides.classList.toggle('on', overrideCount > 0);
    els.overrides.title = overrideCount ? t('本次模型设置（{0} 个模型已调整）', overrideCount) : t('本次使用的模型与推理强度');

    const running = room ? runningTasks(S.active) : [];
    els.stop.hidden = !running.length;
    els.stop.title = t('停止本聊天中正在进行的 {0} 个回复', running.length);
    const parsing = d.attachments.some(a => parse.get(a.url)?.state === 'parsing');
    els.send.disabled = !room || !filled || d.sending || d.uploading > 0 || parsing || !S.connected;
    els.send.classList.toggle('busy', d.sending);
    els.send.title = !S.connected ? t('正在重新连接…') : d.sending ? t('发送中…') : parsing ? t('附件解析完成后可发送') : chosen.size ? t('发送（Enter）') : t('仅保存，不唤醒模型（Enter）');
    els.input.placeholder = ids.length ? t('向 {0} 提问…', names(ids)) : t('写点什么…（未选择回答者时只保存）');
}

function resize() {
    els.input.style.height = 'auto';
    els.input.style.height = Math.min(els.input.scrollHeight, window.innerHeight * 0.42) + 'px';
}

export function focusComposer() { els.input?.focus(); }

export function initComposer() {
    const root = document.getElementById('composer');
    els.context = h('div', { class: 'c-context', hidden: true });
    els.input = h('textarea', { id: 'input', rows: 1, 'aria-label': t('消息输入') });
    els.targets = h('div', { class: 'c-targets', role: 'group', 'aria-label': t('回答者') });
    els.file = h('input', { type: 'file', multiple: true, hidden: true, accept: ACCEPT,
        onchange: event => { addFiles([...event.target.files]); event.target.value = ''; } });
    els.attach = h('button', { class: 'icon-btn', title: t('添加图片或文档（也可粘贴、拖入）\n图片：PNG / JPEG / WebP\n文档：PDF、DOCX、TXT、MD、RTF、HTML、CSV、JSON、XML、TeX'), 'aria-label': t('添加附件'), onclick: () => els.file.click() }, icon(ICONS.clip, 18));
    els.search = h('button', { class: 'pill', title: t('联网：让模型各自搜索、共享同一份检索资料，或两者结合'), onclick: event => { const d = draft(); if (!d.search.mode) { d.search.mode = 'independent'; d.error = ''; sync(); } webMenu(event.currentTarget); } });
    els.lens = h('button', { class: 'pill', onclick: event => lensMenu(event.currentTarget) });
    els.audience = h('button', { class: 'pill', title: t('谁可以看到这条消息'), onclick: event => audienceMenu(event.currentTarget) });
    els.mode = h('button', { class: 'pill', title: t('回答方式：普通讨论 / 独立回答'), onclick: event => modeMenu(event.currentTarget) });
    els.overrides = h('button', { class: 'icon-btn', 'aria-label': t('本次模型设置'), onclick: event => overridesMenu(event.currentTarget) }, icon(ICONS.cpu, 17));
    els.stop = h('button', { class: 'btn stop', hidden: true, onclick: () => stopTask(activeRoom()) }, icon(ICONS.stop, 12), t('停止'));
    els.send = h('button', { class: 'send', 'aria-label': t('发送'), onclick: send }, icon(ICONS.send, 18));
    els.box = h('div', { class: 'c-box' }, els.context, els.input,
        h('div', { class: 'c-bar' }, els.attach, els.file, els.targets,
            h('div', { class: 'c-right' }, els.search, els.lens, els.audience, els.mode, els.overrides, els.stop, els.send)));
    root.append(els.box, h('div', { class: 'c-foot' }, t('Enter 发送 · Shift+Enter 换行 · 选中消息里的文字可以引用')));

    els.input.addEventListener('input', () => {
        const d = draft(); d.text = els.input.value; d.error = ''; d.hint = '';
        store.set('draft-' + S.active, d.text || null);
        resize(); sync();
    });
    els.input.addEventListener('keydown', event => {
        if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); send(); }
        if (event.key === 'Escape' && draft().reply) { draft().reply = null; draft().once = null; sync(); }
    });
    els.context.addEventListener('keydown', event => { if (event.key === 'Enter' && event.target.matches('.c-tool input') && !event.isComposing) { event.preventDefault(); send(); } });
    els.input.addEventListener('paste', event => {
        const files = [...(event.clipboardData?.files || [])];
        if (files.length) { event.preventDefault(); addFiles(files); }
    });
    const zone = document.getElementById('dropzone');
    let depth = 0;
    window.addEventListener('dragenter', event => { if (event.dataTransfer?.types.includes('Files')) { depth++; zone.hidden = false; } });
    window.addEventListener('dragleave', () => { if (--depth <= 0) { depth = 0; zone.hidden = true; } });
    window.addEventListener('dragover', event => { if (event.dataTransfer?.types.includes('Files')) event.preventDefault(); });
    window.addEventListener('drop', event => {
        depth = 0; zone.hidden = true;
        if (!event.dataTransfer?.files.length) return;
        event.preventDefault();
        addFiles([...event.dataTransfer.files]);
    });

    on('attach-files', files => { addFiles(files); els.input.focus(); });
    on('compose', data => {
        if (!activeRoom()) return;
        const d = draft();
        if (data.text !== undefined) {
            if (d.text.trim() && d.text !== data.text && !window.confirm(t('替换当前未发送的草稿？'))) return;
            d.text = data.text;
        }
        if (data.append) d.text = (d.text.trim() ? d.text.replace(/\s*$/, '\n') : '') + data.append;
        store.set('draft-' + S.active, d.text || null);
        d.reply = data.reply || d.reply; d.error = ''; d.hint = data.hint || '';
        // "Let X respond": answer once with X, without changing who normally answers in this chat.
        if (data.once) d.once = new Set(data.once.filter(id => IDS.includes(id)));
        // Quoting private content: narrow to its audience up front (never widen); the user can still change it.
        const parent = d.reply ? S.msgs.get(d.reply.id) : null, parentScope = parent ? audienceOf(parent) : 'public';
        const current = scope(d);
        if (parentScope !== 'public' && (current === 'public' || current.some(id => !parentScope.includes(id)))) {
            d.audience = 'custom'; d.custom = [...parentScope];
            d.hint = d.hint || t('被引用的是私聊内容，可见范围已设为“{0}”。', audienceText(parentScope));
        }
        // Going online defaults to independent retrieval (each model searches for itself), as the web pill does.
        // Private chats cannot send their content to a search service, so there it falls back to shared retrieval.
        if (data.search) { d.search.mode = d.search.mode || (scope(d) === 'public' ? 'independent' : 'shared'); if (data.search === 'scholar') d.search.scholar = true; else d.search.web = d.search.scholar = true; }
        sync(true); els.input.focus();
        els.input.setSelectionRange(els.input.value.length, els.input.value.length);
    });
    on('room', () => { sync(true); if (activeRoom()) els.input.focus(); });
    // when connections or participants change, chats that never had an explicit choice re-pick who can answer
    for (const name of ['backends', 'models']) on(name, () => { for (const channel of [...targetSets.keys()]) if (!store.get('targets-' + channel)) targetSets.delete(channel); });
    for (const name of ['rooms', 'backends', 'tasks', 'conn', 'history', 'models']) on(name, () => sync());
    on('error', text => { const d = draft(); d.error = text; sync(); });
    sync(true);
}
