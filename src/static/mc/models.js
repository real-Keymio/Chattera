// Model management.
//   Connection  — a provider account or a locally signed-in subscription CLI: where a request goes and how it authenticates.
//   Model       — something a connection can run. Being listed is a declaration, not proof that it has been called.
//   Participant — who speaks in a chat: a stable id, a name and a mark, bound to one connection + model.
// API keys go straight to the server's private key directory; this module never keeps or displays one.
import { h, icon, ICONS, api, toast, modal, actionButton, confirmDialog, promptDialog, fmtNum, fmtTime, menu } from './util.js';
import { S, IDS, label, color, sigil, loadModels, loadBackends, refreshRooms, effectiveModel, adoptModels, participantOf, effortName } from './state.js';
import { friendlyReason } from './panes.js';

const PROTOCOL = { 'openai-chat': 'OpenAI 兼容', 'anthropic-messages': 'Anthropic Messages', 'google-generate-content': 'Google Gemini 原生' };
const SEARCH_KIND = { claude: 'Claude Code 官方搜索（WebSearch / WebFetch）', codex: 'Codex 官方搜索（web_search）', deepseek: 'DeepSeek 服务端搜索与网页读取', google: 'Google Search Grounding', web_search_tool: '服务商的 web_search 工具' };
const WINDOW_STATUS = { VERIFIED: '实测', DOCUMENTED: '官方文档', 'PROVIDER-REPORTED': '服务商报告', UNKNOWN: '未核实' };
/** Starting points for a new connection. Addresses are the providers' documented OpenAI-compatible endpoints; all can be edited. */
const PRESETS = [
    { id: 'deepseek', name: 'DeepSeek', url: 'https://api.deepseek.com', reasoning: true },
    { id: 'mimo', name: '小米 MiMo', url: 'https://api.xiaomimimo.com/v1' },
    { id: 'qwen', name: '通义千问 Qwen', sub: '阿里云百炼', url: 'https://dashscope.aliyuncs.com/compatible-mode/v1' },
    { id: 'openai', name: 'OpenAI', url: 'https://api.openai.com/v1', reasoning: true },
    { id: 'anthropic', name: 'Anthropic', sub: 'Claude API', url: 'https://api.anthropic.com/v1', protocol: 'anthropic-messages' },
    { id: 'gemini', name: 'Google Gemini', url: 'https://generativelanguage.googleapis.com/v1beta', protocol: 'google-generate-content' },
    { id: 'openrouter', name: 'OpenRouter', url: 'https://openrouter.ai/api/v1' },
    { id: 'siliconflow', name: '硅基流动', url: 'https://api.siliconflow.cn/v1' },
    { id: 'moonshot', name: 'Kimi', sub: 'Moonshot', url: 'https://api.moonshot.cn/v1' },
    { id: 'zhipu', name: '智谱 GLM', url: 'https://open.bigmodel.cn/api/paas/v4' },
    { id: 'volc', name: '豆包', sub: '火山方舟', url: 'https://ark.cn-beijing.volces.com/api/v3' },
    { id: 'custom', name: '其他服务', sub: '自己填写地址', url: '' },
];

const overview = () => S.models || { profiles: [], catalog: {}, combos: [], participants: [], lenses: [] };
const profileOf = name => overview().profiles.find(p => p.name === name);
const catalogOf = name => overview().catalog[name] || [];
const people = () => overview().participants || [];
const speakersOf = (name, model) => people().filter(p => !p.retired && p.connection === name && (model === undefined || (p.model || profileOf(name)?.model) === model));
export const connectionName = name => {
    if (name === 'legacy') return '未设置';
    if (name === 'mock') return '测试用（不调用模型）';
    const p = profileOf(name);
    return p ? (p.kind === 'cli' ? p.label + ' · 订阅' : p.label) : name;
};
const modelName = (name, model) => catalogOf(name).find(m => m.id === model)?.label || model || '—';
const windowText = entry => entry?.window_tokens ? `${entry.window_tokens >= 1000000 ? (entry.window_tokens / 1e6).toFixed(entry.window_tokens % 1e6 ? 2 : 0) + 'M' : Math.round(entry.window_tokens / 1000) + 'K'}` : '';

async function act(path, body, method) {
    const value = await api(path, body, method);
    adoptModels(value?.overview || value);
    await loadBackends();
    return value;
}
/** replaceChildren that ignores null/false and flattens arrays (the native one would print "null"). */
const put = (el, ...kids) => el.replaceChildren(...kids.flat(Infinity).filter(kid => kid !== null && kid !== undefined && kid !== false));
const BRANDS = { mimo: 'MiMo', gpt: 'GPT', qwen: 'Qwen', deepseek: 'DeepSeek', glm: 'GLM', kimi: 'Kimi', claude: 'Claude', gemini: 'Gemini', llama: 'Llama', doubao: 'Doubao', grok: 'Grok', mistral: 'Mistral', ernie: 'ERNIE' };
/** A readable participant name for a model: the provider's display name, or the id with brand casing (mimo-v2.6-pro → MiMo v2.6 Pro). */
const niceName = m => m.label && m.label !== m.id ? m.label
    : m.id.split('/').pop().split(/[-_]/).filter(Boolean).map(t => BRANDS[t.toLowerCase()] || (/^[a-z]{2,}$/.test(t) ? t[0].toUpperCase() + t.slice(1) : t)).join(' ');
const field = (text, control, hint) => h('label', { class: 'field' }, h('span', { class: 'field-name' }, text), control, hint ? h('small', { class: 'hint' }, hint) : null);
const select = (options, value, props = {}) => h('select', { class: 'input', ...props }, options.map(([v, t, off]) => h('option', { value: v, selected: String(v) === String(value ?? ''), disabled: off }, t)));
function toggle(checked, onchange, text) {
    const input = h('input', { type: 'checkbox', checked, onchange: event => onchange(event.target.checked, event.target) });
    return h('label', { class: 'switch' }, input, h('i'), text ? h('span', null, text) : null);
}
/** Levels a model accepts on a connection, or [] when it cannot be adjusted there. */
function levelsOf(name, model) {
    const profile = profileOf(name), entry = catalogOf(name).find(m => m.id === (model || profile?.model));
    if (!profile || entry?.no_effort) return [];
    if (profile.kind === 'api' && profile.protocol === 'openai-chat' && profile.reasoning_parameter !== 'reasoning_effort') return [];
    return entry?.effort_values || (profile.kind === 'cli' ? profile.effort_values || [] : []);
}
/** 默认 | 低 | 中 | 高 | … — only the levels this model declares. */
function effortPicker(levels, value, onchange, fallback) {
    if (!levels.length) return h('div', { class: 'effort none' }, '这个模型在此连接上没有可调的推理强度');
    const draw = () => put(box, ...[['', '默认'], ...levels.map(l => [l, effortName(l)])].map(([v, t]) =>
        h('button', { type: 'button', class: v === (value || '') ? 'active' : '', title: v ? `调用时传递：${v}` : (fallback ? `使用默认档位（${effortName(fallback)}）` : '使用默认档位'),
            onclick: () => { value = v; draw(); onchange(v); } }, t)));
    const box = h('div', { class: 'seg effort', role: 'group', 'aria-label': '推理强度' });
    draw();
    return box;
}
function avatar(profile, cls = '') {
    const text = profile?.kind === 'cli' ? '>_' : (profile?.label || '?').replace(/[^A-Za-z0-9一-鿿]/g, '').slice(0, 1).toUpperCase() || '?';
    let hash = 0; for (const c of profile?.name || '') hash = (hash * 31 + c.charCodeAt(0)) % 360;
    return h('span', { class: 'pv ' + cls + (profile?.kind === 'cli' ? ' cli' : ''), style: { '--pv': `hsl(${hash} 46% 52%)` }, 'aria-hidden': 'true' }, text);
}
function connStatus(p) {
    if (!p) return ['warn', '连接不存在'];
    if (!p.enabled) return ['off', '已停用'];
    if (p.available) return ['ok', '可用'];
    return p.kind === 'api' && !p.key_present ? ['warn', '缺少密钥'] : ['warn', '不可用'];
}
function partStatus(p) {
    if (p.retired) return ['off', '已移除'];
    if (!p.connection) return ['warn', '未选择连接'];
    if (p.connection === 'legacy') return ['warn', '未选择连接'];
    if (p.connection === 'mock') return ['ok', '测试用'];
    const [kind, text] = connStatus(profileOf(p.connection));
    if (kind !== 'ok') return [kind, '连接' + text];
    return p.hidden ? ['off', '不在选择器中'] : ['ok', '可用'];
}
const dot = ([kind, text]) => h('span', { class: 'dot ' + kind, title: text });
const pill = ([kind, text]) => h('span', { class: 'state ' + kind }, h('i'), text);

