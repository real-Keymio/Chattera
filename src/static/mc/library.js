// Research Library: the sources this room's participants found, as evidence behind the chat.
// Collections are views of one list (shared with the room / found by each participant), not folders.
// Three things are kept apart: the source itself, notes written about it (private to their author until shared),
// and citations that tie a claim in an answer to a place in the source.
import { h, icon, ICONS, api, toast, menu, confirmDialog, promptDialog, fmtNum, fmtTime, fmtSize, TOKEN } from './util.js';
import { S, IDS, ALL, on, emit, label, color, sigil, activeRoom } from './state.js';

const LEVEL = { metadata: ['只有文献信息', ''], abstract: ['有摘要', ''], page: ['读过网页', 'yes'], fulltext: ['已取得全文', 'yes'] };
const cache = new Map();   // room id -> items
let filter = 'all', query = '';

export async function loadLibrary(room, force = false) {
    if (!room) return [];
    if (force || !cache.has(room.id)) cache.set(room.id, (await api(`/api/chat/rooms/${room.id}/library`)).items);
    return cache.get(room.id);
}
export const libraryItems = room => cache.get(room?.id) || [];
const who = id => id === 'shared' ? '共享检索' : id === 'user' ? '你' : label(id);
const mark = id => ALL.includes(id) ? sigil(id, 'xs') : null;
const link = (url, text) => h('a', { class: 'link', href: url, target: '_blank', rel: 'noopener noreferrer' }, text);
const act = async (room, item, action, body = {}) => {
    const value = await api(`/api/chat/rooms/${room.id}/library/${item.id}/${action}`, body);
    await loadLibrary(room, true); emit('library', room.id);
    return value;
};
const audience = item => item.shared ? '整个房间可读' : item.holders.length ? `仅 ${item.holders.map(label).join('、')} 可读` : '只有你可见';

function shareMenu(anchor, room, item) {
    const others = IDS.filter(id => !item.holders.includes(id));
    const go = (to, notes) => async () => { try { await act(room, item, 'share', { to, notes }); toast(to === 'none' ? '已收回：只有找到它的参与者可读' : notes ? '已分享资料和笔记' : '已分享资料（不含各自的笔记）'); } catch (e) { toast(e.message, 'error'); } };
    const hasNotes = item.notes.some(n => n.author !== 'user' && n.shared !== true);
    menu(anchor, [
        { title: '把这份资料分享给' },
        { label: '整个房间', icon: 'users', note: '所有参与者可读', checked: item.shared, action: go('room', false) },
        ...others.map(id => ({ label: label(id), note: '只加给它', action: go([id], false) })),
        hasNotes ? 'sep' : null,
        hasNotes ? { label: '整个房间，连同笔记', icon: 'doc', hint: '默认只分享原始资料，让别人先独立阅读；选这一项会把找到它的模型写的笔记也公开', action: go('room', true) } : null,
        item.shared || item.holders.length > new Set(item.finders.map(f => f.by).filter(b => ALL.includes(b))).size ? 'sep' : null,
        item.shared || item.user_set ? { label: '收回分享', icon: 'lock', note: '只留给找到它的参与者', action: go('none', false) } : null,
        'sep', { node: h('p', { class: 'menu-help' }, '分享的是原始资料和来源。模型各自的阅读笔记默认不随资料分享，这样别的模型可以先独立审查原文。') },
    ]);
}

