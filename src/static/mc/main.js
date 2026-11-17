// Chattera UI entry point.
import { h, icon, ICONS, toast, menu } from './util.js';
import { S, IDS, on, emit, label, color, sigil, activeRoom, connect, loadBackends, loadModels, runningTasks, capsOf, lensOf, effectiveModel, effortName } from './state.js';
import { initPanes, stopTask, setPaneCount, jumpToLatest, focusParticipant } from './panes.js';
import { initComposer } from './composer.js';
import { initSidebar, renameRoom } from './sidebar.js';
import { initPanel } from './panel.js';
import { openSettings } from './studio.js';
import { initSkin } from './skin.js';
import { loadWorkspaces } from './workspace.js';
import { initSetup } from './setup.js';
import { t, translatePage } from './i18n.js';

const CONTEXT = { '100k': '100K', '256k': '256K', '1m': '1M', custom: t('自定义容量') };

function renderStage() {
    const room = activeRoom();
    document.getElementById('room-name').textContent = room ? room.name : 'Chattera';
    document.title = room ? `${room.name} · Chattera` : 'Chattera';
    const sub = document.getElementById('room-sub');
    const lens = room?.lens ? lensOf(room.lens) : null, pins = room?.pins?.length || 0;
    sub.replaceChildren(...(room ? [
        h('button', { title: t('研究视角（点击修改）'), onclick: () => emit('panel', { tab: 'discussion' }) }, lens ? lens.name : t('开放科学讨论')),
        h('button', { title: t('上下文容量档位（点击修改）'), onclick: () => emit('panel', { tab: 'context' }) }, t('上下文 ') + (CONTEXT[room.context_mode] || room.context_mode || '256K')),
        room.forked_from ? h('span', { title: t('从“{0}”的 #{1} 分支', room.forked_from.room_name || '', room.forked_from.message_id) }, t('分支')) : null,
    ].filter(Boolean) : []));
    const pinButton = document.getElementById('pins-btn');
    pinButton.querySelector('.count')?.remove();
    if (pins) pinButton.append(h('span', { class: 'count' }, pins));
    pinButton.hidden = !room;

    const running = room ? runningTasks(S.active) : [];
    document.getElementById('voices').classList.toggle('many', IDS.length > 4);
    document.getElementById('voices').replaceChildren(...(room ? IDS.map(id => {
        const task = running.find(x => x.identity === id), { caps } = capsOf(room, id), now = effectiveModel(room, id);
        const state = task ? (task.output_started_at ? 'writing' : 'thinking') : caps && !caps.available ? 'off' : 'idle';
        return h('button', { class: 'voice', dataset: { state }, style: { '--hue': color(id) },
            title: `${label(id)} · ${now.modelLabel || t('默认模型')}${now.effort ? ' · ' + effortName(now.effort) : ''}${state === 'off' ? t(' · 连接不可用') : ''}`,
            onclick: event => menu(Object.assign(event.currentTarget, { alignRight: true }), [
                { title: `${label(id)} · ${now.modelLabel || ''}` },
                { label: t('跳到它最近的回答'), icon: 'down', action: () => jumpToLatest(id) },
                { label: t('在当前视口聚焦它'), icon: 'eye', action: () => focusParticipant(id) },
                task ? { label: t('停止这次回复'), icon: 'stop', action: () => stopTask(room, id) } : null,
                'sep', { label: t('模型与推理强度…'), icon: 'cpu', action: () => emit('panel', { tab: 'models' }) },
            ]) },
            sigil(id), h('span', { class: 'voice-name' }, label(id)), h('span', { class: 'voice-state' }, task ? (state === 'writing' ? t('书写中') : t('思考中')) : ''));
    }) : []), running.length > 1 ? h('button', { class: 'btn small', onclick: () => stopTask(room) }, icon(ICONS.stop, 12), t('全部停止')) : '');
}

translatePage();
initSkin();
initSidebar();
await loadWorkspaces();   // per-room layouts live on the server; read before the panes are built
initPanes();
initComposer();
initPanel();
document.getElementById('open-settings').addEventListener('click', () => openSettings());
document.getElementById('open-help').addEventListener('click', () => openSettings('help'));
document.getElementById('room-name').addEventListener('click', () => { const room = activeRoom(); if (room) renameRoom(room); });
document.addEventListener('keydown', event => {
    if ((event.ctrlKey || event.metaKey) && !event.shiftKey && !event.altKey && ['1', '2', '3', '4', '5', '6'].includes(event.key)) { event.preventDefault(); setPaneCount(Number(event.key)); }
});
for (const name of ['room', 'rooms', 'tasks', 'models', 'backends']) on(name, renderStage);
on('error', text => { if (!activeRoom()) toast(text, 'error'); });
on('open-settings', section => openSettings(section));
renderStage();
loadBackends();
loadModels();
connect();
initSetup();
