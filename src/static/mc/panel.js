// Room details drawer: per-room settings and diagnostics, hidden by default.
// Global provider/profile/key matters live in settings.js, not here.
import { h, icon, ICONS, api, toast, modal, actionButton, fmtTime, fmtNum, store } from './util.js';
import { S, IDS, ALL, on, emit, label, color, sigil, tile, activeRoom, refreshRooms, loadModels, lensOf } from './state.js';
import { renameRoom, archiveRoom } from './sidebar.js';
import { stopTask, friendlyReason, scrollToMessage } from './panes.js';
import { roomModels } from './models.js';
import { libraryTab } from './library.js';
import { openMaterials } from './evidence.js';

const TABS = [['discussion', '讨论'], ['library', '资料'], ['models', '模型'], ['context', '上下文'], ['summary', '记忆'], ['calls', '记录']];
let tab = store.get('panel-tab2', 'discussion');
let focusTask = null;
let generation = 0;
const panel = () => document.getElementById('room-panel');

const roomApi = (room, path = '', body) => api(`/api/chat/rooms/${room.id}${path}`, body);
const button = (text, action, cls = '') => actionButton({ label: text, action: async () => { await action(); return false; }, primary: cls === 'primary', danger: cls === 'danger' });
const section = (title, ...kids) => h('section', { class: 'p-section' }, title ? h('h3', null, title) : null, kids);

export function togglePanel(open) {
    const el = panel();
    el.hidden = open === undefined ? !el.hidden : !open;
    store.set('panel-open', !el.hidden);
    document.getElementById('panel-toggle').classList.toggle('on', !el.hidden);
    if (!el.hidden) render();
}

async function render() {
    const el = panel(), room = activeRoom(), mine = ++generation;
    if (el.hidden) return;
    const body = h('div', { class: 'p-body' }, h('p', { class: 'hint' }, '加载中…'));
    el.replaceChildren(
        h('div', { class: 'p-head' }, h('h2', null, room ? room.name : '房间'), h('button', { class: 'icon-btn', 'aria-label': '关闭', onclick: () => togglePanel(false) }, icon(ICONS.close))),
        h('div', { class: 'tabs', role: 'tablist' }, TABS.map(([key, text]) => h('button', { class: 'tab' + (key === tab ? ' active' : ''), role: 'tab',
            'aria-selected': String(key === tab), onclick: () => { tab = key; store.set('panel-tab2', key); render(); } }, text))), body);
    if (!room) { body.replaceChildren(h('p', { class: 'hint' }, '请先选择一个聊天。')); return; }
    const content = h('div');
    try { await ({ discussion, library: (r, box) => libraryTab(r, box, render), models, context, summary, calls }[tab] || discussion)(room, content); }
    catch (e) { content.replaceChildren(h('p', { class: 'form-error' }, e.message)); }
    if (mine === generation) body.replaceChildren(content);
}