/** Declared → tested → used in real chats. A model in a list is not a verified model. */
function proof(entry) {
    const chips = [];
    if (entry.test) chips.push(h('span', { class: 'cap ' + (entry.test.ok ? 'yes' : 'warn'), title: entry.test.ok ? `测试调用成功 · ${fmtTime(entry.test.at)} · 服务端报告模型 ${entry.test.reported_model}` : `测试失败 · ${fmtTime(entry.test.at)}：${friendlyReason(entry.test.reason || '')}` },
        entry.test.ok ? '测试通过' : '测试失败'));
    const used = entry.production;
    if (used?.completed) chips.push(h('span', { class: 'cap yes', title: `聊天中成功调用 ${used.completed} 次，最近 ${fmtTime(used.last_success_at)}${used.actual_model ? ' · 服务端报告模型 ' + used.actual_model : ''}`
        + `${used.image_verified_at ? ' · 图片输入已实际使用' : ''}${used.tools_verified_at ? ' · 工具调用已实际使用' : ''}` }, `已使用 ${used.completed} 次`));
    else if (used?.failed) chips.push(h('span', { class: 'cap warn' }, `调用失败 ${used.failed} 次`));
    if (!chips.length) chips.push(h('span', { class: 'cap', title: '已加入目录，还没有实际调用过。可以点“测试”发一次很小的请求确认。' }, '未验证'));
    return chips;
}
const facts = (entry, api_kind) => [
    entry.window_tokens ? h('span', { class: 'fact', title: `上下文窗口 ${fmtNum(entry.window_tokens)} Token · ${WINDOW_STATUS[entry.window_status] || '未核实'}${entry.window_source ? ' · ' + entry.window_source : ''}` },
        windowText(entry) + (entry.window_status === 'UNKNOWN' ? '（假设）' : '')) : null,
    api_kind && entry.images ? h('span', { class: 'fact' }, '图片') : null,
    api_kind && entry.tools ? h('span', { class: 'fact', title: '可以自行检索网页与文献、读取附件其余部分和历史消息' }, '工具') : null,
    entry.effort_values?.length ? h('span', { class: 'fact', title: '可选的推理强度：' + entry.effort_values.join(' / ') }, entry.effort_values.map(effortName).join(' · ')) : null,
];

async function runTest(name, model) {
    const value = await act(`/api/chat/profiles/${encodeURIComponent(name)}/test`, model ? { model } : {});
    return value.test.ok ? { ok: true, text: `调用成功：${value.test.seconds} 秒内返回（服务端报告模型 ${value.test.reported_model}）。` }
        : { ok: false, text: '调用失败：' + friendlyReason(value.test.reason || '') };
}

/** Create a participant for one model; the name is the model's, made unique if needed. Returns the new id. */
async function addSpeaker(connection, model, wanted) {
    const taken = new Set(people().filter(p => !p.retired).map(p => p.label));
    let name = (wanted || niceName(catalogOf(connection).find(m => m.id === model) || { id: model })).trim().slice(0, 40), n = 2;
    const base = name.slice(0, 36);
    while (taken.has(name)) name = `${base} ${n++}`;
    const value = await act('/api/chat/participants', { label: name, slug: model, connection, model });
    return value.id;
}

// ---------------------------------------------------------------------------------------------------------------------
// Dialog: edit one catalogue entry
function modelDialog(backend, entry, done) {
    const api_kind = profileOf(backend)?.kind === 'api';
    const id = h('input', { class: 'input', value: entry?.id || '', placeholder: '服务商文档里的模型 ID，例如 qwen-max', disabled: Boolean(entry?.id), maxLength: 200 });
    const name = h('input', { class: 'input', value: entry?.label && entry.label !== entry.id ? entry.label : '', placeholder: '可选，例如 Qwen Max', maxLength: 80 });
    const size = h('input', { class: 'input', type: 'number', min: 4096, max: 2000000, value: entry?.window_tokens || '', placeholder: api_kind ? '留空则先按 128K 处理' : '留空则沿用订阅默认模型的窗口' });
    const status = select(Object.entries(WINDOW_STATUS), entry?.window_status || 'UNKNOWN');
    const images = h('input', { type: 'checkbox', checked: Boolean(entry?.images) }), tools = h('input', { type: 'checkbox', checked: Boolean(entry?.tools) });
    const efforts = h('input', { class: 'input', value: (entry?.effort_values || []).join(', '), placeholder: '例如 low, medium, high；留空表示不可调' });
    const dialog = modal({ title: entry?.id ? `编辑模型 · ${entry.label || entry.id}` : '手动添加模型', wide: true,
        body: [h('div', { class: 'form-grid' }, field('模型 ID', id, entry?.id ? '模型 ID 不能修改；需要换 ID 时请添加一个新模型。' : '必须与服务商的写法完全一致。'), field('显示名称', name)),
            api_kind ? h('div', { class: 'checks' }, h('label', { class: 'radio' }, images, '支持图片输入'), h('label', { class: 'radio' }, tools, '支持工具调用', h('small', null, '自行检索、读附件与历史'))) : null,
            h('details', { class: 'adv', open: Boolean(entry?.id) }, h('summary', null, '规格'),
                h('div', { class: 'form-grid' }, field('上下文窗口（Token）', size), field('这个数字的依据', status)),
                field('可选的推理强度', efforts, '填写服务商接受的写法（英文）；界面会显示为 低 / 中 / 高 等。是否真的被接受，以测试或实际调用为准。')),
            h('p', { class: 'hint' }, '这里填写的都是“声明”。保存后可以点“测试”发一次很小的真实请求来确认。')],
        actions: [{ label: '保存', primary: true, action: async () => {
            if (!id.value.trim()) throw new Error('请填写模型 ID。');
            await act('/api/chat/catalog/' + encodeURIComponent(backend), { id: id.value.trim(), label: name.value.trim() || id.value.trim(),
                ...(size.value.trim() ? { window_tokens: Number(size.value), window_status: status.value } : {}),
                ...(api_kind ? { images: images.checked, tools: tools.checked } : {}),
                effort_values: efforts.value.split(/[,，\s]+/).filter(Boolean), ...(entry?.id ? {} : { source: 'added by hand' }) });
            done?.(id.value.trim());
        } }] });
    (entry?.id ? name : id).focus();
    return dialog;
}

