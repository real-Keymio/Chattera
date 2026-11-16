// Global settings ("设置"): models and services, research lenses, retrieval and tools, appearance, general, data, diagnostics.
// Room-level choices live in panel.js.
import { h, icon, ICONS, api, toast, modal, actionButton, confirmDialog, fmtNum, fmtTime, store, TOKEN } from './util.js';
import { S, IDS, label, sigil, loadModels, sendRaw, activeRoom, lensOf } from './state.js';
import { friendlyReason } from './panes.js';
import { studioModels, connectionName } from './models.js';
import { applyAppearance, setSkin, skin } from './skin.js';
import { librarySettings } from './library.js';
import { openGuide, showWelcome } from './setup.js';

const SECTIONS = [['models', '模型与服务', 'cpu'], ['lenses', '研究视角', 'lens'], ['tools', '检索与工具', 'search'], ['look', '外观', 'spark'],
    ['general', '通用', 'gear'], ['data', '数据', 'archive'], ['diagnostics', '诊断', 'info'], ['help', '帮助', 'book']];
const group = (title, ...kids) => h('section', { class: 'set-group' }, h('h3', null, title), kids);
const row = (name, hint, control) => h('div', { class: 'set-row' }, h('div', { class: 'set-label' }, h('span', null, name), hint ? h('small', null, hint) : null), control);
function choice(key, fallback, options, after) {
    const current = String(store.get(key, fallback));
    return h('div', { class: 'seg' }, options.map(([value, text]) => h('button', { class: String(value) === current ? 'active' : '',
        onclick: event => {
            store.set(key, value); applyAppearance(); after?.();
            event.currentTarget.parentElement.querySelectorAll('button').forEach(b => b.classList.toggle('active', b === event.currentTarget));
        } }, text)));
}

// ---------- research lenses ----------
async function lenses(box) {
    const rerender = async () => { const next = h('div'); await lenses(next); box.replaceChildren(...next.childNodes); };
    await loadModels();
    const list = S.models?.lenses || [], room = activeRoom();
    const edit = lens => {
        const name = h('input', { class: 'input', value: lens?.name || '', maxLength: 40, placeholder: '例如：机制与理论解释' });
        const summary = h('input', { class: 'input', value: lens?.summary || '', maxLength: 200, placeholder: '一句话说明它关注什么（显示在菜单里）' });
        const method = h('textarea', { class: 'input area', value: lens?.method || '', maxLength: 4000, style: { minHeight: '220px' },
            placeholder: '用几句话描述这种分析方法：读问题时先看什么、追问什么、怎样算证据充分。写方法，不要写结论，也不要规定回答的格式。' });
        modal({ title: lens?.id && !lens.builtin ? '编辑研究视角' : '新的研究视角', wide: true,
            body: [h('label', { class: 'field' }, '名称', name), h('label', { class: 'field' }, '简介', summary), h('label', { class: 'field' }, '方法说明（会原样交给模型）', method),
                h('p', { class: 'hint' }, '好的视角描述的是“怎么看”，而不是“看出什么”。模型仍然按开放讨论的方式回答：可以不同意你，也不必写成报告。用英文写通常更稳定，中文也可以。')],
            actions: [{ label: '保存', primary: true, action: async () => {
                const value = await api('/api/chat/lenses', { id: lens?.builtin ? undefined : lens?.id, name: name.value, summary: summary.value, method: method.value });
                S.models = value.overview; rerender();
            } }] });
    };
    box.append(h('p', { class: 'hint' }, '研究视角是一段分析方法说明，随提问交给模型。它决定模型重点审视什么，不预设结论，也不把模型变成固定角色。可以为整个聊天、某位参与者或单次提问选择。'),
        h('div', { class: 'card-actions' }, h('button', { class: 'btn', onclick: () => edit(null) }, '＋ 新建视角')),
        ...list.map(lens => {
            const text = h('div', { class: 'lens-method', hidden: true }, lens.method || '不附加方法说明：模型只收到通用的开放讨论约定（用户决定方向、主动质疑前提、保留分歧、不强求共识、不把讨论变成任务交付）。');
            const inRoom = room && (room.lens || 'open') === lens.id;
            return h('div', { class: 'lens-card' + (inRoom ? ' active' : '') },
                h('h4', null, lens.name, lens.builtin ? h('span', { class: 'cap' }, '内置') : h('span', { class: 'cap yes' }, '自定义'), inRoom ? h('span', { class: 'cap yes' }, '当前聊天在用') : null),
                h('p', null, lens.summary || ''),
                h('div', { class: 'card-actions' },
                    h('button', { class: 'btn small', onclick: () => { text.hidden = !text.hidden; } }, '查看内容'),
                    h('button', { class: 'btn small', onclick: () => edit({ ...lens, id: undefined, builtin: false, name: lens.name + ' · 副本' }) }, '复制并修改'),
                    lens.builtin ? null : h('button', { class: 'btn small', onclick: () => edit(lens) }, '编辑'),
                    lens.builtin ? null : h('button', { class: 'btn small danger', onclick: () => confirmDialog('删除这个视角', `“${lens.name}”会被删除。正在使用它的聊天将回到“开放科学讨论”；已有回答不受影响。`, '删除',
                        async () => { S.models = await api('/api/chat/lenses/' + lens.id, undefined, 'DELETE'); rerender(); }, true) }, '删除')),
                text);
        }));
}