/** The body of one source: used in the library list (expanded) and as a window on the canvas. */
export function sourceBody(item, room, { compact = false, onchange } = {}) {
    const box = h('div', { class: 'src-body' });
    const refresh = async () => { await loadLibrary(room, true); onchange?.(); };
    const run = (fn, done) => async event => {
        const button = event.currentTarget; button.disabled = true;
        try { await fn(); if (done) toast(done); await refresh(); } catch (e) { toast(e.message, 'error'); button.disabled = false; }
    };
    const file = item.file || {};
    const facts = [item.authors?.length ? (Array.isArray(item.authors) ? item.authors.slice(0, 4).join(', ') + (item.authors.length > 4 ? ' 等' : '') : item.authors) : null,
        item.year, item.venue || item.site, item.published && !item.year ? item.published : null].filter(Boolean).join(' · ');
    const textBox = h('div', { class: 'src-text', hidden: true });
    let offset = 0;
    const readMore = async () => {
        try {
            const part = await api(`/api/chat/rooms/${room.id}/library/${item.id}/text?offset=${offset}`);
            textBox.hidden = false;
            textBox.querySelector('.more')?.remove();
            textBox.append(h('pre', null, part.text));
            offset = part.offset + part.returned_characters;
            textBox.append(h('div', { class: 'more hint' }, `已显示 ${fmtNum(offset)} / ${fmtNum(part.total_characters)} 字`, part.complete ? '' : h('button', { class: 'tiny-btn', onclick: readMore }, '继续')));
        } catch (e) { toast(e.message, 'error'); }
    };
    box.append(
        facts ? h('div', { class: 'src-facts' }, facts) : null,
        h('div', { class: 'src-ids' }, item.doi ? link('https://doi.org/' + item.doi, 'DOI ' + item.doi) : link(item.url, item.url.replace(/^https?:\/\/(www\.)?/, '').slice(0, 70)),
            h('span', { class: 'hint' }, ' · ' + ({ 'version of record (DOI)': '正式发表版本', 'web page': '网页' }[item.version] || item.version))),
        h('div', { class: 'src-state' },
            h('span', { class: 'cap ' + LEVEL[item.level][1], title: '实际取得的程度：文献信息 < 摘要 < 读过网页 < 全文' }, LEVEL[item.level][0]),
            h('span', { class: 'cap' + (item.shared ? ' yes' : ''), title: '谁可以读到这份资料' }, audience(item)),
            item.kept ? h('span', { class: 'cap yes', title: '已收藏：不会被缓存清理删除' }, '已收藏') : item.cached ? h('span', { class: 'cap', title: '全文在临时缓存里，空间不够时会被清理，可重新获取' }, '临时缓存') : null,
            file.original_bytes ? h('span', { class: 'cap', title: `原始文件 SHA-256 ${file.sha256 || ''}` }, 'PDF ' + fmtSize(file.original_bytes)) : null),
        h('div', { class: 'src-found' }, '找到它的：', item.finders.slice(-6).map(f => h('span', { class: 'src-finder', title: [f.via, f.query ? '检索词：' + f.query : '', fmtTime(f.at)].filter(Boolean).join(' · ') },
            mark(f.by), who(f.by), f.message_id != null ? h('button', { class: 'tiny-btn', onclick: () => emit('goto-message', f.message_id) }, '#' + f.message_id) : null))),
        item.abstract && !compact ? h('p', { class: 'src-abstract' }, item.abstract) : item.snippet && !compact ? h('p', { class: 'src-abstract' }, item.snippet) : null,
        file.extraction ? h('div', { class: 'hint' }, `全文来源：${{ 'open PDF': '开放获取 PDF', 'web page': '网页正文' }[file.route] || file.route || ''} · ${file.extraction} · ${fmtNum(file.characters)} 字${file.pages ? ` · ${file.pages} 页` : ''} · 获取于 ${fmtTime(file.fetched_at)}。模型默认读文字；需要核对图表、公式或扫描页时，可以按页查看页面图像。`) : null,
        h('div', { class: 'card-actions' },
            h('button', { class: 'btn small', onclick: event => shareMenu(event.currentTarget, room, item) }, icon(ICONS.share, 13), '分享…'),
            item.cached ? h('button', { class: 'btn small', onclick: readMore }, '阅读全文')
                : h('button', { class: 'btn small', title: '按需获取开放全文到临时缓存；需要订阅的论文拿不到', onclick: run(() => act(room, item, 'fetch'), '已取得全文（临时缓存）') }, icon(ICONS.down, 13), '获取全文'),
            h('button', { class: 'btn small', title: item.kept ? '取消收藏：文件回到临时缓存' : '收藏：永久保存全文，不参与缓存清理', onclick: run(() => act(room, item, 'keep', { on: !item.kept }), item.kept ? '已取消收藏' : '已收藏') }, icon(ICONS.pin, 13), item.kept ? '取消收藏' : '收藏'),
            file.original_sha ? h('button', { class: 'btn small', onclick: async () => {
                const response = await fetch(`/api/chat/rooms/${room.id}/library/${item.id}/file`, { headers: { 'X-Session-Token': TOKEN } });
                if (response.ok) window.open(URL.createObjectURL(await response.blob()), '_blank'); else toast('原始文件已不在缓存中，可以重新获取', 'error');
            } }, '打开 PDF') : null,
            file.original_sha ? h('button', { class: 'btn small', title: '把这份 PDF 的页面放到画布上对照（先切到画布模式）', onclick: () => emit('canvas-add', { kind: 'pdf', ref: 'library:' + item.id, item: item.id, name: (item.title || 'PDF').slice(0, 40), page: 1 }) }, '页面到画布') : null,
            h('button', { class: 'btn small ghost', onclick: () => promptDialog('写一条笔记', '', '保存', async text => { await act(room, item, 'notes', { text }); onchange?.(); }, { placeholder: '只有你能看到，除非之后选择分享给模型', maxLength: 2000 }) }, '写笔记'),
            h('button', { class: 'btn small ghost danger', onclick: () => confirmDialog('从资料库移除', `“${(item.title || item.url).slice(0, 60)}”会从这个房间的资料库移除，相关笔记和引用记录一并删除。聊天里的回答不受影响。`, '移除', async () => { await act(room, item, 'remove'); onchange?.(); }, true) }, '移除')),
        textBox);
    if ((item.page_views || []).length) box.append(h('div', { class: 'hint' }, '看过页面图像（不只是文字）：', [...new Map(item.page_views.map(v => [v.by + ':' + v.page, v])).values()].slice(-12).map(v => `${who(v.by)} 第 ${v.page} 页`).join('、')));
    if (item.notes.length) box.append(h('div', { class: 'src-sec' }, h('h5', null, '笔记'), item.notes.map(n => h('div', { class: 'src-note', style: { '--hue': ALL.includes(n.author) ? color(n.author) : 'var(--ink-3)' } },
        h('div', { class: 'src-note-head' }, mark(n.author), h('strong', null, who(n.author)), h('span', { class: 'hint' }, fmtTime(n.at)),
            h('span', { class: 'cap' + (n.shared === true ? ' yes' : '') }, n.shared === true ? '已分享给房间' : Array.isArray(n.shared) && n.shared.length ? '已分享给 ' + n.shared.map(label).join('、') : n.author === 'user' ? '仅自己' : '仅作者可见'),
            h('button', { class: 'tiny-btn', onclick: async () => { await act(room, item, 'note-share', { note_id: n.id, on: n.shared !== true }); onchange?.(); } }, n.shared === true ? '收回' : '分享给房间'),
            h('button', { class: 'tiny-btn danger', onclick: async () => { await act(room, item, 'note-remove', { note_id: n.id }); onchange?.(); } }, '删除')),
        h('p', null, n.text)))));
    if (item.citations.length) box.append(h('div', { class: 'src-sec' }, h('h5', null, '被引用'), item.citations.slice(-12).map(c => h('div', { class: 'src-cite', style: { '--hue': ALL.includes(c.by) ? color(c.by) : 'var(--ink-3)' } },
        h('div', null, mark(c.by), h('strong', null, who(c.by)), c.locator ? h('span', { class: 'cap' }, c.locator) : null,
            c.message_id != null ? h('button', { class: 'tiny-btn', onclick: () => emit('goto-message', c.message_id) }, '回答 #' + c.message_id) : null),
        c.claim ? h('p', { class: 'claim' }, c.claim) : null, c.quote ? h('blockquote', null, c.quote) : null))));
    return box;
}

