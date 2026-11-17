// Room details drawer: per-room settings and diagnostics, hidden by default.
// Global provider/profile/key matters live in settings.js, not here.
import { h, icon, ICONS, api, toast, modal, actionButton, fmtTime, fmtNum, store } from './util.js';
import { S, IDS, ALL, on, emit, label, color, sigil, tile, activeRoom, refreshRooms, loadModels, lensOf } from './state.js';
import { renameRoom, archiveRoom, deleteRoom } from './sidebar.js';
import { stopTask, friendlyReason, scrollToMessage } from './panes.js';
import { roomModels } from './models.js';
import { libraryTab } from './library.js';
import { openMaterials } from './evidence.js';
import { t } from './i18n.js';

const TABS = [['discussion', t('讨论')], ['library', t('资料')], ['models', t('模型')], ['context', t('上下文')], ['summary', t('记忆')], ['calls', t('记录')]];
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
    const body = h('div', { class: 'p-body' }, h('p', { class: 'hint' }, t('加载中…')));
    el.replaceChildren(
        h('div', { class: 'p-head' }, h('h2', null, room ? room.name : t('房间')), h('button', { class: 'icon-btn', 'aria-label': t('关闭'), onclick: () => togglePanel(false) }, icon(ICONS.close))),
        h('div', { class: 'tabs', role: 'tablist' }, TABS.map(([key, text]) => h('button', { class: 'tab' + (key === tab ? ' active' : ''), role: 'tab',
            'aria-selected': String(key === tab), onclick: () => { tab = key; store.set('panel-tab2', key); render(); } }, text))), body);
    if (!room) { body.replaceChildren(h('p', { class: 'hint' }, t('请先选择一个聊天。'))); return; }
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
        try { await roomApi(room, '/lens', { lens: shared.value }); await refreshRooms(); toast(t('房间的分析框架已更新')); } catch (e) { toast(e.message, 'error'); }
        describe();
    });
    describe();
    const per = room.lens_by_model || {};
    const rows = IDS.map(id => {
        const pick = h('select', { class: 'input' }, h('option', { value: '' }, t('跟随房间')), lenses.map(l => h('option', { value: l.id, selected: per[id] === l.id }, l.name)));
        pick.addEventListener('change', async () => {
            const next = { ...(activeRoom()?.lens_by_model || {}) };
            if (pick.value) next[id] = pick.value; else delete next[id];
            try { await roomApi(room, '/lens', { lens_by_model: next }); await refreshRooms(); } catch (e) { toast(e.message, 'error'); }
        });
        return h('div', { class: 'who-row' }, h('label', null, sigil(id, 'xs'), label(id)), h('div', { class: 'inline' }, pick,
            h('button', { class: 'btn small', title: t('查看 {0} 在这个聊天里实际收到的固定说明', label(id)), onclick: () => effectivePrompt(room, id) }, t('查看'))));
    });
    box.append(section(t('分析框架'), h('label', { class: 'field' }, t('所有参与者共用'), shared), about,
        h('details', null, h('summary', null, t('让不同参与者使用不同视角')), rows,
            h('p', { class: 'hint' }, t('例如同一篇论文让 Claude 看研究设计、Codex 看统计推断。这只是这个聊天里的分工，随时可以改；单次提问还可以在输入区临时换视角。'))),
        h('p', { class: 'hint' }, t('分析框架是分析方法，不预设结论，也不要求固定的回答格式。新增或修改框架：设置 → 分析框架。'))));

    const pins = room.pins || [];
    box.append(section(t('固定内容（{0}）', pins.length),
        pins.length ? pins.map(pin => h('div', { class: 'pin-item' }, icon(ICONS.pin, 15),
            h('div', { style: { flex: 1, minWidth: 0 } }, h('p', null, pin.text),
                h('div', { class: 'pin-src' }, pin.author ? [ALL.includes(pin.author) ? sigil(pin.author, 'xs') : null, ALL.includes(pin.author) ? label(pin.author) : pin.author] : t('手动添加'),
                    pin.source_message_id != null ? h('button', { class: 'link', onclick: () => scrollToMessage(pin.source_message_id) }, t('来源 #{0}', pin.source_message_id)) : null)),
            h('button', { class: 'icon-btn tiny', title: t('取消固定'), onclick: async () => { try { await api(`/api/chat/rooms/${room.id}/pins/${pin.id}`, undefined, 'DELETE'); await refreshRooms(); } catch (e) { toast(e.message, 'error'); } } }, icon(ICONS.close, 13))))
            : h('p', { class: 'hint' }, t('还没有固定内容。在任意公开消息的“更多”里选择“固定到房间背景”，或在下面直接写一条。')),
        (() => {
            const input = h('textarea', { class: 'input area small', placeholder: t('写下一条研究前提、已确认的结论或约束…'), maxLength: 2000 });
            return h('div', null, input, h('div', { class: 'card-actions' }, button(t('固定这段文字'), async () => {
                if (!input.value.trim()) throw new Error(t('请先写下要固定的内容。'));
                await roomApi(room, '/pins', { text: input.value.trim() }); await refreshRooms(); toast(t('已固定'));
            })));
        })(),
        h('p', { class: 'hint' }, t('固定内容会作为房间背景带进之后每一次提问（公开与私聊都会），并保留来源。最多 50 条。'))));

    box.append(section(t('这个聊天'),
        h('dl', { class: 'kv' }, h('dt', null, t('名称')), h('dd', null, room.name),
            room.forked_from ? [h('dt', null, t('分支自')), h('dd', null, `${room.forked_from.room_name || ''} #${room.forked_from.message_id}`)] : null,
            h('dt', null, t('创建时间')), h('dd', null, room.created_at ? new Date(room.created_at * 1000).toLocaleString('zh-CN') : t('未知')),
            h('dt', null, t('固定 ID')), h('dd', null, h('code', null, room.id))),
        h('div', { class: 'card-actions' }, h('button', { class: 'btn', onclick: () => renameRoom(room) }, t('重命名')),
            h('button', { class: 'btn', onclick: () => archiveRoom(room) }, t('归档')),
            h('button', { class: 'btn', title: t('参与者在这个聊天里保存的笔记、表格和下载的论文'), onclick: () => api(`/api/chat/rooms/${room.id}/files/open`, {}).catch(e => toast(e.message, 'error')) }, t('打开聊天文件夹')),
            h('button', { class: 'btn danger', onclick: () => deleteRoom(room) }, t('永久删除…'))),
        h('p', { class: 'hint' }, t('重命名只改变显示名称。归档会隐藏聊天并停止生成，可随时恢复。永久删除会清除这个聊天的全部数据，无法恢复；从它建立的分支是独立副本，不受影响。'))));
}

