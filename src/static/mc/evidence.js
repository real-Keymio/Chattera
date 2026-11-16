// Evidence and transparency: the shared retrieval record of a turn, and what one answer was actually given
// (documents and how much of them, pages that were OCR, images, retrieved sources and their depth, tool calls).
import { h, icon, ICONS, api, modal, fmtNum } from './util.js';
import { S, label, color, sigil, lensOf } from './state.js';
import { t } from './i18n.js';

const LEVEL = { snippet: t('仅搜索摘要'), page: t('已读网页正文'), metadata: t('仅文献信息'), abstract: t('已读摘要'), fulltext: t('已读全文'), failed: t('未取得') };
const TOOL = { chat_read: t('读取历史消息'), chat_search: t('搜索历史'), chat_context: t('读取摘要'), attachment_read: t('读取附件'), research_search: t('学术检索'),
    research_read: t('读取文献'), web_search: t('网页搜索'), web_read: t('读取网页'), calc: t('数值核查') };
const bundles = new Map();
const hostOf = url => { try { return new URL(url).hostname.replace(/^www\./, ''); } catch (e) { return ''; } };

function loadBundle(id) {
    if (!bundles.has(id)) bundles.set(id, api('/api/chat/evidence/' + id).catch(error => { bundles.delete(id); throw error; }));
    return bundles.get(id);
}

function sourceRow(item, extra = null) {
    const link = item.url ? h('a', { href: item.url, target: '_blank', rel: 'noopener noreferrer' }, item.title || item.url) : h('strong', null, item.title || t('（无标题）'));
    const meta = [h('span', { class: 'evi-level', dataset: { level: item.level || item.found_level } }, LEVEL[item.level || item.found_level] || item.level)];
    if (item.kind === 'paper') {
        const authors = (item.authors || []).slice(0, 3).join(', ') + ((item.authors || []).length > 3 ? t(' 等') : '');
        if (authors) meta.push(h('span', null, authors));
        if (item.year) meta.push(h('span', null, String(item.year)));
        if (item.venue) meta.push(h('span', null, item.venue));
        if (item.doi) meta.push(h('span', null, 'doi:' + item.doi));
        if (item.fulltext_route) meta.push(h('span', { title: t('全文来源') }, item.fulltext_route));
    } else {
        if (hostOf(item.url)) meta.push(h('span', null, hostOf(item.url)));
        if (item.date) meta.push(h('span', null, String(item.date).slice(0, 10)));
        if (item.read_via) meta.push(h('span', { title: t('直接读取失败后，通过公共阅读服务取得正文') }, t('经阅读服务')));
    }
    if (item.total_characters) meta.push(h('span', null, t('{0} 字', fmtNum(item.total_characters))));
    if (item.read_error && !['page', 'fulltext'].includes(item.level)) meta.push(h('span', { title: item.read_error }, t('正文未能读取')));
    const text = item.snippet || (item.abstract || '').slice(0, 320);
    return h('div', { class: 'evi-item' }, h('span', { class: 'evi-n' }, `[${item.n}]`), h('div', null, link, h('div', { class: 'evi-meta' }, meta, extra)),
        text ? h('div', { class: 'evi-snippet' }, text) : null);
}