// Dialog: what the provider / CLI offers, add or remove with one click
function manageModels(profile, done) {
    const list = h('div', { class: 'pick-list' }), note = h('p', { class: 'hint' }, '正在读取模型列表…');
    const search = h('input', { class: 'input', type: 'search', placeholder: '搜索模型', oninput: () => draw() });
    let rows = [];
    const draw = () => {
        const q = search.value.trim().toLowerCase(), known = new Set(catalogOf(profile.name).map(m => m.id));
        const shown = rows.filter(m => !q || (m.id + ' ' + (m.label || '')).toLowerCase().includes(q));
        put(list, ...shown.map(m => {
            const added = known.has(m.id), current = profileOf(profile.name)?.model === m.id, inUse = speakersOf(profile.name, m.id).length;
            return h('div', { class: 'pick-row' + (added ? ' added' : '') },
                h('div', { class: 'pick-main' }, h('strong', null, m.label || m.id), m.is_default ? h('span', { class: 'cap' }, 'CLI 默认') : null, m.label && m.label !== m.id ? h('code', null, m.id) : null,
                    h('div', { class: 'pick-facts' }, [m.description, m.window_tokens ? '窗口 ' + fmtNum(m.window_tokens) : null, m.images === true ? '图片' : null,
                        m.effort_values?.length ? '推理强度 ' + m.effort_values.map(effortName).join(' / ') : m.no_effort ? '推理强度不可调' : null].filter(Boolean).join(' · '))),
                added ? h('button', { class: 'pick-btn on', disabled: current || inUse > 0, title: current ? '这是连接的默认模型' : inUse ? '有参与者正在使用' : '从目录移除',
                        onclick: async event => { event.currentTarget.disabled = true; try { await act(`/api/chat/catalog/${encodeURIComponent(profile.name)}/remove`, { id: m.id }); } catch (e) { toast(e.message, 'error'); } draw(); } }, icon(ICONS.check, 14), '已添加')
                    : h('button', { class: 'pick-btn', onclick: async event => { event.currentTarget.disabled = true; try { await act('/api/chat/catalog/' + encodeURIComponent(profile.name), fromListing(m)); } catch (e) { toast(e.message, 'error'); } draw(); } }, icon(ICONS.plus, 14), '添加'));
        }), !shown.length && rows.length ? h('p', { class: 'hint' }, '没有匹配的模型。') : null);
    };
    modal({ title: `${profile.label} · 可用模型`, wide: true, onclose: done,
        body: [search, note, list, h('div', { class: 'card-actions' }, h('button', { class: 'btn small', onclick: () => modelDialog(profile.name, null, () => draw()) }, '列表里没有？手动添加模型 ID'))] }).el.classList.add('pick-modal');
    api(`/api/chat/connections/${encodeURIComponent(profile.name)}/discover`, {}).then(value => {
        rows = value.models || [];
        note.textContent = value.ok ? `${value.source} 返回了 ${rows.length} 个模型。出现在列表里只说明可以选择，不代表已经调用验证。${value.note || ''}` : (value.reason || '没有读到模型列表。');
        note.classList.toggle('warn-text', !value.ok);
        draw(); search.focus();
    }).catch(e => { note.textContent = e.message; note.classList.add('warn-text'); });
}
const fromListing = m => ({ id: m.id, label: m.label || m.id, source: 'provider model list', ...(m.description ? { description: m.description } : {}),
    ...(m.window_tokens ? { window_tokens: m.window_tokens, window_status: 'PROVIDER-REPORTED', window_source: '服务商模型列表返回' } : {}),
    ...(typeof m.images === 'boolean' ? { images: m.images } : {}), ...(m.effort_values?.length ? { effort_values: m.effort_values } : {}),
    ...(m.default_effort ? { default_effort: m.default_effort } : {}), ...(m.no_effort ? { no_effort: true } : {}) });