// ---------- discussion: lens, pins, room facts ----------
async function discussion(room, box) {
    if (!S.models) await loadModels();
    const lenses = S.models?.lenses || [];
    const options = lenses.map(l => h('option', { value: l.id }, l.name));
    const shared = h('select', { class: 'input' }, lenses.map(l => h('option', { value: l.id, selected: (room.lens || 'open') === l.id }, l.name)));
    const about = h('p', { class: 'hint' });
    const describe = () => { const lens = lensOf(shared.value); about.textContent = lens?.summary || ''; };
    shared.addEventListener('change', async () => {
        try { await roomApi(room, '/lens', { lens: shared.value }); await refreshRooms(); toast('房间的研究视角已更新'); } catch (e) { toast(e.message, 'error'); }
        describe();
    });
    describe();
    const per = room.lens_by_model || {};
    const rows = IDS.map(id => {
        const pick = h('select', { class: 'input' }, h('option', { value: '' }, '跟随房间'), lenses.map(l => h('option', { value: l.id, selected: per[id] === l.id }, l.name)));
        pick.addEventListener('change', async () => {
            const next = { ...(activeRoom()?.lens_by_model || {}) };
            if (pick.value) next[id] = pick.value; else delete next[id];
            try { await roomApi(room, '/lens', { lens_by_model: next }); await refreshRooms(); } catch (e) { toast(e.message, 'error'); }
        });
        return h('div', { class: 'who-row' }, h('label', null, sigil(id, 'xs'), label(id)), h('div', { class: 'inline' }, pick,
            h('button', { class: 'btn small', title: `查看 ${label(id)} 在这个聊天里实际收到的固定说明`, onclick: () => effectivePrompt(room, id) }, '查看')));
    });
    box.append(section('研究视角', h('label', { class: 'field' }, '所有参与者共用', shared), about,
        h('details', null, h('summary', null, '让不同参与者使用不同视角'), rows,
            h('p', { class: 'hint' }, '例如同一篇论文让 Claude 看研究设计、Codex 看统计推断。这只是这个聊天里的分工，随时可以改；单次提问还可以在输入区临时换视角。')),
        h('p', { class: 'hint' }, '视角是分析方法，不预设结论，也不要求固定的回答格式。新增或修改视角：设置 → 研究视角。')));

    const pins = room.pins || [];
    box.append(section(`固定内容（${pins.length}）`,
        pins.length ? pins.map(pin => h('div', { class: 'pin-item' }, icon(ICONS.pin, 15),
            h('div', { style: { flex: 1, minWidth: 0 } }, h('p', null, pin.text),
                h('div', { class: 'pin-src' }, pin.author ? [ALL.includes(pin.author) ? sigil(pin.author, 'xs') : null, ALL.includes(pin.author) ? label(pin.author) : pin.author] : '手动添加',
                    pin.source_message_id != null ? h('button', { class: 'link', onclick: () => scrollToMessage(pin.source_message_id) }, `来源 #${pin.source_message_id}`) : null)),
            h('button', { class: 'icon-btn tiny', title: '取消固定', onclick: async () => { try { await api(`/api/chat/rooms/${room.id}/pins/${pin.id}`, undefined, 'DELETE'); await refreshRooms(); } catch (e) { toast(e.message, 'error'); } } }, icon(ICONS.close, 13))))
            : h('p', { class: 'hint' }, '还没有固定内容。在任意公开消息的“更多”里选择“固定到房间背景”，或在下面直接写一条。'),
        (() => {
            const input = h('textarea', { class: 'input area small', placeholder: '写下一条研究前提、已确认的结论或约束…', maxLength: 2000 });
            return h('div', null, input, h('div', { class: 'card-actions' }, button('固定这段文字', async () => {
                if (!input.value.trim()) throw new Error('请先写下要固定的内容。');
                await roomApi(room, '/pins', { text: input.value.trim() }); await refreshRooms(); toast('已固定');
            })));
        })(),
        h('p', { class: 'hint' }, '固定内容会作为房间背景带进之后每一次提问（公开与私聊都会），并保留来源。最多 12 条。')));

    box.append(section('这个聊天',
        h('dl', { class: 'kv' }, h('dt', null, '名称'), h('dd', null, room.name),
            room.forked_from ? [h('dt', null, '分支自'), h('dd', null, `${room.forked_from.room_name || ''} #${room.forked_from.message_id}`)] : null,
            h('dt', null, '创建时间'), h('dd', null, room.created_at ? new Date(room.created_at * 1000).toLocaleString('zh-CN') : '未知'),
            h('dt', null, '固定 ID'), h('dd', null, h('code', null, room.id))),
        h('div', { class: 'card-actions' }, h('button', { class: 'btn', onclick: () => renameRoom(room) }, '重命名'),
            h('button', { class: 'btn', onclick: () => archiveRoom(room) }, '归档')),
        h('p', { class: 'hint' }, '重命名只改变显示名称。归档会隐藏聊天并停止生成，可随时恢复。目前没有删除聊天：历史以只追加方式保存，安全删除（连同附件、摘要、版本与调用快照）尚未实现。')));
}

async function effectivePrompt(room, id) {
    const dialog = modal({ title: `${label(id)} 在这个聊天里收到的固定说明`, wide: true, body: h('p', { class: 'hint' }, '读取中…') });
    try {
        const value = await api(`/api/chat/rooms/${room.id}/effective-prompt?model=${id}`);
        dialog.body.replaceChildren(
            h('h4', { class: 'mat-sec' }, '身份与讨论方式（所有聊天相同）'), h('div', { class: 'lens-method' }, value.identity),
            h('h4', { class: 'mat-sec', style: { marginTop: '14px' } }, '研究视角'), value.lens ? h('div', { class: 'lens-method' }, `【${value.lens.name}】\n` + value.lens.method) : h('p', { class: 'hint' }, '开放科学讨论：不附加额外的方法说明。'),
            value.pins?.length ? [h('h4', { class: 'mat-sec', style: { marginTop: '14px' } }, '固定内容'), h('div', { class: 'lens-method' }, value.pins.map(p => '· ' + p.text).join('\n'))] : null,
            (value.global_rules?.rules || []).length ? [h('h4', { class: 'mat-sec', style: { marginTop: '14px' } }, '全局规则'), h('div', { class: 'lens-method' }, value.global_rules.rules.map(r => '· ' + (r.text || r)).join('\n'))] : null,
            h('p', { class: 'hint' }, '除此之外，每次提问还会带上当时的历史、摘要、附件和检索资料；单次提问临时选择的视角不在这里显示，可在回答下方的“依据”里看到。'));
    } catch (e) { dialog.body.replaceChildren(h('p', { class: 'form-error' }, e.message)); }
}