/** The retrieval record shown in the timeline: collapsed by default, sources on demand. */
export function evidenceCard(msg) {
    const meta = msg.metadata || {}, counts = meta.evidence_counts || {};
    const first = String(msg.text || '').split('\n')[0].replace(/^(检索证据|Retrieved evidence) · /, '');
    const body = h('div', { class: 'evi-body' });
    const card = h('section', { class: 'evi', dataset: { id: msg.id } });
    let loaded = false;
    const head = h('button', { class: 'evi-head', 'aria-expanded': 'false', onclick: async () => {
        const open = card.classList.toggle('open');
        head.setAttribute('aria-expanded', String(open));
        if (!open || loaded) return;
        loaded = true;
        body.replaceChildren(h('p', { class: 'hint' }, t('正在读取检索记录…')));
        try {
            const bundle = await loadBundle(meta.evidence_id);
            const queries = Object.entries(bundle.queries || {}).flatMap(([kind, list]) => list.map(q => h('code', { title: kind === 'web' ? t('网页搜索') : t('学术检索') }, q)));
            body.replaceChildren(
                h('div', { class: 'evi-queries' }, t('检索词'), queries, h('span', null, bundle.query_source === 'user query' ? t('（你填写的）') : bundle.query_source === 'query planner' ? t('（由模型根据问题拟定）') : t('（直接使用问题原文）'))),
                bundle.items.map(item => sourceRow(item)),
                bundle.errors?.length ? h('div', { class: 'evi-errors' }, bundle.errors.map(e => h('div', null, e))) : null,
                h('p', { class: 'hint' }, t('这份检索由系统执行一次（{0} 秒），本轮所有回答者拿到的是同一份资料；各模型实际收到多少正文，可在回答下方的“依据”里查看。', bundle.elapsed_seconds ?? '?')));
        } catch (e) { loaded = false; body.replaceChildren(h('p', { class: 'form-error' }, e.message)); }
    } },
        h('span', { class: 'evi-icon' }, icon(ICONS.search, 15)),
        h('span', { class: 'evi-title' }, t('检索到 {0} 条来源', counts.total ?? '?')),
        h('span', { class: 'evi-sub' }, counts.read ? t('其中 {0} 条已读正文 · {1}', counts.read, first) : first),
        h('span', { class: 'chev' }, icon(ICONS.chevron, 14)));
    card.append(head, body);
    return card;
}

/** One line under an answer: what it was based on. Only shown when there is something to say. */
export function basisStrip(msg, author) {
    const meta = msg.metadata || {}, id = meta.task_id || meta.source_task_id, task = id ? S.tasks.get(id) : null;
    if (!task) return null;
    const chips = [], open = () => openMaterials(id, author);
    const chip = (name, text, cls = '', title = '') => chips.push(h('button', { class: 'basis-chip ' + cls, title: title || t('查看这次回答实际拿到的材料'), onclick: open }, icon(ICONS[name], 12), text));
    const docs = (task.attachment_sources || []).length, images = (task.image_processing || []).filter(i => i.image_sha256).length;
    if (docs - images > 0) chip('doc', t('{0} 份文档', docs - images));
    if (images) chip('eye', t('{0} 张图片', images));
    const delivery = task.evidence_delivery || [];
    if (delivery.length) {
        const read = delivery.filter(d => d.given_characters > 0).length, cut = delivery.filter(d => d.total_characters > 0 && d.given_characters === 0).length;
        chip('search', t('检索资料 {0} 条 · 读到正文 {1} 条', delivery.length, read), cut ? 'warn' : '', cut ? t('有 {0} 条来源的正文因容量没有给到这个模型', cut) : '');
    }
    if (task.search) {
        const s = task.search_summary || {}, did = (s.searches || 0) + (s.pages || 0) > 0;
        if (task.search.path === 'none') chip('lock', t('未联网 · 私聊'), 'warn', t('私聊上下文不会发给搜索服务，这次回答没有联网'));
        else if (task.state === 'completed') chip('globe', did ? t('{0} · 搜索 {1} 次{2}{3}', pathName(task.search), s.searches || 0, s.pages ? t(' · 读 {0} 页', s.pages) : '', s.sources ? t(' · {0} 个来源', s.sources) : '') : t('{0} · 没有实际搜索', pathName(task.search)),
            !did || s.errors ? 'warn' : '', !did ? t('这次回答开启了联网，但模型没有执行搜索：内容来自它已有的知识') : s.errors ? t('有搜索或读取失败，点开查看') : t('查看这个模型自己搜了什么、读了什么'));
    }
    const calls = (task.tool_calls || []).filter(n => !task.search || !/^(mcp__room__)?(web_search|web_read|research_search|research_read|WebSearch|WebFetch)$/.test(String(n)));
    if (calls.length) {
        const names = [...new Set(calls)].map(n => TOOL[String(n).replace(/^mcp__room__/, '')] || n);
        chip('tool', t('自行调用工具 {0} 次 · {1}', calls.length, names.slice(0, 3).join(t('、'))));
    }
    if (task.research_lens) chip('lens', task.research_lens.name, '', t('这次回答使用的研究视角'));
    if ((task.trimmed_history_ranges || []).length) chip('history', t('部分历史未带入'), 'warn', t('因容量限制，较早的一些消息没有进入这次输入'));
    if (!chips.length) return null;
    return h('div', { class: 'basis', style: { '--hue': color(author) } }, chips);
}