// ---------------------------------------------------------------------------------------------------------------------
// "添加模型": provider → key → models → into the chat, in one pass
function addWizard(done, start) {
    const body = h('div', { class: 'wiz' });
    const dialog = modal({ title: '添加模型', wide: true, body });
    dialog.el.classList.add('wiz-modal');
    const steps = h('ol', { class: 'wiz-steps' }, ['选择服务商', '连接', '选择模型'].map(t => h('li', null, t)));
    const pane = h('div', { class: 'wiz-pane' }), error = h('p', { class: 'form-error', role: 'alert', hidden: true });
    body.append(steps, error, pane);
    const fail = text => { error.textContent = text || ''; error.hidden = !text; if (text) error.scrollIntoView({ block: 'nearest' }); };
    const at = n => { steps.querySelectorAll('li').forEach((li, i) => { li.classList.toggle('now', i === n); li.classList.toggle('done', i < n); }); fail(''); };

    function chooseProvider() {
        at(0);
        const have = overview().profiles;
        put(pane, 
            have.length ? h('h4', null, '已有的连接') : null,
            h('div', { class: 'tiles' }, have.map(p => h('button', { class: 'tile-btn', onclick: () => chooseModels({ existing: p }) }, avatar(p), h('span', null, h('strong', null, p.label), h('small', null, p.kind === 'cli' ? '本机订阅' : `${catalogOf(p.name).length} 个模型`))))),
            h('h4', null, '新的服务商'),
            h('div', { class: 'tiles' }, PRESETS.map(preset => h('button', { class: 'tile-btn', onclick: () => connect(preset) },
                avatar({ name: preset.id, label: preset.name, kind: 'api' }), h('span', null, h('strong', null, preset.name), h('small', null, preset.sub || (preset.protocol ? PROTOCOL[preset.protocol] : 'OpenAI 兼容')))))));
    }

    function connect(preset) {
        at(1);
        const taken = new Set(overview().profiles.map(p => p.label));
        let suggested = preset.id === 'custom' ? '' : preset.name, n = 2;
        while (taken.has(suggested) && suggested) suggested = `${preset.name} ${n++}`;
        const name = h('input', { class: 'input', value: suggested, maxLength: 40, placeholder: '例如 我的中转站' });
        const key = h('input', { class: 'input', type: 'password', autocomplete: 'off', placeholder: '粘贴 API Key' });
        const url = h('input', { class: 'input', value: preset.url, placeholder: 'https://…/v1' });
        const protocol = select(Object.entries(PROTOCOL), preset.protocol || 'openai-chat');
        const go = actionButton({ label: '连接并获取模型', primary: true, action: async () => {
            fail('');
            for (const el of [name, key, url]) el.classList.remove('invalid');
            const bad = (el, text) => { el.classList.add('invalid'); el.focus(); throw new Error(text); };
            if (!name.value.trim()) bad(name, '给这个连接起个名字。');
            if (!/^(https:\/\/|http:\/\/127\.0\.0\.1[:/])[^\s]+/.test(url.value.trim())) { advanced.open = true; bad(url, '服务地址需要以 https:// 开头。'); }
            if (key.value.trim().length < 8) bad(key, '请粘贴 API Key。');
            const draft = { label: name.value.trim(), preset: preset.id, protocol: protocol.value, base_url: url.value.trim().replace(/\/+$/, ''), key: key.value.trim(), reasoning: Boolean(preset.reasoning) };
            const value = await api('/api/chat/connections/probe', { protocol: draft.protocol, base_url: draft.base_url, key: draft.key });
            if (value.auth === false) bad(key, value.reason);
            chooseModels({ draft, listing: value });
            return false;
        } }, null, fail);
        const advanced = h('details', { class: 'adv', open: preset.id === 'custom' }, h('summary', null, '高级：服务地址与协议'),
            h('div', { class: 'form-grid' }, field('服务地址（Base URL）', url, '请求会发往这个地址；使用第三方中转时，聊天内容会经过中转方。'), field('协议', protocol)));
        put(pane, h('div', { class: 'wiz-form' }, h('div', { class: 'wiz-who' }, avatar({ name: preset.id, label: preset.name, kind: 'api' }, 'lg'), h('div', null, h('strong', null, preset.name), h('small', null, preset.url || '自定义地址'))),
            field('名称', name), field('API Key', key, '只保存到本机的私有目录，页面不会回显，也不进入聊天记录和日志。'), advanced),
            h('div', { class: 'wiz-foot' }, h('button', { class: 'btn ghost', onclick: chooseProvider }, '← 上一步'), h('span', { class: 'grow' }), go));
        (suggested ? key : name).focus();
        key.addEventListener('keydown', event => { if (event.key === 'Enter') go.click(); });
    }

    async function chooseModels({ existing, draft, listing }) {
        at(2);
        put(pane, h('p', { class: 'hint' }, '正在读取模型列表…'));
        if (existing) {
            try { listing = await api(`/api/chat/connections/${encodeURIComponent(existing.name)}/discover`, {}); }
            catch (e) { listing = { models: [], ok: false, reason: e.message }; }
        }
        const known = existing ? catalogOf(existing.name) : [];
        const rows = [...(listing.models || [])];
        for (const m of known) if (!rows.some(r => r.id === m.id)) rows.unshift({ ...m, added: true });
        const picked = new Map();   // id -> {join}
        const search = h('input', { class: 'input', type: 'search', placeholder: '搜索模型', oninput: () => draw() });
        const manual = h('input', { class: 'input', placeholder: '模型 ID，例如 qwen-max', maxLength: 200 });
        const list = h('div', { class: 'pick-list' }), summary = h('span', { class: 'hint' });
        const addManual = () => {
            const id = manual.value.trim();
            if (!/^[A-Za-z0-9][A-Za-z0-9._:\/-]{0,199}$/.test(id)) { fail('模型 ID 只能包含字母、数字和 . _ : / -'); return; }
            if (!rows.some(r => r.id === id)) rows.unshift({ id, label: id, manual: true });
            picked.set(id, { join: true, name: niceName({ id }) }); manual.value = ''; fail(''); draw();
        };
        manual.addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); addManual(); } });
        const draw = () => {
            const q = search.value.trim().toLowerCase();
            const shown = rows.filter(m => !q || (m.id + ' ' + (m.label || '')).toLowerCase().includes(q));
            put(list, ...shown.map(m => {
                const state = picked.get(m.id), speaking = existing ? speakersOf(existing.name, m.id) : [];
                const on = Boolean(state);
                return h('div', { class: 'pick-row' + (on ? ' picked' : '') },
                    h('label', { class: 'pick-main' }, h('input', { type: 'checkbox', checked: on, onchange: event => { event.target.checked ? picked.set(m.id, { join: !speaking.length, name: niceName(m) }) : picked.delete(m.id); draw(); } }),
                        h('span', null, h('strong', null, m.label || m.id), m.added ? h('span', { class: 'cap' }, '已在目录') : null, m.manual ? h('span', { class: 'cap' }, '手动') : null,
                            speaking.length ? h('span', { class: 'cap yes' }, '参与者：' + speaking.map(p => p.label).join('、')) : null, m.label && m.label !== m.id ? h('code', null, m.id) : null,
                            h('div', { class: 'pick-facts' }, [m.description, m.window_tokens ? '窗口 ' + fmtNum(m.window_tokens) : null, m.images === true ? '图片' : null,
                                m.effort_values?.length ? '推理强度 ' + m.effort_values.map(effortName).join(' / ') : null].filter(Boolean).join(' · ')))),
                    on && state.join ? h('input', { class: 'input small pick-name', value: state.name, maxLength: 40, title: '在聊天里显示的名字，之后可以再改', 'aria-label': '参与者名称', oninput: event => { state.name = event.target.value; } }) : null,
                    on ? toggle(state.join, value => { state.join = value; draw(); }, '加入聊天') : null);
            }), !shown.length ? h('p', { class: 'hint' }, rows.length ? '没有匹配的模型。' : '还没有可选的模型，请在下方手动填写模型 ID。') : null);
            const join = [...picked.values()].filter(v => v.join).length;
            summary.textContent = picked.size ? `已选 ${picked.size} 个模型${join ? `，其中 ${join} 个会作为参与者出现在聊天输入区` : ''}` : '勾选要使用的模型';
            finish.disabled = !picked.size;
        };
        const finish = actionButton({ label: '完成', primary: true, action: async () => {
            fail('');
            const chosen = rows.filter(m => picked.has(m.id));
            let name = existing?.name;
            if (!existing) {
                const made = await api('/api/chat/profiles', { label: draft.label, preset: draft.preset, settings: { protocol: draft.protocol, base_url: draft.base_url, model: chosen[0].id,
                    ...(draft.reasoning && draft.protocol === 'openai-chat' ? { reasoning_parameter: 'reasoning_effort' } : {}) } });
                adoptModels(made);
                name = made.saved;
                await act(`/api/chat/profiles/${encodeURIComponent(name)}/key`, { key: draft.key });
                draft.key = '';
            }
            // A new connection's first model was entered bare when the connection was created: send it again with its listing details.
            const fresh = existing ? chosen.filter(m => !catalogOf(name).some(k => k.id === m.id)) : chosen;
            if (fresh.length) await act('/api/chat/catalog/' + encodeURIComponent(name), { models: fresh.map(m => m.manual ? { id: m.id, label: m.id, source: 'added by hand' } : fromListing(m)) });
            let first = null;
            for (const m of chosen) {
                if (!picked.get(m.id).join || speakersOf(name, m.id).some(p => p.model === m.id)) continue;
                const id = await addSpeaker(name, m.id, picked.get(m.id).name);
                first ||= id;
            }
            toast(first ? `已添加。现在可以在聊天输入区选择 ${label(first)} 了。` : '模型已加入目录。');
            done?.(first ? { type: 'p', id: first } : { type: 'c', id: name });
        } }, () => dialog.close(), fail);
        put(pane, 
            h('p', { class: 'hint' + (listing.ok ? '' : ' warn-text') }, listing.ok ? `${listing.source || '服务商'} 返回了 ${listing.models.length} 个模型。勾选要用的；“加入聊天”打开后，它会成为一位独立的参与者。`
                : (listing.reason || '没有读到模型列表。') + ' 可以直接填写模型 ID。'),
            rows.length > 6 ? search : null, list,
            h('div', { class: 'inline manual-row' }, manual, h('button', { class: 'btn', onclick: addManual }, '添加这个 ID')),
            h('div', { class: 'wiz-foot' }, h('button', { class: 'btn ghost', onclick: () => existing ? chooseProvider() : connect(PRESETS.find(p => p.id === draft.preset)) }, '← 上一步'), summary, h('span', { class: 'grow' }), finish));
        draw();
    }
    if (start?.existing) chooseModels({ existing: start.existing }); else chooseProvider();
}

// ---------------------------------------------------------------------------------------------------------------------
// Settings → 模型与服务: list on the left, one item's detail on the right
const view = { sel: null, query: '' };