async function effectivePrompt(room, id) {
    const dialog = modal({ title: t('{0} 在这个聊天里收到的固定说明', label(id)), wide: true, body: h('p', { class: 'hint' }, t('读取中…')) });
    try {
        const value = await api(`/api/chat/rooms/${room.id}/effective-prompt?model=${id}`);
        dialog.body.replaceChildren(
            h('h4', { class: 'mat-sec' }, t('身份与讨论方式（所有聊天相同）')), h('div', { class: 'lens-method' }, value.identity),
            h('h4', { class: 'mat-sec', style: { marginTop: '14px' } }, t('分析框架')), value.lens ? h('div', { class: 'lens-method' }, t('【{0}】\n', value.lens.name) + value.lens.method) : h('p', { class: 'hint' }, t('开放科学讨论：不附加额外的方法说明。')),
            value.pins?.length ? [h('h4', { class: 'mat-sec', style: { marginTop: '14px' } }, t('固定内容')), h('div', { class: 'lens-method' }, value.pins.map(p => '· ' + p.text).join('\n'))] : null,
            (value.global_rules?.rules || []).length ? [h('h4', { class: 'mat-sec', style: { marginTop: '14px' } }, t('全局规则')), h('div', { class: 'lens-method' }, value.global_rules.rules.map(r => '· ' + (r.text || r)).join('\n'))] : null,
            h('p', { class: 'hint' }, t('除此之外，每次提问还会带上当时的历史、摘要、附件和检索资料；单次提问临时选择的视角不在这里显示，可在回答下方的“依据”里看到。')));
    } catch (e) { dialog.body.replaceChildren(h('p', { class: 'form-error' }, e.message)); }
}