const open = new Set();
/** Room drawer → 资料. */
export async function libraryTab(room, box, rerender) {
    let items;
    try { items = await loadLibrary(room, true); } catch (e) { box.append(h('p', { class: 'form-error' }, e.message)); return; }
    const finders = [...new Set(items.flatMap(i => i.holders))].filter(id => ALL.includes(id));
    const counts = { all: items.length, shared: items.filter(i => i.shared).length };
    const chip = (key, text, id) => h('button', { class: 'lib-chip' + (filter === key ? ' active' : ''), style: id ? { '--hue': color(id) } : null, onclick: () => { filter = key; rerender(); } }, id ? sigil(id, 'xs') : null, text);
    const search = h('input', { class: 'input small', type: 'search', placeholder: '搜索标题、作者、DOI', value: query, oninput: event => { query = event.target.value; draw(); } });
    const list = h('div', { class: 'lib-list' });
    const draw = () => {
        const q = query.trim().toLowerCase();
        const shown = items.filter(i => (filter === 'all' || (filter === 'shared' ? i.shared : i.holders.includes(filter)))
            && (!q || [i.title, i.doi, i.url, Array.isArray(i.authors) ? i.authors.join(' ') : i.authors].join(' ').toLowerCase().includes(q)));
        list.replaceChildren(...(shown.length ? shown.map(item => {
            const expanded = open.has(item.id);
            const row = h('div', { class: 'lib-item' + (expanded ? ' open' : ''), dataset: { id: item.id } },
                h('button', { class: 'lib-head', onclick: () => { expanded ? open.delete(item.id) : open.add(item.id); draw(); } },
                    h('span', { class: 'lib-kind' }, icon(item.kind === 'paper' ? ICONS.book : ICONS.globe, 15)),
                    h('span', { class: 'lib-title' }, h('strong', null, item.title || item.url.replace(/^https?:\/\/(www\.)?/, '')),
                        h('small', null, [item.year, LEVEL[item.level][0], item.notes.length ? `${item.notes.length} 条笔记` : null, item.citations.length ? `被引用 ${item.citations.length} 处` : null].filter(Boolean).join(' · '))),
                    h('span', { class: 'lib-who' }, item.shared ? h('span', { class: 'cap yes', title: '整个房间可读' }, '共享') : null, item.holders.slice(0, 4).map(id => mark(id)))),
                expanded ? sourceBody(item, room, { onchange: rerender }) : null);
            return row;
        }) : [h('p', { class: 'hint' }, items.length ? '这个分类下没有资料。' : '这个房间还没有资料。打开“联网”提问后，模型读过的网页、引用的来源和查到的文献会记在这里；也可以自己按 DOI 或网址添加。')]));
    };
    box.append(
        h('p', { class: 'hint' }, '模型在这个聊天里找到并用到的资料。独立检索找到的归各自所有，共享检索的归房间；你决定把哪一份分享给谁。这里只记录文献信息，全文在需要时才获取。'),
        h('div', { class: 'lib-chips' }, chip('all', `全部 ${counts.all}`), chip('shared', `房间共享 ${counts.shared}`), finders.map(id => chip(id, `${label(id)} ${items.filter(i => i.holders.includes(id)).length}`, id))),
        h('div', { class: 'inline lib-tools' }, search, h('button', { class: 'btn small', onclick: () => promptDialog('添加资料', '', '添加', async ref => {
            if (!ref) throw new Error('请填写 DOI 或网址。');
            await api(`/api/chat/rooms/${room.id}/library`, { ref }); rerender();
        }, { placeholder: '10.1038/… 或 https://…', maxLength: 600, note: '只记录文献信息，不下载文件。添加后默认整个房间可读。' }) }, icon(ICONS.plus, 13), '添加')),
        list,
        h('p', { class: 'hint' }, '把一条资料拖到画布上，可以和回答并排阅读。缓存位置和空间上限在 设置 → 数据。'));
    draw();
}