// ---------- retrieval & tools ----------
async function tools(box) {
    const rerender = async () => { const next = h('div'); await tools(next); box.replaceChildren(...next.childNodes); };
    await loadModels();
    const s = S.models?.search || {}, profiles = S.models?.profiles || [];
    const save = async body => { try { S.models = await api('/api/chat/search-settings', body); rerender(); } catch (e) { toast(e.message, 'error'); } };
    const provider = h('select', { class: 'input' }, [['duckduckgo', 'DuckDuckGo（无需 Key）'], ['tavily', 'Tavily（需要 Key）'], ['brave', 'Brave Search（需要 Key）']]
        .map(([v, t]) => h('option', { value: v, selected: s.web_provider === v }, t)));
    provider.addEventListener('change', () => save({ web_provider: provider.value }));
    const keyRow = (name, title, present, note) => {
        const input = h('input', { class: 'input', type: 'password', autocomplete: 'off', placeholder: present ? '粘贴新的 Key 以替换' : '粘贴 Key' });
        return row(title, note, h('div', { class: 'inline' }, input, actionButton({ label: present ? '更新' : '保存', action: async () => {
            const key = input.value.trim(); input.value = '';
            if (!key) throw new Error('请粘贴 Key。');
            S.models = await api(`/api/chat/profiles/${name}/key`, { key }); toast('已保存到本机私有目录'); rerender(); return false;
        } }), present ? h('button', { class: 'btn', onclick: async () => { S.models = await api(`/api/chat/profiles/${name}/key`, undefined, 'DELETE'); rerender(); } }, '移除') : null));
    };
    const planner = h('select', { class: 'input' }, [['auto', `自动（当前：${s.planner_effective ? connectionName(s.planner_effective) : '没有可用的 API 连接'}）`], ['off', '关闭：直接用问题原文检索'],
        ...profiles.filter(p => p.kind === 'api').map(p => [p.name, connectionName(p.name) + ' · ' + p.model])].map(([v, t]) => h('option', { value: v, selected: (s.planner || 'auto') === v }, t)));
    planner.addEventListener('change', () => save({ planner: planner.value }));
    const reader = h('input', { type: 'checkbox', checked: s.reader_fallback !== false, onchange: event => save({ reader_fallback: event.target.checked }) });
    const query = h('input', { class: 'input', placeholder: '输入检索词试一下', value: '' }), out = h('div');
    const tryIt = kind => actionButton({ label: kind === 'web' ? '试搜网页' : '试搜文献', action: async () => {
        if (!query.value.trim()) throw new Error('请先输入检索词。');
        out.replaceChildren(h('p', { class: 'hint' }, '检索中…'));
        const value = await api('/api/chat/search-test', { kind, query: query.value.trim() });
        out.replaceChildren(h('p', { class: 'hint' }, `${kind === 'web' ? '搜索服务 ' + value.provider : '来源 ' + (value.sources || []).join('、')} · ${value.results.length} 条${value.errors?.length ? ' · 未响应：' + value.errors.join('；') : ''}`),
            h('table', { class: 'model-table' }, h('tbody', null, value.results.map(r => h('tr', null, h('td', null, h('a', { class: 'link', href: r.url, target: '_blank', rel: 'noopener noreferrer' }, r.title || r.url),
                h('div', { class: 'hint' }, kind === 'web' ? (r.snippet || '').slice(0, 160) : `${(r.authors || []).slice(0, 3).join(', ')} · ${r.year || ''} · ${r.venue || ''} · ${r.abstract ? '有摘要' : '仅文献信息'}${r.oa_url || r.fulltext_pmcid ? ' · 有开放全文' : ''}`)))))));
        return false;
    } });
    const able = IDS.map(id => {
        const names = [...new Set(S.rooms.filter(r => !r.archived).map(r => r.owners?.[id]).filter(Boolean))];
        const yes = names.some(n => n === 'claude' || n === 'codex' || profiles.find(p => p.name === n)?.tools);
        return h('span', { class: 'cap ' + (yes ? 'yes' : '') }, sigil(id, 'xs'), ` ${label(id)}：${yes ? '可自行检索' : '只接收共享检索'}`);
    });
    box.append(
        h('p', { class: 'hint' }, '两种方式让讨论用上外部资料：① 在输入区打开“检索”，系统先检索一次，把同一份资料交给本轮所有回答者；② 支持工具的模型在回答过程中自行检索、读网页、读文献全文和核对数字。两种都会留下可追溯的记录。'),
        group('谁可以自行检索', h('div', { class: 'profile-line' }, able), h('p', { class: 'hint' }, '在 模型与服务 里为连接的模型打开“支持工具调用”即可启用。私聊上下文中，外部检索工具始终关闭。')),
        group('网页搜索',
            row('搜索服务', 'DuckDuckGo 无需配置，但偶尔会限流；Tavily、Brave 需要自己申请 Key，结果更稳定。', provider),
            s.web_provider === 'tavily' ? keyRow('search_tavily', 'Tavily Key', s.web_key_present, '在 tavily.com 申请。') : null,
            s.web_provider === 'brave' ? keyRow('search_brave', 'Brave Search Key', s.web_key_present, '在 brave.com/search/api 申请。') : null,
            s.web_provider !== 'duckduckgo' && !s.web_key_present ? h('p', { class: 'msg-note warn' }, '还没有填写 Key：在填写之前，网页搜索会退回 DuckDuckGo。') : null,
            row('读不到正文时使用公共阅读服务', '有些网页需要脚本渲染或拒绝直接访问。开启后会把该网页的地址（不含你的问题）交给 r.jina.ai 代为读取。', reader)),
        group('学术文献',
            h('p', { class: 'hint' }, '同时查询 OpenAlex、Europe PMC、Crossref 和 arXiv，按 DOI 合并。能取得开放获取全文时会读取正文（Europe PMC 全文、arXiv 或开放 PDF），否则只有摘要或文献信息——界面和模型都会看到这个区别。'),
            keyRow('search_openalex', 'OpenAlex Key（可选）', s.openalex_key_present, '不填也能用，但与同网络的其他人共享每日免费额度；可在 openalex.org 免费申请。')),
        group('检索词', row('由谁把问题改写成检索词', '只在你没有填写检索词时使用；会把那条提问发给所选的 API 连接（一次很小的请求）。私聊不会自动改写。', planner)),
        group('试一下', h('div', { class: 'inline' }, query, tryIt('web'), tryIt('scholar')), out),
        group('数值核查', h('p', { class: 'hint' }, '支持工具的模型可以调用内置计算器核对算术和基础统计（均值、标准差、置信区间、t 检验、相关、回归、比例区间、卡方）。它只计算你们给出的数字，不能运行程序或访问文件。')));
}