async function models(room, box) { await roomModels(room, box, render); }

// ---------- context capacity ----------
async function context(room, box) {
    const legacy = { standard: 20000, long: 100000, maximum: 1000000 };
    const current = room.context_mode in legacy ? 'custom' : room.context_mode || '256k';
    const custom = h('input', { class: 'input', type: 'number', min: 1, max: 1000000, step: 1, value: legacy[room.context_mode] || room.context_limit_tokens || 262144 });
    const radios = [['100k', '100K'], ['256k', t('256K（默认）')], ['1m', '1M'], ['custom', t('自定义')]].map(([value, text]) =>
        h('label', { class: 'radio' }, h('input', { type: 'radio', name: 'ctx-mode', value, checked: value === current, onchange: () => { custom.hidden = value !== 'custom'; } }), text));
    custom.hidden = current !== 'custom';
    const table = h('div');
    const load = async () => {
        const cap = await roomApi(room, '/context-capacity');
        table.replaceChildren(h('p', { class: 'hint' }, t('所选上限：{0} Token。下表是每个模型实际生效的可控输入容量。', fmtNum(cap.selected_limit_tokens))),
            h('table', { class: 'grid' }, h('thead', null, h('tr', null, [t('模型'), t('实际可控输入'), t('模型窗口'), t('依据')].map(head => h('th', null, head)))),
                h('tbody', null, IDS.map(id => {
                    const p = cap.models[id];
                    const status = p?.window_status ?? p?.status ?? 'UNKNOWN';
                    return h('tr', null, h('td', null, label(id)), h('td', { class: 'num' }, fmtNum(p?.initial_budget_tokens)),
                        h('td', { class: 'num' }, fmtNum(p?.backend_window_tokens)),
                        h('td', null, h('span', { class: 'cap ' + (status === 'UNKNOWN' ? 'warn' : 'yes'), title: p?.window_source || '' }, status)));
                }))),
            h('ul', { class: 'hint list' },
                h('li', null, t('档位是输入上限，不是每轮都要填满的目标。')),
                h('li', null, t('容量按 UTF-8 字节数保守估算，不是精确 Token 计数；图片会另外占用本轮余量。')),
                h('li', null, t('依据为 UNKNOWN 的模型使用本地保护值，实际容量未经验证，不随所选档位增大。'))));
    };
    await load();
    box.append(section(t('上下文容量档位'), h('div', { class: 'radios' }, radios), custom,
        h('div', { class: 'card-actions' }, button(t('保存档位'), async () => {
            const mode = box.querySelector('input[name=ctx-mode]:checked').value;
            await roomApi(room, '', { context_mode: mode, context_limit_tokens: mode === 'custom' ? Number(custom.value) : null });
            await refreshRooms(); await load(); toast(t('上下文档位已保存'));
        }, 'primary'))), table);
}

// ---------- summary / rolling memory ----------
const SECTIONS = { question: t('讨论背景与当前问题'), positions: t('各模型立场'), verified: t('有来源的主张（自动摘要不代表已核实）'),
    unverified: t('未核查的主张'), disagreements: t('未解决的分歧'), changes: t('立场变化') };
const JOB = { running: t('正在生成'), completed: t('已完成'), failed: t('失败'), cancelled: t('已取消') };