/** Settings → 数据: where the library lives and how much room it may take. */
export async function librarySettings(box) {
    const draw = async () => {
        const s = await api('/api/chat/library/stats');
        const limit = h('select', { class: 'input' }, [[256, '256 MB'], [512, '512 MB'], [1024, '1 GB'], [2048, '2 GB'], [5120, '5 GB'], [20480, '20 GB']].map(([mb, text]) => h('option', { value: mb, selected: Math.round(s.limit_bytes / 1048576) === mb }, text)));
        limit.addEventListener('change', async () => { try { await api('/api/chat/library/settings', { cache_limit_bytes: Number(limit.value) * 1048576 }); toast('缓存上限已更新'); draw(); } catch (e) { toast(e.message, 'error'); } });
        box.replaceChildren(
            h('p', { class: 'hint' }, '资料库先记录文献信息（标题、作者、DOI、来源、谁找到的），全文只在有人需要阅读时才获取。同一个文件按内容只保存一份，不管被多少房间、多少模型引用。'),
            h('dl', { class: 'kv' },
                h('dt', null, '位置'), h('dd', null, h('code', null, s.root)),
                h('dt', null, '文献索引'), h('dd', null, `${s.rooms} 个房间 · ${fmtSize(s.index_bytes)}`),
                h('dt', null, '临时缓存'), h('dd', null, `${fmtSize(s.cache_bytes)} / 上限 ${fmtSize(s.limit_bytes)}（超出时自动删除最久没用的）`),
                h('dt', null, '已收藏'), h('dd', null, `${fmtSize(s.kept_bytes)}（不参与清理）`),
                h('dt', null, '文件数'), h('dd', null, `${s.files} 个，其中原始 PDF ${s.originals} 个`)),
            h('div', { class: 'set-row' }, h('div', { class: 'set-label' }, h('span', null, '临时缓存上限'), h('small', null, '读过不等于收藏：没有收藏的全文只是缓存，可以随时重新获取。')), limit),
            h('div', { class: 'card-actions' },
                h('button', { class: 'btn', onclick: async () => { const r = await api('/api/chat/library/cleanup', { everything: true }); toast(`已清理 ${fmtSize(r.removed_bytes)} 临时缓存；收藏的资料保留`); draw(); } }, '清空临时缓存'),
                h('button', { class: 'btn', onclick: () => promptDialog('资料库位置', s.root, '保存', async root => { await api('/api/chat/library/settings', { root }); toast('已切换位置；原位置的文件没有移动'); draw(); },
                    { placeholder: '完整路径，例如 D:\\Research\\ChatteraLibrary', maxLength: 240, note: '新位置从空开始；原来的资料留在原位置，不会自动搬迁。留空则回到默认位置。' }) }, '更改位置…')));
    };
    try { await draw(); } catch (e) { box.replaceChildren(h('p', { class: 'form-error' }, e.message)); }
}

on('room', () => { filter = 'all'; });