// ---------- appearance ----------
function look(box) {
    const cards = h('div', { class: 'skin-cards' });
    const draw = () => cards.replaceChildren(...[['expressive', 'x', '表版 · 共鸣', '一个有纵深的空间：每位参与者在自己的轨道上，思考、联网搜索和书写都看得见；回答从深处浮现。有深色和浅色两种主题。'],
        ['focus', 'f', '里版 · 纸本', '纸色与细线，低干扰：适合长时间阅读论文、比较长回答和日常高频使用。可选浅色或深色。']].map(([key, cls, name, text]) =>
        h('button', { class: 'skin-card' + (skin() === key ? ' active' : ''), onclick: event => { setSkin(key, event.currentTarget); setTimeout(draw, 60); } },
            h('div', { class: 'demo ' + cls }, h('i'), h('i'), h('i')), h('div', { class: 'cap-text' }, h('strong', null, name), h('small', null, text)))));
    draw();
    box.append(group('视觉模式', cards, h('p', { class: 'hint' }, '两种模式共用同一份聊天、草稿、视口布局和设置，随时可以在侧栏底部的“表 | 里”切换。')),
        group('显示',
            row('表版主题', '“共鸣”的深色是发光的空间，浅色是日光下的瓷面，两者都有完整的三维舞台。', choice('x-theme', 'dark', [['system', '跟随系统'], ['light', '浅色'], ['dark', '深色']])),
            row('里版主题', '“纸本”的主题单独选择。', choice('theme', 'system', [['system', '跟随系统'], ['light', '浅色'], ['dark', '深色']])),
            row('字号', '影响消息正文和界面文字。', choice('font-scale', 1, [[0.93, '小'], [1, '标准'], [1.08, '大'], [1.16, '特大']])),
            row('阅读宽度', '单栏时每行的最大宽度；多栏时各栏自动占满。', choice('reading-width', 'normal', [['narrow', '窄'], ['normal', '标准'], ['wide', '宽']])),
            row('动态效果', '“跟随系统”会遵循系统的“减少动态效果”设置；关闭后两种模式都不再有动画。', choice('motion', 'full', [['full', '完整'], ['system', '跟随系统'], ['off', '关闭']]))));
}