async function summary(room, box) {
    const [versions, memory] = await Promise.all([roomApi(room, '/summaries'), roomApi(room, '/memory')]);
    // Same rule as the server's SummaryStore.active: the newest confirmed/automatic version, never a newer draft.
    const active = [...versions].reverse().find(v => ['confirmed', 'automatic'].includes(v.status));
    const latest = versions.at(-1);
    const job = memory.job || {};
    box.append(section(t('自动摘要'),
        h('p', { class: 'modal-text' }, memory.enabled ? t('已开启') : t('已暂停'), job.state ? t(' · 最近一次：{0}', JOB[job.state] || job.state) : t(' · 尚未触发'),
            job.state === 'failed' && job.reason ? h('span', { class: 'err-text' }, t('（{0}）', job.reason)) : null),
        h('p', { class: 'hint' }, t('公开讨论变长后，后台会自动整理较早的公开历史，聊天不会被阻塞；私聊内容不会进入摘要，原始消息始终保留。')),
        h('div', { class: 'card-actions' },
            button(memory.enabled ? t('暂停自动摘要') : t('开启自动摘要'), async () => { await roomApi(room, '/memory', { enabled: !memory.enabled }); render(); }),
            job.state === 'running' ? button(t('停止当前摘要任务'), async () => { await roomApi(room, '/memory', {}); render(); }) : null)));

    const view = h('div');
    const show = version => {
        if (!version) { view.replaceChildren(h('p', { class: 'hint' }, t('目前没有生效的摘要。模型直接读取近期公开历史。'))); return; }
        const ids = version.source_ids || [];
        view.replaceChildren(
            h('div', { class: 'sum-head' }, h('strong', null, `v${version.version}`),
                h('span', { class: 'cap ' + (version.status === 'confirmed' ? 'yes' : 'warn') }, version.status === 'confirmed' ? t('已人工确认') : version.status === 'automatic' ? t('自动生成 · 未经人工确认') : t('草稿（未生效）')),
                version.stale ? h('span', { class: 'cap no' }, t('来源已被编辑，摘要失效')) : null),
            h('p', { class: 'hint' }, t('覆盖 {0} 条公开消息{1} · {2}', ids.length, ids.length ? t('（#{0} – #{1}）', Math.min(...ids), Math.max(...ids)) : '', version.author || '')),
            ...Object.entries(SECTIONS).map(([key, title]) => h('div', { class: 'sum-sec' + (['disagreements', 'changes'].includes(key) ? ' key' : '') },
                h('h4', null, title), h('p', null, version.sections?.[key] || t('（空）')))));
    };
    show(active);
    box.append(section(t('当前生效的摘要'), view,
        latest && latest !== active && latest.status === 'draft' ? h('p', { class: 'hint' }, t('另有未确认的草稿 v{0}，尚未生效。', latest.version)) : null,
        h('p', { class: 'hint' }, t('格式校验通过不代表内容正确：模型可能把分歧写成共识。请重点检查“未解决的分歧”和“立场变化”。'))));

    const base = latest?.status === 'draft' ? latest : active;
    const fields = Object.fromEntries(Object.keys(SECTIONS).map(key => [key, h('textarea', { class: 'input area small', value: base?.sections?.[key] || '' })]));
    const ids = h('input', { class: 'input', placeholder: t('例如 12,13,15'), value: (base?.source_ids || []).join(',') });
    const save = confirm => async () => {
        const source_ids = ids.value.split(/[,，\s]+/).filter(Boolean).map(Number);
        if (source_ids.some(n => !Number.isInteger(n))) throw new Error(t('覆盖的消息 ID 需为整数。'));
        const sections = Object.fromEntries(Object.entries(fields).filter(([, f]) => f.value.trim()).map(([k, f]) => [k, f.value.trim()]));
        await roomApi(room, '/summaries', { source_ids, sections, confirm }); toast(confirm ? t('摘要已确认并生效') : t('草稿已保存（未生效）')); render();
    };
    box.append(h('details', { class: 'p-section' }, h('summary', null, t('修订摘要')),
        Object.entries(SECTIONS).map(([key, title]) => h('label', { class: 'field' }, title, fields[key])),
        h('label', { class: 'field' }, t('覆盖的公开消息 ID'), ids),
        h('div', { class: 'card-actions' }, button(t('保存草稿'), save(false)), button(t('确认并生效'), save(true), 'primary'))));

    box.append(h('details', { class: 'p-section' }, h('summary', null, t('全部版本（{0}）', versions.length)),
        [...versions].reverse().map(v => h('div', { class: 'row' },
            h('span', null, `v${v.version} · ${{ confirmed: t('已确认'), automatic: t('自动'), draft: t('草稿') }[v.status] || v.status}${v === active ? t(' · 生效中') : ''}${v.stale ? t(' · 已失效') : ''}`),
            h('span', { class: 'grow' }),
            h('button', { class: 'btn small', onclick: () => show(v) }, t('查看')),
            v === active || v.status === 'draft' ? null : button(t('恢复'), async () => { await roomApi(room, '/summaries', { restore: v.version }); toast(t('已恢复摘要 v{0}', v.version)); render(); }))),
        h('p', { class: 'hint' }, t('恢复会追加一个新的已确认版本；之后迟到的后台摘要结果不会覆盖你的选择。'))));
}