async function models(room, box) { await roomModels(room, box, render); }

// ---------- context capacity ----------
async function context(room, box) {
    const legacy = { standard: 20000, long: 100000, maximum: 1000000 };
    const current = room.context_mode in legacy ? 'custom' : room.context_mode || '256k';
    const custom = h('input', { class: 'input', type: 'number', min: 1, max: 1000000, step: 1, value: legacy[room.context_mode] || room.context_limit_tokens || 262144 });
    const radios = [['100k', '100K'], ['256k', '256K（默认）'], ['1m', '1M'], ['custom', '自定义']].map(([value, text]) =>
        h('label', { class: 'radio' }, h('input', { type: 'radio', name: 'ctx-mode', value, checked: value === current, onchange: () => { custom.hidden = value !== 'custom'; } }), text));
    custom.hidden = current !== 'custom';
    const table = h('div');
    const load = async () => {
        const cap = await roomApi(room, '/context-capacity');
        table.replaceChildren(h('p', { class: 'hint' }, `所选上限：${fmtNum(cap.selected_limit_tokens)} Token。下表是每个模型实际生效的可控输入容量。`),
            h('table', { class: 'grid' }, h('thead', null, h('tr', null, ['模型', '实际可控输入', '模型窗口', '依据'].map(t => h('th', null, t)))),
                h('tbody', null, IDS.map(id => {
                    const p = cap.models[id];
                    const status = p?.window_status ?? p?.status ?? 'UNKNOWN';
                    return h('tr', null, h('td', null, label(id)), h('td', { class: 'num' }, fmtNum(p?.initial_budget_tokens)),
                        h('td', { class: 'num' }, fmtNum(p?.backend_window_tokens)),
                        h('td', null, h('span', { class: 'cap ' + (status === 'UNKNOWN' ? 'warn' : 'yes'), title: p?.window_source || '' }, status)));
                }))),
            h('ul', { class: 'hint list' },
                h('li', null, '档位是输入上限，不是每轮都要填满的目标。'),
                h('li', null, '容量按 UTF-8 字节数保守估算，不是精确 Token 计数；图片会另外占用本轮余量。'),
                h('li', null, '依据为 UNKNOWN 的模型使用本地保护值，实际容量未经验证，不随所选档位增大。')));
    };
    await load();
    box.append(section('上下文容量档位', h('div', { class: 'radios' }, radios), custom,
        h('div', { class: 'card-actions' }, button('保存档位', async () => {
            const mode = box.querySelector('input[name=ctx-mode]:checked').value;
            await roomApi(room, '', { context_mode: mode, context_limit_tokens: mode === 'custom' ? Number(custom.value) : null });
            await refreshRooms(); await load(); toast('上下文档位已保存');
        }, 'primary'))), table);
}

// ---------- summary / rolling memory ----------
const SECTIONS = { question: '讨论背景与当前问题', positions: '各模型立场', verified: '有来源的主张（自动摘要不代表已核实）',
    unverified: '未核查的主张', disagreements: '未解决的分歧', changes: '立场变化' };
const JOB = { running: '正在生成', completed: '已完成', failed: '失败', cancelled: '已取消' };