function participantDetail(p, rerender) {
    const draft = { label: p.label, connection: p.connection || '', model: p.model || '', effort: p.effort || '', hidden: p.hidden };
    const notice = h('div', { class: 'mm-notice', hidden: true });
    const say = (text, kind = 'error') => { notice.textContent = text || ''; notice.className = 'mm-notice ' + kind; notice.hidden = !text; if (text) notice.scrollIntoView({ block: 'nearest' }); };
    const save = h('button', { class: 'btn primary', disabled: true }, '保存');
    const dirty = () => { save.disabled = false; say(''); };
    const name = h('input', { class: 'title-input', value: p.label, maxLength: 40, 'aria-label': '参与者名称', oninput: () => { draft.label = name.value; dirty(); } });
    const modelBox = h('div'), effortBox = h('div'), infoBox = h('div', { class: 'facts' });
    const connections = overview().profiles;
    const conn = select([...(draft.connection ? [] : [['', '选择连接…']]), ...connections.map(c => [c.name, connectionName(c.name) + (connStatus(c)[0] === 'ok' ? '' : `（${connStatus(c)[1]}）`)]),
        ...(p.connection && !connections.some(c => c.name === p.connection) ? [[p.connection, connectionName(p.connection)]] : [])], draft.connection);
    const fill = () => {
        const profile = profileOf(draft.connection), list = catalogOf(draft.connection);
        if (!profile) { put(modelBox, h('p', { class: 'hint' }, draft.connection ? '这种方式不支持在这里选择模型。' : '先选择一个连接。')); put(effortBox, ); put(infoBox, ); return; }
        const pick = select([...(p.model && draft.connection === p.connection ? [] : [['', `跟随连接默认 · ${modelName(profile.name, profile.model)}`]]),
            ...list.map(m => [m.id, m.label && m.label !== m.id ? m.label : m.id]), ['+', '＋ 获取或添加其他模型…']], draft.model);
        pick.addEventListener('change', () => {
            if (pick.value === '+') { pick.value = draft.model; manageModels(profile, () => { fill(); }); return; }
            draft.model = pick.value;
            if (!levelsOf(draft.connection, draft.model).includes(draft.effort)) draft.effort = '';
            dirty(); fill();
        });
        const levels = levelsOf(draft.connection, draft.model), entry = list.find(m => m.id === (draft.model || profile.model));
        put(modelBox, pick);
        put(effortBox, effortPicker(levels, draft.effort, value => { draft.effort = value; dirty(); }, entry?.default_effort || profile.effort),
            !levels.length && profile.kind === 'api' && profile.protocol === 'openai-chat' && profile.reasoning_parameter !== 'reasoning_effort' && entry?.effort_values?.length
                ? h('p', { class: 'hint' }, '这个连接没有开启“推理强度参数”。如果服务商支持 reasoning_effort，可在连接的高级设置里开启。') : null);
        put(infoBox, ...(entry ? [h('code', null, entry.id), ...facts(entry, profile.kind === 'api'), ...proof(entry)] : []));
    };
    conn.addEventListener('change', () => { draft.connection = conn.value; draft.model = ''; draft.effort = ''; dirty(); fill(); });
    fill();
    const result = h('div', { class: 'test-result', hidden: true });
    const test = actionButton({ label: '测试这个模型', action: async () => {
        if (!profileOf(draft.connection)) throw new Error('先选择连接。');
        result.hidden = false; result.className = 'test-result'; result.textContent = '正在发送一次很小的请求…';
        const out = await runTest(draft.connection, draft.model || undefined);
        result.className = 'test-result ' + (out.ok ? 'ok' : 'bad'); result.textContent = out.text; fill(); return false;
    } }, null, say);
    save.addEventListener('click', async () => {
        save.disabled = true;
        try {
            if (!draft.label.trim()) { name.classList.add('invalid'); name.focus(); throw new Error('名字不能为空。'); }
            await act('/api/chat/participants', { id: p.id, label: draft.label.trim(), connection: draft.connection || null, model: draft.model || null, effort: draft.effort || null, hidden: draft.hidden });
            toast(`${draft.label.trim()} 的设置已保存`); rerender();
        } catch (e) { save.disabled = false; say(e.message); }
    });
    const used = S.rooms.filter(r => !r.archived && r.model_settings?.[p.id] && Object.keys(r.model_settings[p.id]).some(k => k !== 'backend')).length;
    if (p.retired) {
        return [h('div', { class: 'mm-scroll' }, h('header', { class: 'mm-head' }, sigil(p.id, 'lg'), h('div', { class: 'mm-title' }, h('h3', null, p.label), pill(partStatus(p)))),
            h('p', { class: 'hint' }, '这位参与者已从聊天中移除。它说过的话仍然保留原来的名字和标记。'), notice,
            h('div', { class: 'card-actions' }, actionButton({ label: '恢复为参与者', primary: true, action: async () => { await act('/api/chat/participants', { id: p.id, retired: false, hidden: false }); rerender(); return false; } }, null, say)))];
    }
    return [h('div', { class: 'mm-scroll' },
        h('header', { class: 'mm-head', style: { '--hue': color(p.id) } }, sigil(p.id, 'lg'), h('div', { class: 'mm-title' }, name, pill(partStatus(p)))),
        notice,
        h('section', { class: 'mm-sec' }, h('h4', null, '使用的模型'),
            h('div', { class: 'form-grid two' }, field('连接', conn), field('模型', modelBox)),
            field('推理强度', effortBox), infoBox,
            h('div', { class: 'card-actions' }, test), result),
        h('section', { class: 'mm-sec' }, h('h4', null, '在聊天中'),
            h('div', { class: 'set-row' }, h('div', { class: 'set-label' }, h('span', null, '显示在输入区的回答者里'), h('small', null, '关闭后不能再被点名，已有的消息不受影响。')),
                toggle(!draft.hidden, value => { draft.hidden = !value; dirty(); })),
            h('p', { class: 'hint' }, `这里是 ${p.label} 的默认设置，对所有聊天生效。某个聊天可以在 房间 → 模型 里单独改，一次提问也可以临时改${used ? `；目前有 ${used} 个聊天做了单独设置` : ''}。`)),
        h('details', { class: 'adv' }, h('summary', null, '高级'),
            h('dl', { class: 'kv' }, h('dt', null, '参与者 ID'), h('dd', null, h('code', null, p.id), ' 固定不变，历史消息按它归属作者'),
                h('dt', null, '来源'), h('dd', null, p.builtin ? '内置参与者' : '添加于 ' + (p.created_at ? fmtTime(p.created_at) : '—'))),
            p.builtin ? h('p', { class: 'hint' }, '内置参与者不能移除；不需要时可以关闭“显示在输入区的回答者里”。')
                : h('div', { class: 'card-actions' }, h('button', { class: 'btn danger', onclick: () => confirmDialog(`移除参与者 ${p.label}`, '它会从输入区和所有聊天的参与者里消失，正在进行的回复会被停止。它已经说过的话保留不变，仍显示原来的名字。连接和模型目录不受影响。', '移除',
                    async () => { const value = await act('/api/chat/participants/' + encodeURIComponent(p.id), undefined, 'DELETE'); view.sel = value.kept_for_history ? view.sel : null; toast(value.kept_for_history ? '已移除；历史消息保留它的名字' : '已移除'); rerender(); }, true) }, '移除参与者')))),
    h('footer', { class: 'mm-foot' }, h('span', { class: 'hint' }, '更改保存后立即生效，不影响已有消息。'), h('span', { class: 'grow' }), save)];
}

function modelRows(profile, rerender, say) {
    const api_kind = profile.kind === 'api';
    return catalogOf(profile.name).map(entry => {
        const speaking = speakersOf(profile.name, entry.id);
        const result = h('div', { class: 'test-result', hidden: true });
        const more = event => menu(Object.assign(event.currentTarget, { alignRight: true }), [
            { label: '编辑名称与规格…', icon: 'edit', action: () => modelDialog(profile.name, entry, rerender) },
            { label: '测试', icon: 'check', action: async () => {
                result.hidden = false; result.className = 'test-result'; result.textContent = '正在发送一次很小的请求…';
                try { const out = await runTest(profile.name, entry.id); result.className = 'test-result ' + (out.ok ? 'ok' : 'bad'); result.textContent = out.text; }
                catch (e) { result.className = 'test-result bad'; result.textContent = e.message; } } },
            entry.current ? null : { label: '设为这个连接的默认模型', icon: 'pin', action: async () => {
                try { await act(api_kind ? '/api/chat/profiles' : '/api/chat/cli/' + profile.name, api_kind ? { name: profile.name, settings: { model: entry.id } } : { model: entry.id }); rerender(); } catch (e) { say(e.message); } } },
            'sep',
            { label: '从目录移除', icon: 'close', danger: true, disabled: entry.current || speaking.length > 0, hint: entry.current ? '这是连接的默认模型' : speaking.length ? '有参与者正在使用' : '',
                action: async () => { try { await act(`/api/chat/catalog/${encodeURIComponent(profile.name)}/remove`, { id: entry.id }); rerender(); } catch (e) { say(e.message); } } },
        ]);
        return h('div', { class: 'model-row' },
            h('div', { class: 'model-main' }, h('div', { class: 'model-name' }, h('strong', null, entry.label || entry.id), entry.current ? h('span', { class: 'cap' }, '连接默认') : null, entry.label !== entry.id ? h('code', null, entry.id) : null),
                h('div', { class: 'facts' }, facts(entry, api_kind), proof(entry)), result),
            h('div', { class: 'model-side' },
                speaking.length ? speaking.map(p => h('button', { class: 'who-link', style: { '--hue': color(p.id) }, title: '查看这位参与者', onclick: () => { view.sel = { type: 'p', id: p.id }; rerender(); } }, sigil(p.id, 'xs'), p.label))
                    : h('button', { class: 'btn small', title: '让这个模型成为一位独立的聊天参与者', onclick: async event => {
                        event.currentTarget.disabled = true;
                        try { const id = await addSpeaker(profile.name, entry.id); toast(`已加入聊天：${label(id)}`); rerender(); } catch (e) { say(e.message); rerender(); } } }, icon(ICONS.plus, 13), '加入聊天'),
                h('button', { class: 'icon-btn tiny', 'aria-label': '更多', onclick: more }, icon(ICONS.more, 15))));
    });
}