// ---------- general ----------
function general(box) {
    const name = h('input', { class: 'input', value: S.username, maxLength: 20 });
    const history = h('select', { class: 'input' }, [['all', '全部（每个聊天最多 10000 条）'], ['500', '最近 500 条'], ['200', '最近 200 条']].map(([v, t]) =>
        h('option', { value: v, selected: String(S.settings.history_limit ?? 'all') === v }, t)));
    history.addEventListener('change', () => { sendRaw({ type: 'update_settings', data: { history_limit: history.value } }); toast('已保存，刷新页面后生效'); });
    box.append(group('身份',
        row('你的显示名称', '用于之后发送的消息；不会改写历史消息的作者。', h('div', { class: 'inline' }, name, actionButton({ label: '保存', action: async () => {
            if (!name.value.trim()) throw new Error('名称不能为空。');
            sendRaw({ type: 'update_settings', data: { username: name.value.trim() } }); toast('名称已保存'); return false;
        } })))),
        group('历史', row('打开页面时加载', '只影响浏览器里可显示和可搜索的范围；服务器上的完整历史不变。', history)),
        group('快捷键', h('dl', { class: 'keys' },
            ...[['Enter', '发送'], ['Shift + Enter', '换行'], ['Ctrl + K', '搜索聊天与消息'], ['Ctrl + Shift + O', '新聊天'], ['Ctrl + 1 / 2 / 3', '单栏 / 双栏 / 三栏阅读'], ['Esc', '取消引用 / 关闭弹层']]
                .flatMap(([k, v]) => [h('dt', null, h('kbd', null, k)), h('dd', null, v)]))));
}