// ---------- call audit ----------
const TASK = { running: t('进行中'), completed: t('完成'), failed: t('失败'), cancelled: t('已停止') };
async function calls(room, box) {
    const tasks = (await roomApi(room, '/tasks')).reverse();
    box.append(h('div', { class: 'card-actions' }, button(t('停止本聊天全部生成'), () => stopTask(room)), button(t('刷新'), render)),
        h('p', { class: 'hint' }, t('每次调用模型的原始记录（范围、容量、用量）。想看某个回答读了哪些材料，点“查看这次回答的依据”。估算值不是服务商的实际计费；缺失的字段显示为 UNKNOWN。')));
    if (!tasks.length) box.append(h('p', { class: 'hint' }, t('本聊天还没有模型调用。')));
    for (const task of tasks.slice(0, 30)) {
        const u = v => v ?? 'UNKNOWN';
        const range = v => Array.isArray(v) ? (v.length ? v.map(r => Array.isArray(r) ? r.join('–') : r).join(', ') : t('无')) : u(v);
        const rows = [
            [t('针对提问'), `#${task.current_id}`], [t('可见范围'), task.visibility === 'public' || !task.visibility ? t('公开') : task.visibility.map(label).join(t('、'))],
            [t('模型之间'), { normal: t('背靠背回答'), strict: t('严格背靠背') }[task.independent_mode] || t('回答互相可见')], [t('连接'), task.backend], [t('分析框架'), task.research_lens?.name || t('开放科学讨论')],
            [t('请求模型 / effort'), `${u(task.requested_model)} / ${u(task.requested_effort)}`], [t('服务端实际模型'), u(task.actual_model)],
            [t('输入估算 / 容量'), `${fmtNum(task.input_estimate)} / ${fmtNum(task.budget)} ${task.budget_unit || 'bytes'}`],
            [t('文字 UTF-8 字节'), fmtNum(task.input_bytes)], [t('摘要版本'), u(task.summary_version)],
            [t('快照截止消息'), u(task.snapshot_cutoff)], [t('送入初始输入的消息'), range(task.included_message_ranges ?? task.message_ids)],
            [t('额外引用'), range(task.reference_ids)], [t('因容量被裁剪'), range(task.trimmed_history_ranges)], [t('由摘要覆盖'), range(task.summarized_history_ranges)],
            [t('图片传输字节'), u(task.image_transport_bytes)], [t('图片 Token 预留'), u(task.image_token_allowance)],
            [t('实际用量（服务返回）'), typeof (task.usage_fields ?? task.usage) === 'object' ? JSON.stringify(task.usage_fields ?? task.usage) : u(task.usage_fields ?? task.usage)],
            [t('重新生成自'), task.regenerated_from != null ? `#${task.regenerated_from}` : '—'], [t('任务 ID'), task.task_id],
        ];
        const item = h('details', { class: 'call ' + task.state, open: task.task_id === focusTask },
            h('summary', null, tile(task.identity || task.model, 'xs'), h('strong', null, label(task.identity || task.model)),
                h('span', { class: 'cap ' + ({ completed: 'yes', failed: 'no', running: 'warn' }[task.state] || '') }, TASK[task.state] || task.state),
                h('span', { class: 'grow' }), h('span', { class: 'hint' }, task.created_at ? fmtTime(task.created_at) : '')),
            h('div', { class: 'card-actions' }, h('button', { class: 'btn small', onclick: () => openMaterials(task.task_id, task.identity || task.model) }, t('查看这次回答的依据'))),
            task.reason && task.state === 'failed' ? h('p', { class: 'msg-note error' }, friendlyReason(task.reason)) : null,
            task.reason ? h('p', { class: 'hint' }, t('原始记录：') + task.reason) : null,
            h('dl', { class: 'kv' }, rows.map(([k, v]) => [h('dt', null, k), h('dd', null, String(v))])),
            h('details', null, h('summary', null, t('原始记录（JSON）')), h('pre', { class: 'raw' }, JSON.stringify(task, null, 2))));
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