function connectionDetail(profile, rerender) {
    const api_kind = profile.kind === 'api', status = connStatus(profile);
    const notice = h('div', { class: 'mm-notice', hidden: true });
    const say = (text, kind = 'error') => { notice.textContent = text || ''; notice.className = 'mm-notice ' + kind; notice.hidden = !text; if (text) notice.scrollIntoView({ block: 'nearest' }); };
    const save = h('button', { class: 'btn primary', disabled: true }, '保存');
    const dirty = () => { save.disabled = false; say(''); };
    const name = h('input', { class: 'title-input', value: profile.label, maxLength: 40, 'aria-label': '连接名称', oninput: dirty });
    const enabled = toggle(profile.enabled, async (value, input) => {
        try {
            await act(api_kind ? '/api/chat/profiles' : '/api/chat/cli/' + profile.name, api_kind ? { name: profile.name, settings: { enabled: value } } : { enabled: value });
            toast(value ? `${profile.label} 已启用` : `${profile.label} 已停用，使用它的参与者暂时不能回答`); rerender();
        } catch (e) { input.checked = !value; say(e.message); }
    }, profile.enabled ? '已启用' : '已停用');
    const speaking = speakersOf(profile.name);
    const f = {}, adv = h('details', { class: 'adv' }, h('summary', null, '高级设置'));
    const sections = [];
    const result = h('div', { class: 'test-result', hidden: true });
    const test = actionButton({ label: '测试连接', action: async () => {
        result.hidden = false; result.className = 'test-result'; result.textContent = '正在用默认模型发送一次很小的请求…';
        const out = await runTest(profile.name); result.className = 'test-result ' + (out.ok ? 'ok' : 'bad'); result.textContent = out.text; return false;
    } }, null, text => { if (text) { result.hidden = false; result.className = 'test-result bad'; result.textContent = text; } });

    if (api_kind) {
        const env = profile.key_source?.startsWith('environment');
        const key = h('input', { class: 'input', type: 'password', autocomplete: 'off', placeholder: profile.key_present ? '已保存 · 粘贴新的密钥可替换' : '粘贴 API Key', 'aria-label': 'API Key' });
        const keySave = actionButton({ label: profile.key_present ? '替换' : '保存密钥', action: async () => {
            const value = key.value.trim();
            if (!value) { key.classList.add('invalid'); key.focus(); throw new Error('请先粘贴密钥。'); }
            key.value = '';
            await act(`/api/chat/profiles/${encodeURIComponent(profile.name)}/key`, { key: value }); toast('密钥已保存到本机私有目录'); rerender(); return false;
        } }, null, say);
        sections.push(h('section', { class: 'mm-sec' }, h('h4', null, 'API 密钥'),
            env ? h('p', { class: 'hint' }, '这个连接从环境变量读取密钥，请在启动环境里修改。') : h('div', { class: 'inline' }, key, keySave,
                profile.key_present ? h('button', { class: 'btn', onclick: () => confirmDialog('移除这个连接的密钥', '移除后这个连接立即不可用，正在进行的回复会被停止。密钥文件会从本机私有目录删除，无法恢复。', '移除密钥',
                    async () => { await act(`/api/chat/profiles/${encodeURIComponent(profile.name)}/key`, undefined, 'DELETE'); rerender(); }, true) }, '移除') : null),
            h('div', { class: 'card-actions' }, test, h('span', { class: 'hint' }, `请求发往 ${profile.host || '—'}`)), result));
        Object.assign(f, {
            base_url: h('input', { class: 'input', value: profile.base_url || '', placeholder: 'https://…/v1', oninput: dirty }),
            protocol: select(Object.entries(PROTOCOL), profile.protocol, { onchange: () => { dirty(); f.effortRow.hidden = f.protocol.value !== 'openai-chat'; } }),
            max_output_tokens: h('input', { class: 'input', type: 'number', min: 256, max: 32768, value: profile.max_output_tokens ?? 8192, oninput: dirty }),
            timeout: h('input', { class: 'input', type: 'number', min: 10, max: 300, value: profile.timeout ?? 120, oninput: dirty }),
            max_requests: h('input', { class: 'input', type: 'number', min: 1, placeholder: '不限', value: profile.max_requests ?? '', oninput: dirty }),
            effort_param: h('input', { type: 'checkbox', checked: profile.reasoning_parameter === 'reasoning_effort', onchange: dirty }),
            native_search: select([['', '自动判断'], ['off', '不使用（改用 Chattera 检索）'], ['deepseek', 'DeepSeek 服务端搜索'], ['web_search_tool', 'web_search 工具（MiMo 等）'], ['google', 'Google Search（原生协议）']], profile.native_search || '', { onchange: dirty }),
            google_auth: h('input', { type: 'checkbox', checked: profile.google_auth === 'bearer', onchange: dirty }),
        });
        f.effortRow = h('label', { class: 'radio', hidden: profile.protocol !== 'openai-chat' }, f.effort_param, '向服务商发送推理强度参数', h('small', null, 'reasoning_effort；仅在服务商支持时开启'));
        const u = profile.usage;
        adv.append(h('div', { class: 'form-grid two' }, field('服务地址（Base URL）', f.base_url), field('协议', f.protocol),
                field('单次输出上限（Token）', f.max_output_tokens, '太小会让长回答被截断。'), field('超时（秒）', f.timeout), field('累计请求上限', f.max_requests, '留空为不限。'),
                field('官方联网搜索', f.native_search, `当前生效：${SEARCH_KIND[profile.search_kind] || '没有官方搜索，独立检索时使用 Chattera 检索'}。服务商拒绝时会自动改用 Chattera 检索并记录原因。`)),
            f.effortRow, h('label', { class: 'radio' }, f.google_auth, '用 Bearer 方式发送密钥', h('small', null, '仅 Google 原生协议经中转站访问时需要')),
            h('dl', { class: 'kv' }, h('dt', null, '连接 ID'), h('dd', null, h('code', null, profile.name)), h('dt', null, '密钥位置'), h('dd', null, profile.key_source || '—'),
                u ? [h('dt', null, '本机累计'), h('dd', null, `请求 ${fmtNum(u.requests)} 次 · 输入 ${fmtNum(u.prompt_tokens)} / 输出 ${fmtNum(u.completion_tokens)} Token（服务商返回值；金额无法从这里得知）`)] : null));
    } else {
        f.timeout = h('input', { class: 'input', type: 'number', min: 10, max: 1800, value: profile.timeout ?? 120, oninput: dirty });
        f.native_search = select([['', '使用（独立检索时）'], ['off', '不使用（改用 Chattera 检索）']], profile.native_search === 'off' ? 'off' : '', { onchange: dirty });
        sections.push(h('section', { class: 'mm-sec' }, h('h4', null, '本机订阅'),
            h('p', { class: 'hint' }, `通过本机已登录的官方命令行调用，不使用 API Key，也不读取它的登录凭据。${profile.version ? '当前版本：' + profile.version + '。' : ''}`),
            h('div', { class: 'card-actions' }, test), result));
        adv.append(h('div', { class: 'form-grid two' }, field('超时（秒）', f.timeout, '联网搜索的回答需要更长时间。'), field('CLI 自带的联网搜索', f.native_search, name === undefined ? '' : `${profile.name === 'claude' ? 'WebSearch / WebFetch' : 'web_search'}，走订阅额度，不使用 API Key。`)),
            h('dl', { class: 'kv' }, h('dt', null, '程序'), h('dd', null, h('code', null, profile.executable || '—')), h('dt', null, '默认模型'), h('dd', null, modelName(profile.name, profile.model) + (profile.effort ? ' · ' + effortName(profile.effort) : ''))));
    }
    const models = h('section', { class: 'mm-sec' },
        h('div', { class: 'sec-head' }, h('h4', null, `模型 · ${catalogOf(profile.name).length}`), h('span', { class: 'grow' }),
            h('button', { class: 'btn small', onclick: () => manageModels(profile, rerender) }, icon(ICONS.down, 13), api_kind ? '获取模型列表' : '从 CLI 读取模型'),
            h('button', { class: 'btn small', onclick: () => modelDialog(profile.name, null, rerender) }, icon(ICONS.plus, 13), '手动添加')),
        h('div', { class: 'model-list' }, modelRows(profile, rerender, say)),
        h('p', { class: 'hint' }, '“加入聊天”会让一个模型成为独立的参与者，之后可以在输入区直接点名。同一个连接下的不同模型可以分别加入。'));
    save.addEventListener('click', async () => {
        save.disabled = true;
        for (const el of Object.values(f)) el.classList?.remove('invalid');
        try {
            if (!name.value.trim()) { name.classList.add('invalid'); name.focus(); throw new Error('名称不能为空。'); }
            if (api_kind) {
                const settings = { protocol: f.protocol.value, base_url: f.base_url.value.trim().replace(/\/+$/, ''), max_output_tokens: Number(f.max_output_tokens.value), timeout: Number(f.timeout.value),
                    max_requests: f.max_requests.value.trim() ? Number(f.max_requests.value) : null, reasoning_parameter: f.protocol.value === 'openai-chat' && f.effort_param.checked ? 'reasoning_effort' : null,
                    native_search: f.native_search.value || null, google_auth: f.protocol.value === 'google-generate-content' && f.google_auth.checked ? 'bearer' : null };
                await act('/api/chat/profiles', { name: profile.name, label: name.value.trim(), settings });
            } else await act('/api/chat/cli/' + profile.name, { label: name.value.trim(), timeout: Number(f.timeout.value), native_search: f.native_search.value || null });
            toast('已保存，立即生效'); rerender();
        } catch (e) {
            save.disabled = false;
            const where = /HTTPS|base URL|服务地址/.test(e.message) ? f.base_url : /output|输出/.test(e.message) ? f.max_output_tokens : /timeout|超时/i.test(e.message) ? f.timeout : null;
            if (where) { adv.open = true; where.classList.add('invalid'); where.focus(); }
            say(e.message);
        }
    });
    return [h('div', { class: 'mm-scroll' },
        h('header', { class: 'mm-head' }, avatar(profile, 'lg'), h('div', { class: 'mm-title' }, name, h('div', { class: 'mm-sub' }, pill(status), h('span', null, api_kind ? PROTOCOL[profile.protocol] || profile.protocol : '订阅 CLI'),
            speaking.length ? h('span', null, '参与者：' + speaking.map(p => p.label).join('、')) : h('span', null, '还没有参与者使用'),
            h('span', { title: '独立检索时这个连接使用的搜索方式' }, profile.search_kind ? '官方搜索 ✓' : 'Chattera 检索'))), enabled),
        notice, ...sections, models, adv,
        api_kind ? h('div', { class: 'card-actions end' },
            h('button', { class: 'btn', onclick: async () => { try { const made = await act(`/api/chat/profiles/${encodeURIComponent(profile.name)}/duplicate`, {});
                if (made.saved) view.sel = { type: 'c', id: made.saved }; toast('已复制。密钥不会复制，请为新连接单独填写。'); rerender(); } catch (e) { say(e.message); } } }, '复制连接'),
            h('button', { class: 'btn danger', onclick: () => confirmDialog(`移除连接 ${profile.label}`, '连接的设置和模型目录会被移除；密钥文件保留在私有目录里（可先点“移除”删除）。已有聊天记录不受影响。', '移除连接',
                async () => { try { await act('/api/chat/profiles/' + encodeURIComponent(profile.name), undefined, 'DELETE'); view.sel = null; rerender(); } catch (e) { say(e.message); throw e; } }, true) }, '移除连接')) : null),
    h('footer', { class: 'mm-foot' }, h('span', { class: 'hint' }, '名称与高级设置需要点保存；启用、密钥和模型的更改立即生效。'), h('span', { class: 'grow' }), save)];
}