async function helpPage(box) {
    const info = await api('/api/chattera/setup');
    box.append(group('使用指南',
        h('p', { class: 'hint' }, '两份图文指南都在 Chattera 程序文件夹里，也可以在这里直接打开。'),
        h('div', { class: 'card-actions' },
            h('button', { class: 'btn primary', disabled: !info.guides.start, onclick: () => openGuide('start') }, '先看这个（入门）'),
            h('button', { class: 'btn', disabled: !info.guides.full, onclick: () => openGuide('full') }, '完整使用指南'),
            h('button', { class: 'btn', onclick: () => { document.querySelector('dialog.settings-modal')?.close(); document.querySelector('dialog.settings-modal')?.remove(); showWelcome(info, true); } }, '重新打开欢迎页')),
        !info.guides.start ? h('p', { class: 'hint' }, '没有找到指南文件（从源码运行时，指南需要先生成，见 docs/INSTALL.md）。') : null),
        group('费用', h('p', { class: 'hint' }, 'Chattera 本身免费、开源。使用真实 AI 模型时，你是直接向模型服务商（或你选择的中转服务）付费，按用量计费，与 Chattera 无关。演示模式不调用任何模型，不产生费用。')),
        group('关于', h('dl', { class: 'kv' },
            h('dt', null, '版本'), h('dd', null, 'Chattera ' + info.version),
            h('dt', null, '数据位置'), h('dd', null, h('code', null, info.home), info.portable ? '（便携模式）' : ''),
            h('dt', null, '许可'), h('dd', null, 'MIT；基于 agentchattr（MIT）。第三方组件见程序文件夹中的 THIRD_PARTY_NOTICES。'))),
        group('退出', h('p', { class: 'hint' }, '关闭这个窗口不会停止 Chattera。要完全退出，点下面的按钮，或在任务栏通知区域的 Chattera 图标上选择“退出”。'),
            h('div', { class: 'card-actions' }, actionButton({ label: '退出 Chattera', danger: true, action: async () => { await api('/api/chattera/quit', {}); document.body.replaceChildren(h('div', { class: 'quit-note' }, 'Chattera 已退出。可以关闭这个窗口；下次双击 Chattera.exe 重新打开。')); } }))));
}

function data(box) {
    const lib = h('div');
    librarySettings(lib);
    const where = h('p', { class: 'hint' }, '…');
    api('/api/chattera/setup').then(info => where.replaceChildren('聊天、设置、API Key、资料库和日志都保存在 ', h('code', null, info.home),
        info.portable ? '（便携模式：在程序文件夹旁边）。' : '。升级或替换程序文件夹不会影响这些数据。')).catch(() => {});
    box.append(group('数据保存在哪里', where), group('资料库', lib), group('导出',
        h('p', { class: 'hint' }, '导出全部聊天（含私聊）的消息与元数据为 ZIP。不含附件原文件、摘要版本、调用快照和用量账本，所以不是完整备份；文件含私有内容，请勿上传到云盘。'),
        h('div', { class: 'card-actions' }, actionButton({ label: '导出聊天历史', action: async () => {
            const response = await fetch('/api/export', { headers: { 'X-Session-Token': TOKEN } });
            if (!response.ok) throw new Error('导出失败');
            const url = URL.createObjectURL(await response.blob());
            const name = /filename="([^"]+)"/.exec(response.headers.get('Content-Disposition') || '')?.[1] || 'chattera-export.zip';
            h('a', { href: url, download: name }).click(); setTimeout(() => URL.revokeObjectURL(url), 60000); return false;
        } }))),
        group('保留与删除', h('p', { class: 'hint' }, '消息、版本和分享都以追加方式保存。目前没有删除聊天、清空或导入覆盖的功能；想把聊天收起来请用归档，归档后可随时恢复。想换个方向又不想丢掉现有讨论，可以在某条消息上“建立分支”。')));
}