const PATHS = { native: t('官方搜索'), 'multichat-tools': t('Chattera 检索工具'), 'multichat-planned': t('Chattera 代为检索'), none: t('未联网') };
const NATIVE = { claude: t('Claude Code 官方搜索'), codex: t('Codex 官方搜索'), deepseek: t('DeepSeek 官方搜索'), google: t('Google 搜索'), web_search_tool: t('服务商官方搜索') };
const pathName = plan => plan?.path === 'native' ? NATIVE[plan.native] || t('官方搜索') : PATHS[plan?.path] || t('联网');
const MODE_NAMES = { independent: t('独立检索：这个模型自己决定搜什么、读什么，其他模型各查各的'), shared: t('共享检索'), hybrid: t('混合检索：先收到一份共同资料，再由这个模型自行扩展') };
const clock = at => at ? new Date(at * 1000).toLocaleTimeString('zh-CN', { hour12: false }) : '';
const tidy = url => String(url || '').replace(/[)\].,;，。；、）】]+$/, '').replace(/^https?:\/\/(www\.)?/, '').replace(/\/$/, '').toLowerCase();
/** What one answer searched and read by itself, and how the links in its text relate to that record. */
function searchView(search, answer) {
    const plan = search.plan || {}, t = search.totals || {};
    const out = [h('div', { class: 'mat-line' }, h('strong', null, pathName(plan)), plan.label ? h('span', { class: 'hint' }, `  ${plan.label}`) : null),
        h('div', { class: 'mat-line hint' }, MODE_NAMES[plan.mode] || '', plan.path === 'none' ? t(' 这是私聊上下文，外部搜索被关闭。') : t(' 共搜索 {0} 次，读取 {1} 个网页，得到 {2} 个不同来源。', t.searches || 0, t.pages || 0, t.sources || 0))];
    const known = new Set();
    for (const e of search.entries || []) {
        for (const r of e.results || []) known.add(tidy(r.url));
        if (e.kind === 'read' && e.url) known.add(tidy(e.url));
        const what = e.kind === 'search' ? (e.query ? [e.query] : e.queries || []).map(q => `“${q}”`).join('  ') || t('（服务商没有给出查询词）') : e.kind === 'read' ? e.url : '';
        out.push(h('div', { class: 'trace ' + e.kind + (e.error ? ' bad' : '') },
            h('div', { class: 'trace-head' }, h('span', { class: 'trace-kind' }, e.kind === 'search' ? t('搜索') : e.kind === 'read' ? t('读取') : t('提示')), h('span', { class: 'trace-what' }, e.kind === 'read' ? link(e.url, e.title || e.url) : what),
                h('span', { class: 'trace-meta' }, [e.via, e.provider, clock(e.at), e.seconds ? e.seconds + t(' 秒') : null].filter(Boolean).join(' · '))),
            e.error ? h('div', { class: 'err-text trace-note' }, e.error) : null,
            e.kind === 'read' && !e.error ? h('div', { class: 'hint trace-note' }, readLevel(e)) : null,
            e.note ? h('div', { class: 'hint trace-note' }, { 'Codex reports its queries and the pages it opens, not the result list': t('Codex 只报告查询词和它打开的网页，不提供结果列表。'),
                'Google returns redirect links and site names; page titles are not provided': t('Google 返回的是跳转链接和站点名，不提供网页标题。'), 'the provider returned its sources but not the queries it ran': t('服务商返回了来源，但没有给出它使用的查询词。') }[e.note] || e.note) : null,
            (e.results || []).length ? h('ol', { class: 'trace-results' }, e.results.map(r => h('li', null, link(r.url, r.title || r.url), r.site || r.published ? h('span', { class: 'hint' }, '  ' + [r.site, r.published].filter(Boolean).join(' · ')) : null,
                r.snippet ? h('div', { class: 'hint' }, r.snippet.slice(0, 160)) : null))) : null));
    }
    if (!(search.entries || []).length && plan.path !== 'none') out.push(h('p', { class: 'msg-note warn' }, t('这次回答开启了联网，但记录里没有任何搜索或读取：回答来自模型已有的知识，其中的链接没有经过这次检索核实。')));
    if ((search.citations || []).length) out.push(h('div', { class: 'mat-line' }, h('strong', null, t('服务商标注的引用')), h('span', { class: 'hint' }, t('  回答里的这些片段由服务商直接关联到来源'))),
        h('ol', { class: 'trace-results' }, search.citations.slice(0, 24).map(c => h('li', null, answer && Number.isInteger(c.end) ? h('span', { class: 'hint' }, `“${answer.slice(c.start || 0, c.end).replace(/\s+/g, ' ').slice(0, 46)}…” → `) : null, link(c.url, c.title || c.url)))));
    const links = [...new Set((answer || '').match(/https?:\/\/[^\s<>"'）)\]】]+/g) || [])].slice(0, 30);
    if (links.length && plan.path !== 'none') {
        const redirect = [...known].some(u => u.startsWith('vertexaisearch.cloud.google.com'));
        out.push(h('div', { class: 'mat-line' }, h('strong', null, t('回答里出现的链接'))), h('ul', { class: 'trace-results links' }, links.map(u => {
            const mine = tidy(u), hit = known.has(mine) || [...known].some(k => k && (k.startsWith(mine) || mine.startsWith(k)));
            return h('li', null, h('span', { class: 'cap ' + (hit ? 'yes' : 'warn') }, hit ? t('在检索记录中') : redirect ? t('无法比对') : t('不在检索记录中')), ' ', link(u, u));
        })), h('p', { class: 'hint' }, redirect ? t('Google 搜索只返回跳转链接，无法与回答里的原始网址逐一比对。') : t('“不在检索记录中”的链接不是这次搜索或读取得到的，可能来自模型的记忆，请自行核实。')));
    }
    if (search.usage && search.usage !== 'UNKNOWN') out.push(h('div', { class: 'mat-line hint' }, t('服务商报告的搜索用量：') + JSON.stringify(search.usage)));
    return out;
}
const link = (url, text) => h('a', { class: 'link', href: url, target: '_blank', rel: 'noopener noreferrer' }, String(text).slice(0, 110));
const readLevel = e => e.level === 'page text read' || e.level === 'full text read' ? t('读到正文{0}', e.characters ? t(' {0} / {1} 字', fmtNum(e.characters), fmtNum(e.total_characters)) : '')
    : e.level === 'abstract read' ? t('只读到摘要') : e.level === 'bibliographic metadata only' ? t('只有文献信息') : e.level === 'page read by the CLI' ? t('由 CLI 读取网页{0}', e.bytes ? t('（{0} 字节）', fmtNum(e.bytes)) : '')
    : e.level === 'page image viewed' ? t('看了第 {0} 页的页面图像（图表、公式按原样可见）', e.page)
    : e.level === 'page opened by the CLI' ? t('由 CLI 打开网页{0}', e.find ? t('，页内查找“{0}”', e.find) : '') : e.level === 'page read by the provider' ? t('由服务商读取网页') : e.level || '';
const range = list => (list || []).map(r => Array.isArray(r) ? (r[0] === r[1] ? `#${r[0]}` : `#${r[0]}–#${r[1]}`) : `#${r}`).join(t('、')) || t('无');

function documentView(doc, hue) {
    const total = doc.total_characters || 0, initial = doc.initial?.coverage;
    const toolChars = doc.tool_reads.reduce((sum, r) => sum + (r.coverage?.returned_characters || 0), 0);
    const given = initial?.returned_characters || 0;
    const unit = doc.unit_type === 'page' ? t('页') : t('段');
    const got = new Set();
    if (initial?.returned_units?.length === 2) for (let u = initial.returned_units[0]; u <= initial.returned_units[1]; u++) got.add(u);
    for (const read of doc.tool_reads) if (read.coverage?.returned_units?.length === 2) for (let u = read.coverage.returned_units[0]; u <= read.coverage.returned_units[1]; u++) got.add(u);
    const ocr = new Set(doc.ocr_units || []);
    const pages = doc.unit_type === 'page' && doc.total_units <= 120
        ? h('div', { class: 'mat-pages' }, Array.from({ length: doc.total_units }, (_, i) => h('i', { class: (got.has(i + 1) ? 'got' : '') + (ocr.has(i + 1) ? ' ocr' : ''),
            title: t('第 {0} 页：{1}{2}', i + 1, got.has(i + 1) ? t('文字已给到模型') : t('未给到模型'), ocr.has(i + 1) ? t('（OCR 识别）') : '') }, i + 1))) : null;
    return h('div', { class: 'mat-doc', style: { '--hue': hue } },
        h('strong', null, doc.name || t('文档')), ' ', h('span', { class: 'hint' }, t('{0}{1} · 共 {2} 字', (doc.format || '').toUpperCase(), doc.page_count ? t(' · {0} 页', doc.page_count) : '', fmtNum(total))),
        h('div', { class: 'mat-bar' }, h('i', { style: { width: (total ? Math.min(100, given / total * 100) : 0) + '%' } }),
            h('i', { class: 'tool', style: { width: (total ? Math.min(100 - Math.min(100, given / total * 100), toolChars / total * 100) : 0) + '%' } })),
        h('div', { class: 'mat-line' }, initial
            ? t('提问时直接给到：{0} 字{1}{2}', fmtNum(given), initial.returned_units?.length === 2 ? t('（第 {0}–{1} {2}，共 {3} {4}）', initial.returned_units[0], initial.returned_units[1], unit, initial.total_units, unit) : '', initial.complete ? t(' · 全文') : t(' · 部分'))
            : t('提问时没有直接给到正文（属于更早的消息）。'),
            doc.tool_reads.length ? t('；模型随后自行读取 {0} 次，约 {1} 字', doc.tool_reads.length, fmtNum(toolChars)) : (initial && !initial.complete ? t('；模型没有再读取其余部分') : '')),
        doc.initial?.extraction ? h('div', { class: 'mat-line hint' }, describeExtraction(doc)) : null, pages,
        doc.unit_type === 'page' ? h('div', { class: 'mat-line hint' }, t('模型拿到的是提取出的文字，看不到页面里的图表与版式，除非该页作为图片另外附上。')) : null);
}
function describeExtraction(doc) {
    if (doc.ocr_pages?.length) return t('文字层 + OCR：第 {0} 页由 OCR 识别，公式、表格和小字可能有误。', doc.ocr_pages.slice(0, 30).join(t('、')));
    if (doc.pages_without_text?.length) return t('第 {0} 页没有可提取的文字（扫描或图片页），未做 OCR。', doc.pages_without_text.slice(0, 30).join(t('、')));
    return doc.format === 'pdf' ? t('PDF 文字层。') : t('由原文件转换得到的文字。');
}

/** Detail view: what this one answer actually received. */
export async function openMaterials(taskId, author) {
    const dialog = modal({ title: t('这次回答的依据'), wide: true, body: h('p', { class: 'hint' }, t('正在读取调用记录…')) });
    let m;
    try { m = await api(`/api/chat/tasks/${taskId}/materials`); }
    catch (e) { dialog.body.replaceChildren(h('p', { class: 'form-error' }, e.message)); return; }
    const who = m.model || author, hue = color(who);
    const sec = (title, ...kids) => h('section', { class: 'mat-sec' }, h('h4', null, title), kids);
    const out = [h('p', { class: 'modal-text' }, sigil(who), ' ', h('strong', null, label(who)), t(' · 请求模型 {0}', m.requested_model ?? t('未知')),
        m.actual_model && m.actual_model !== 'UNKNOWN' && m.actual_model !== m.requested_model ? t('（服务端报告 {0}）', m.actual_model) : '', m.effort && m.effort !== 'UNKNOWN' ? t(' · 推理强度 {0}', m.effort) : '',
        m.lens ? t(' · 研究视角：{0}', m.lens.name) : '')];
    out.push(sec(t('文档'), m.documents.length ? m.documents.map(d => documentView(d, hue)) : h('p', { class: 'hint' }, t('这次调用没有涉及文档。'))));
    if (m.images.length) out.push(sec(t('图片（模型直接看到像素）'), m.images.map(i => h('div', { class: 'mat-line' }, `${i.name || t('图片')}${i.prepared_size ? t(' · 送入尺寸 {0}', i.prepared_size.join('×')) : ''}`))));
    if (m.shared_evidence) {
        const e = m.shared_evidence, total = e.items.reduce((s, i) => s + i.given_characters, 0);
        out.push(sec(t('共享检索资料（{0} 条，给到这个模型的正文共 {1} 字）', e.items.length, fmtNum(total)),
            e.items.map(i => sourceRow({ ...i, level: i.given_level },
                i.given_characters ? h('span', null, t('给到 {0} / {1} 字', fmtNum(i.given_characters), fmtNum(i.total_characters)))
                    : i.total_characters ? h('span', { class: 'err-text' }, t('正文因容量未给到这个模型')) : null))));
    }
    if (m.search) {
        const reply = [...S.msgs.values()].find(x => (x.metadata?.task_id || x.metadata?.source_task_id) === taskId);
        out.push(sec(t('联网检索（这个模型自己的搜索）'), searchView(m.search, reply?.text || '')));
    }
    out.push(sec(t('模型自行调用的工具'), m.tool_calls.length ? m.tool_calls.map(c => {
        const a = c.args || {}, s = c.summary || {};
        const what = a.query ? `“${a.query}”` : a.url ? a.url : a.expression ? a.expression : a.message_id != null ? t('消息 #{0}{1}', a.message_id, a.unit ? t(' 第 {0} 单元起', a.unit) : '') : '';
        const result = c.status !== 'success' ? t('失败{0}', c.error ? t('：') + c.error : '') : s.results ? t('{0} 条结果', s.results.length) : s.level ? t('{0} · {1} / {2} 字', LEVEL[{ 'page text read': 'page', 'full text read': 'fulltext', 'abstract read': 'abstract' }[s.level]] || s.level, fmtNum(s.characters), fmtNum(s.total_characters))
            : s.coverage ? t('{0} 字', fmtNum(s.coverage.returned_characters)) : t('完成');
        return h('div', { class: 'mat-line' }, h('strong', null, TOOL[c.tool] || c.tool), ' ', what, ' → ', h('span', { class: c.status === 'success' ? '' : 'err-text' }, result),
            s.results ? h('div', { class: 'hint' }, s.results.slice(0, 5).map(r => r.title || r.url).join(t('；'))) : null);
    }) : h('p', { class: 'hint' }, m.tools_available ? (m.external_tools_allowed ? t('模型可以调用工具，但这次没有调用：回答只基于上面给到的材料和它自身的知识。') : t('这是私聊上下文：外部检索工具被关闭，模型只能读取本房间内容。'))
        : t('这个连接不支持工具调用：模型不能自己检索或翻页，只能使用上面给到的材料。'))));
    out.push(sec(t('对话历史'), h('div', { class: 'mat-line' }, t('带入 {0} 条较早的消息', m.history.included),
        m.history.summary_version != null ? t(' · 使用摘要 v{0}（覆盖 {1}）', m.history.summary_version, range(m.history.summarized)) : '',
        m.history.trimmed.length ? t(' · 因容量未带入：{0}', range(m.history.trimmed)) : t(' · 没有因容量被裁剪的历史'),
        m.pins ? t(' · 固定内容 {0} 条', m.pins) : ''),
        h('div', { class: 'mat-line hint' }, t('输入估算 {0} / 容量 {1}（{2}，保守估算，不是服务商计费）', fmtNum(m.input_estimate), fmtNum(m.budget), m.budget_unit || ''))));
    out.push(h('p', { class: 'hint' }, t('“直接给到”是系统在提问时放进输入的内容；“自行调用”是模型之后通过工具读取的内容。没有出现在这里的资料，模型没有读过。')));
    dialog.body.replaceChildren(...out);
}