async function summary(room, box) {
    const [versions, memory] = await Promise.all([roomApi(room, '/summaries'), roomApi(room, '/memory')]);
    // Same rule as the server's SummaryStore.active: the newest confirmed/automatic version, never a newer draft.
    const active = [...versions].reverse().find(v => ['confirmed', 'automatic'].includes(v.status));
    const latest = versions.at(-1);
    const job = memory.job || {};
    box.append(section('自动摘要',
        h('p', { class: 'modal-text' }, memory.enabled ? '已开启' : '已暂停', job.state ? ` · 最近一次：${JOB[job.state] || job.state}` : ' · 尚未触发',
            job.state === 'failed' && job.reason ? h('span', { class: 'err-text' }, `（${job.reason}）`) : null),
        h('p', { class: 'hint' }, '公开讨论变长后，后台会自动整理较早的公开历史，聊天不会被阻塞；私聊内容不会进入摘要，原始消息始终保留。'),
        h('div', { class: 'card-actions' },
            button(memory.enabled ? '暂停自动摘要' : '开启自动摘要', async () => { await roomApi(room, '/memory', { enabled: !memory.enabled }); render(); }),
            job.state === 'running' ? button('停止当前摘要任务', async () => { await roomApi(room, '/memory', {}); render(); }) : null)));

    const view = h('div');
    const show = version => {
        if (!version) { view.replaceChildren(h('p', { class: 'hint' }, '目前没有生效的摘要。模型直接读取近期公开历史。')); return; }
        const ids = version.source_ids || [];
        view.replaceChildren(
            h('div', { class: 'sum-head' }, h('strong', null, `v${version.version}`),
                h('span', { class: 'cap ' + (version.status === 'confirmed' ? 'yes' : 'warn') }, version.status === 'confirmed' ? '已人工确认' : version.status === 'automatic' ? '自动生成 · 未经人工确认' : '草稿（未生效）'),
                version.stale ? h('span', { class: 'cap no' }, '来源已被编辑，摘要失效') : null),
            h('p', { class: 'hint' }, `覆盖 ${ids.length} 条公开消息${ids.length ? `（#${Math.min(...ids)} – #${Math.max(...ids)}）` : ''} · ${version.author || ''}`),
            ...Object.entries(SECTIONS).map(([key, title]) => h('div', { class: 'sum-sec' + (['disagreements', 'changes'].includes(key) ? ' key' : '') },
                h('h4', null, title), h('p', null, version.sections?.[key] || '（空）'))));
    };
    show(active);
    box.append(section('当前生效的摘要', view,
        latest && latest !== active && latest.status === 'draft' ? h('p', { class: 'hint' }, `另有未确认的草稿 v${latest.version}，尚未生效。`) : null,
        h('p', { class: 'hint' }, '格式校验通过不代表内容正确：模型可能把分歧写成共识。请重点检查“未解决的分歧”和“立场变化”。')));

    const base = latest?.status === 'draft' ? latest : active;
    const fields = Object.fromEntries(Object.keys(SECTIONS).map(key => [key, h('textarea', { class: 'input area small', value: base?.sections?.[key] || '' })]));
    const ids = h('input', { class: 'input', placeholder: '例如 12,13,15', value: (base?.source_ids || []).join(',') });
    const save = confirm => async () => {
        const source_ids = ids.value.split(/[,，\s]+/).filter(Boolean).map(Number);
        if (source_ids.some(n => !Number.isInteger(n))) throw new Error('覆盖的消息 ID 需为整数。');
        const sections = Object.fromEntries(Object.entries(fields).filter(([, f]) => f.value.trim()).map(([k, f]) => [k, f.value.trim()]));
        await roomApi(room, '/summaries', { source_ids, sections, confirm }); toast(confirm ? '摘要已确认并生效' : '草稿已保存（未生效）'); render();
    };
    box.append(h('details', { class: 'p-section' }, h('summary', null, '修订摘要'),
        Object.entries(SECTIONS).map(([key, title]) => h('label', { class: 'field' }, title, fields[key])),
        h('label', { class: 'field' }, '覆盖的公开消息 ID', ids),
        h('div', { class: 'card-actions' }, button('保存草稿', save(false)), button('确认并生效', save(true), 'primary'))));

    box.append(h('details', { class: 'p-section' }, h('summary', null, `全部版本（${versions.length}）`),
        [...versions].reverse().map(v => h('div', { class: 'row' },
            h('span', null, `v${v.version} · ${{ confirmed: '已确认', automatic: '自动', draft: '草稿' }[v.status] || v.status}${v === active ? ' · 生效中' : ''}${v.stale ? ' · 已失效' : ''}`),
            h('span', { class: 'grow' }),
            h('button', { class: 'btn small', onclick: () => show(v) }, '查看'),
            v === active || v.status === 'draft' ? null : button('恢复', async () => { await roomApi(room, '/summaries', { restore: v.version }); toast(`已恢复摘要 v${v.version}`); render(); }))),
        h('p', { class: 'hint' }, '恢复会追加一个新的已确认版本；之后迟到的后台摘要结果不会覆盖你的选择。')));
}

// ---------- call audit ----------
const TASK = { running: '进行中', completed: '完成', failed: '失败', cancelled: '已停止' };
async function calls(room, box) {
    const tasks = (await roomApi(room, '/tasks')).reverse();
    box.append(h('div', { class: 'card-actions' }, button('停止本聊天全部生成', () => stopTask(room)), button('刷新', render)),
        h('p', { class: 'hint' }, '每次调用模型的原始记录（范围、容量、用量）。想看某个回答读了哪些材料，点“查看这次回答的依据”。估算值不是服务商的实际计费；缺失的字段显示为 UNKNOWN。'));
    if (!tasks.length) box.append(h('p', { class: 'hint' }, '本聊天还没有模型调用。'));
    for (const task of tasks.slice(0, 30)) {
        const u = v => v ?? 'UNKNOWN';
        const range = v => Array.isArray(v) ? (v.length ? v.map(r => Array.isArray(r) ? r.join('–') : r).join(', ') : '无') : u(v);
        const rows = [
            ['针对提问', `#${task.current_id}`], ['可见范围', task.visibility === 'public' || !task.visibility ? '公开' : task.visibility.map(label).join('、')],
            ['回答方式', { normal: '独立回答', strict: '严格独立' }[task.independent_mode] || '普通讨论'], ['连接', task.backend], ['研究视角', task.research_lens?.name || '开放科学讨论'],
            ['请求模型 / effort', `${u(task.requested_model)} / ${u(task.requested_effort)}`], ['服务端实际模型', u(task.actual_model)],
            ['输入估算 / 容量', `${fmtNum(task.input_estimate)} / ${fmtNum(task.budget)} ${task.budget_unit || 'bytes'}`],
            ['文字 UTF-8 字节', fmtNum(task.input_bytes)], ['摘要版本', u(task.summary_version)],
            ['快照截止消息', u(task.snapshot_cutoff)], ['送入初始输入的消息', range(task.included_message_ranges ?? task.message_ids)],
            ['额外引用', range(task.reference_ids)], ['因容量被裁剪', range(task.trimmed_history_ranges)], ['由摘要覆盖', range(task.summarized_history_ranges)],
            ['图片传输字节', u(task.image_transport_bytes)], ['图片 Token 预留', u(task.image_token_allowance)],
            ['实际用量（服务返回）', typeof (task.usage_fields ?? task.usage) === 'object' ? JSON.stringify(task.usage_fields ?? task.usage) : u(task.usage_fields ?? task.usage)],
            ['重新生成自', task.regenerated_from != null ? `#${task.regenerated_from}` : '—'], ['任务 ID', task.task_id],
        ];
        const item = h('details', { class: 'call ' + task.state, open: task.task_id === focusTask },
            h('summary', null, tile(task.identity || task.model, 'xs'), h('strong', null, label(task.identity || task.model)),
                h('span', { class: 'cap ' + ({ completed: 'yes', failed: 'no', running: 'warn' }[task.state] || '') }, TASK[task.state] || task.state),
                h('span', { class: 'grow' }), h('span', { class: 'hint' }, task.created_at ? fmtTime(task.created_at) : '')),
            h('div', { class: 'card-actions' }, h('button', { class: 'btn small', onclick: () => openMaterials(task.task_id, task.identity || task.model) }, '查看这次回答的依据')),
            task.reason && task.state === 'failed' ? h('p', { class: 'msg-note error' }, friendlyReason(task.reason)) : null,
            task.reason ? h('p', { class: 'hint' }, '原始记录：' + task.reason) : null,
            h('dl', { class: 'kv' }, rows.map(([k, v]) => [h('dt', null, k), h('dd', null, String(v))])),
            h('details', null, h('summary', null, '原始记录（JSON）'), h('pre', { class: 'raw' }, JSON.stringify(task, null, 2))));
        box.append(item);
        if (task.task_id === focusTask) setTimeout(() => item.scrollIntoView({ block: 'nearest' }), 50);
    }
    focusTask = null;
}

export function initPanel() {
    document.getElementById('panel-toggle').addEventListener('click', () => togglePanel());
    on('panel', data => { if (data.tab) { tab = data.tab; store.set('panel-tab2', tab); } focusTask = data.task || null; togglePanel(true); });
    on('room', render);
    on('library', id => { if (tab === 'library' && !panel().hidden && activeRoom()?.id === id && !panel().contains(document.activeElement)) render(); });
    on('rooms', () => { if (!panel().hidden && tab === 'discussion' && !panel().contains(document.activeElement)) render(); });
    document.getElementById('pins-btn').addEventListener('click', () => emit('panel', { tab: 'discussion' }));
    on('tasks', channel => { if (tab === 'calls' && channel === S.active && !panel().hidden && !panel().querySelector('details.call[open]')) render(); });
    if (store.get('panel-open', false) && window.innerWidth >= 1200) togglePanel(true);
}