async function diagnostics(box) {
    await loadModels();
    const proof = S.verification || {};
    const clis = h('div', null, h('p', { class: 'hint' }, '正在扫描…'));
    const AUTH = { 'signed-in': '已登录', 'signed-out': '未登录', UNKNOWN: '未知' };
    const scan = () => api('/api/chattera/cli-scan').then(value => {
        const paths = h('textarea', { class: 'input', rows: 3, placeholder: '每行一个文件夹，例如 D:\\Tools\\claude', value: (value.extra_paths || []).join('\n') });
        clis.replaceChildren(h('table', { class: 'grid' }, h('thead', null, h('tr', null, ['CLI', '状态', '登录', '版本', '位置 / 说明'].map(t => h('th', null, t)))),
            h('tbody', null, value.clis.map(c => h('tr', null, h('td', null, c.label),
                h('td', null, h('span', { class: 'cap ' + (c.usable ? 'yes' : c.discovered ? 'warn' : '') }, c.usable ? '可以聊天' : c.adapted && c.discovered ? '已适配' : c.discovered ? '已发现' : '未找到')),
                h('td', null, c.auth ? h('span', { title: '来自官方状态命令：' + c.auth.source }, AUTH[c.auth.status] || '未知', c.auth.method ? ` · ${c.auth.method}` : '') : '—'),
                h('td', null, c.version || '—'), h('td', null, c.path ? h('code', { style: { fontSize: '.8em' } }, c.path) : null, h('div', { class: 'hint' }, c.note)))))),
            row('额外扫描位置', '除了系统 PATH 和各家默认安装位置，还会在这些文件夹里找 CLI。保存后重新扫描；新找到的 CLI 在重启 Chattera 后出现在连接列表里（默认关闭）。',
                h('div', null, paths, h('div', { class: 'card-actions' }, actionButton({ label: '保存并重新扫描', action: async () => {
                    await api('/api/chattera/cli-paths', { paths: paths.value.split('\n').map(s => s.trim()).filter(Boolean) }); clis.replaceChildren(h('p', { class: 'hint' }, '正在扫描…')); scan(); return false;
                } })))));
    }).catch(e => clis.replaceChildren(h('p', { class: 'form-error' }, e.message)));
    scan();
    box.append(group('连接', h('dl', { class: 'kv' },
        h('dt', null, '实时连接'), h('dd', null, S.connected ? '已连接' : '已断开，正在重连'),
        h('dt', null, '地址'), h('dd', null, location.origin + '（仅本机可访问）'),
        h('dt', null, '聊天数'), h('dd', null, `${S.rooms.filter(r => !r.archived).length} 个进行中，${S.rooms.filter(r => r.archived).length} 个已归档`),
        h('dt', null, '已加载消息'), h('dd', null, fmtNum(S.msgs.size)))),
        group('各连接的调用记录', Object.keys(proof).length ? h('table', { class: 'grid' },
            h('thead', null, h('tr', null, ['连接', '成功', '失败', '最近成功', '最近失败原因'].map(t => h('th', null, t)))),
            h('tbody', null, Object.entries(proof).map(([name, v]) => h('tr', null, h('td', null, connectionName(name)), h('td', { class: 'num' }, v.completed), h('td', { class: 'num' }, v.failed),
                h('td', null, v.last_success_at ? fmtTime(v.last_success_at) : '—'), h('td', null, v.last_failure_reason && (!v.last_success_at || v.last_failure_at > v.last_success_at) ? friendlyReason(v.last_failure_reason) : '—')))))
            : h('p', { class: 'hint' }, '还没有调用记录。'),
            h('p', { class: 'hint' }, '每次调用的输入范围、用量和原始失败信息见 房间 → 记录。')),
        group('本机订阅 CLI', clis, h('p', { class: 'hint' }, '“已发现”只说明这台电脑上装了它；“已适配”表示 Chattera 有它的聊天适配器；“可以聊天”表示现在就能作为参与者的连接使用。登录状态只来自各家 CLI 自己的状态命令，Chattera 不读取、不复制任何登录凭据，也不会替你登录。'),
            h('p', { class: 'hint' }, '注意：CLI 能以非交互方式调用，不代表服务商允许第三方软件使用你的消费者订阅。启用之前，请自行确认对应服务条款。')));
}

export function openSettings(start = 'models') {
    const body = h('div', { class: 'set-body' });
    const nav = h('nav', { class: 'set-nav' });
    const show = async key => {
        nav.querySelectorAll('button').forEach(b => b.classList.toggle('active', b.dataset.key === key));
        const box = h('div', { class: 'set-page' });
        body.replaceChildren(box);
        body.scrollTop = 0;
        try { await ({ models: studioModels, lenses, tools, look, general, data, diagnostics, help: helpPage })[key](box); } catch (e) { box.append(h('p', { class: 'form-error' }, e.message)); }
    };
    nav.append(...SECTIONS.map(([key, text, glyph]) => h('button', { dataset: { key }, onclick: () => show(key) }, icon(ICONS[glyph], 16), text)));
    const dialog = modal({ title: '设置', wide: true, body: h('div', { class: 'set' }, nav, body) });
    dialog.el.classList.add('settings-modal', 'studio');
    show(start);
}