export async function studioModels(box) {
    await Promise.all([loadModels(), loadBackends()]);
    box.classList.add('mm');
    const list = h('aside', { class: 'mm-list' }), detail = h('section', { class: 'mm-detail' });
    put(box, list, detail);
    const rerender = () => { draw(); };
    const open = sel => { view.sel = sel; box.classList.add('open'); draw(); };
    function draw() {
        const o = overview(), q = view.query.trim().toLowerCase();
        const match = (...texts) => !q || texts.join(' ').toLowerCase().includes(q);
        const active = people().filter(p => !p.retired), retired = people().filter(p => p.retired);
        if (!view.sel || !(view.sel.type === 'p' ? people().some(p => p.id === view.sel.id) : o.profiles.some(p => p.name === view.sel.id))) view.sel = active[0] ? { type: 'p', id: active[0].id } : null;
        const is = (type, id) => view.sel?.type === type && view.sel.id === id;
        const search = list.querySelector('input') || h('input', { class: 'input', type: 'search', placeholder: '搜索参与者、连接或模型', value: view.query, oninput: event => { view.query = event.target.value; draw(); } });
        const person = p => {
            const model = p.connection && p.connection !== 'legacy' ? modelName(p.connection, p.model || profileOf(p.connection)?.model) : '';
            return match(p.label, model, connectionName(p.connection || '')) ? h('button', { class: 'mm-item' + (is('p', p.id) ? ' active' : '') + (p.hidden || p.retired ? ' dim' : ''), style: { '--hue': color(p.id) }, onclick: () => open({ type: 'p', id: p.id }) },
                sigil(p.id), h('span', { class: 'mm-item-text' }, h('strong', null, p.label), h('small', null, [model, p.effort ? effortName(p.effort) : null].filter(Boolean).join(' · ') || connectionName(p.connection || '') || '未设置')), dot(partStatus(p))) : null;
        };
        put(list, h('div', { class: 'mm-top' },
            h('button', { class: 'btn primary mm-add', onclick: () => addWizard(sel => { if (sel) view.sel = sel; draw(); }) }, icon(ICONS.plus, 15), '添加模型'),
            search), h('div', { class: 'mm-items' },
            h('h5', null, '聊天参与者', h('span', null, active.length)), ...active.map(person),
            h('h5', null, '连接', h('span', null, o.profiles.length)),
            ...o.profiles.map(c => match(c.label, c.host || '', ...catalogOf(c.name).map(m => m.id + ' ' + (m.label || ''))) ? h('button', { class: 'mm-item' + (is('c', c.name) ? ' active' : '') + (c.enabled ? '' : ' dim'), onclick: () => open({ type: 'c', id: c.name }) },
                avatar(c), h('span', { class: 'mm-item-text' }, h('strong', null, c.label), h('small', null, `${catalogOf(c.name).length} 个模型 · ${c.kind === 'cli' ? '本机订阅' : c.host || 'API'}`)), dot(connStatus(c))) : null),
            retired.length ? h('details', { class: 'mm-retired' }, h('summary', null, `已移除的参与者 · ${retired.length}`), ...retired.map(person)) : null,
            h('p', { class: 'hint mm-where' }, '订阅 CLI 的内容发往对应的官方服务；API 连接的内容发往它的服务地址。')));
        if (!list.contains(document.activeElement) && view.query) search.focus();
        const back = h('button', { class: 'btn ghost mm-back', onclick: () => box.classList.remove('open') }, '← 返回列表');
        const p = view.sel?.type === 'p' ? people().find(x => x.id === view.sel.id) : null, c = view.sel?.type === 'c' ? profileOf(view.sel.id) : null;
        put(detail, back, ...(p ? participantDetail(p, rerender) : c ? connectionDetail(c, rerender) : [h('div', { class: 'mm-empty' }, h('p', null, '还没有参与者。'), h('p', { class: 'hint' }, '点“添加模型”，选择服务商、填入密钥、勾选模型，就可以在聊天里使用了。'))]));
    }
    draw();
}

// ---------------------------------------------------------------------------------------------------------------------
// Room panel → 模型: what each participant uses in this chat
export async function roomModels(room, box, rerender) {
    await Promise.all([loadModels(), Object.keys(S.backends).length ? null : loadBackends()]);
    const o = overview();
    const apply = async (id, backend, settings, text) => {
        try { await api(`/api/chat/rooms/${room.id}/backend`, { model: id, backend, settings }); await refreshRooms(); toast(text); }
        catch (e) { toast(e.message, 'error'); }
        rerender();
    };
    box.append(h('p', { class: 'hint' }, '每位参与者在这个聊天里使用的模型和推理强度。不改动时跟随参与者自己的设置；输入区的“本次模型设置”可以只为一次提问临时改。更换模型不会改变已有消息的作者。'));
    for (const id of IDS) {
        const own = participantOf(id), now = effectiveModel(room, id);
        const pinned = room.model_settings?.[id]?.backend === now.backend && (room.model_settings[id].model || room.model_settings[id].effort);
        const moved = own?.connection && now.backend !== own.connection;
        // Two separate choices: which model of the current connection, and (separately) which connection.
        const models = catalogOf(now.backend).map(m => [m.id, m.label || m.id]);
        if (now.model && !models.some(([v]) => v === now.model)) models.unshift([now.model, now.modelLabel || now.model]);
        const pick = select(models.length ? models : [['', now.backend === 'legacy' ? '先选择一个连接' : '这个连接还没有模型']], now.model || '', { 'aria-label': `${label(id)} 的模型`, disabled: !models.length });
        const routes = o.profiles.map(c => [c.name, connectionName(c.name)]);
        if (!routes.some(([v]) => v === now.backend)) routes.unshift([now.backend, connectionName(now.backend)]);
        const route = select(routes, now.backend, { 'aria-label': `${label(id)} 的连接`, class: 'input small' });
        const levels = levelsOf(now.backend, now.model);
        const effort = select([['', levels.length ? '默认强度' : '不可调'], ...levels.map(l => [l, effortName(l)])], levels.includes(room.model_settings?.[id]?.effort) && pinned ? room.model_settings[id].effort : '', { disabled: !levels.length, 'aria-label': `${label(id)} 的推理强度` });
        pick.addEventListener('change', () => apply(id, now.backend, { model: pick.value }, `${label(id)} 在这个聊天里改用 ${modelName(now.backend, pick.value)}`));
        route.addEventListener('change', () => apply(id, route.value, route.value === 'legacy' ? undefined : {}, `${label(id)} 在这个聊天里改用连接：${connectionName(route.value)}`));
        effort.addEventListener('change', () => apply(id, now.backend, { ...(now.model ? { model: now.model } : {}), ...(effort.value ? { effort: effort.value } : {}) },
            `${label(id)} 的推理强度：${effort.value ? effortName(effort.value) : '默认'}`));
        const profile = now.profile, thinkable = profile?.kind === 'api' && profile.protocol !== 'openai-chat';
        box.append(h('div', { class: 'room-model', style: { '--hue': color(id) } },
            h('div', { class: 'room-model-who' }, sigil(id), h('strong', null, label(id)),
                pinned || moved ? h('button', { class: 'tiny-btn', title: '回到这位参与者自己的设置', onclick: () => apply(id, own?.connection || now.backend, {}, `${label(id)} 已恢复默认设置`) }, '恢复默认') : h('span', { class: 'hint' }, '跟随默认'),
                thinkable ? h('button', { class: 'tiny-btn', title: '高级：thinking 参数（JSON）', onclick: () => promptDialog('thinking 参数（JSON）', room.model_settings?.[id]?.thinking ? JSON.stringify(room.model_settings[id].thinking) : '', '保存', async text => {
                    let thinking = null;
                    if (text) { try { thinking = JSON.parse(text); } catch (e) { throw new Error('不是合法的 JSON。'); } }
                    await api(`/api/chat/rooms/${room.id}/backend`, { model: id, backend: now.backend, settings: { ...(now.model ? { model: now.model } : {}), ...(thinking ? { thinking } : {}) } });
                    await refreshRooms(); rerender();
                }, { placeholder: '留空使用连接默认', maxLength: 400, note: '仅 Anthropic、Google 原生协议支持。' }) }, 'thinking') : null),
            h('div', { class: 'room-model-pick' }, pick, effort),
            h('label', { class: 'room-route' }, h('span', null, '连接'), route),
            now.entry ? h('div', { class: 'facts' }, facts(now.entry, profile?.kind === 'api'), proof(now.entry)) : null));
    }
    const pick = select([['', o.combos.length ? '选择一个常用组合…' : '还没有保存过组合'], ...o.combos.map(c => [c.id, c.name])], '', { disabled: !o.combos.length });
    box.append(h('div', { class: 'card' }, h('div', { class: 'card-title' }, '常用组合'),
        h('div', { class: 'inline' }, pick,
            actionButton({ label: '应用', action: async () => {
                const combo = o.combos.find(c => c.id === pick.value);
                if (!combo) throw new Error('请先选择一个组合。');
                for (const [id, item] of Object.entries(combo.items)) {
                    if (!IDS.includes(id)) continue;
                    const settings = Object.fromEntries(['model', 'effort', 'thinking'].filter(k => item[k] != null).map(k => [k, item[k]]));
                    await api(`/api/chat/rooms/${room.id}/backend`, { model: id, backend: item.backend, settings });
                }
                await refreshRooms(); toast(`已应用“${combo.name}”`); rerender(); return false;
            } }),
            o.combos.length ? h('button', { class: 'btn', title: '删除选中的组合', onclick: async () => { if (!pick.value) return; await act('/api/chat/combos/' + pick.value, undefined, 'DELETE'); rerender(); } }, '删除') : null),
        h('div', { class: 'card-actions' }, h('button', { class: 'btn small', onclick: () => promptDialog('保存为常用组合', '', '保存', async name => {
            const items = Object.fromEntries(IDS.map(id => {
                const now = effectiveModel(room, id);
                return [id, { backend: now.backend, ...(now.model ? { model: now.model } : {}), ...(now.effort && levelsOf(now.backend, now.model).includes(now.effort) ? { effort: now.effort } : {}) }];
            }).filter(([, v]) => v.backend !== 'legacy'));
            await act('/api/chat/combos', { name, items }); toast('已保存'); rerender();
        }, { placeholder: '例如：深度推理 / 快速讨论', maxLength: 40, note: '保存这个聊天里每位参与者当前的模型和推理强度，之后可以一键用到其他聊天。' }) }, '把当前设置存为组合'))),
        h('p', { class: 'hint' }, '添加服务商、模型和参与者，在 设置 → 模型与服务。'));
}
