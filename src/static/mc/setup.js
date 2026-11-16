// First start: a welcome page that offers the two ways in (add a real API connection, or try the demo chat) and the
// local user guides. Shown while no connection can be used yet; "稍后" hides it until the user opens it from 设置 → 帮助.
import { h, api, modal, toast, TOKEN } from './util.js';
import { S, emit, refreshRooms, openRoom } from './state.js';

export function openGuide(key) {
    window.open(`/api/chattera/guide/${key}?token=${encodeURIComponent(TOKEN)}`, '_blank', 'noopener');
}

export async function startDemo() {
    const room = await api('/api/chattera/demo', {});
    await refreshRooms();
    openRoom(room.channel);
    toast('已打开演示聊天：这里的回答是预先写好的示例，不调用任何模型，也不会产生费用。', 'info', 7000);
}

export function showWelcome(info, again = false) {
    const card = (title, text, button, action, primary = false) => h('div', { class: 'welcome-card' + (primary ? ' primary' : '') },
        h('h3', null, title), h('p', null, text), h('button', { class: 'btn' + (primary ? ' primary' : ''), onclick: action }, button));
    let dialog = null;
    const finish = async () => { try { await api('/api/chattera/setup/done', {}); } catch (e) { /* shown again next time */ } };
    const body = h('div', { class: 'welcome' },
        h('p', { class: 'welcome-tag' }, 'A group chat for you and your AIs.'),
        h('p', { class: 'welcome-lead' }, '把多个 AI 模型请进同一个对话：自然地交谈，听听不同的看法，让讨论继续下去。'),
        h('div', { class: 'welcome-cards' },
            card('先体验演示模式', '不需要任何账号或 Key。打开一个示例聊天，看看群聊、引用、多栏和画布是怎么用的。', '进入演示', async () => {
                dialog.close(); await finish(); try { await startDemo(); } catch (e) { toast(e.message, 'error'); } }, !info.connections.length),
            card('添加第一个 AI 模型', '从模型服务商网站拿到 API Key，在设置里添加一个连接，就可以开始真实的聊天。', '添加连接', async () => {
                dialog.close(); await finish(); emit('open-settings', 'models'); }),
            info.guides.start ? card('阅读使用指南', '一步一步的图文说明，适合第一次使用。也可以随时在左下角“帮助”里打开。', '打开指南', () => openGuide('start')) : null),
        h('p', { class: 'welcome-cost' }, 'Chattera 本身免费。使用真实模型时，由你选择的模型服务商按用量收费；演示模式不产生任何费用。'),
        h('p', { class: 'welcome-where' }, '你的聊天和设置只保存在这台电脑上：', h('code', null, info.home)));
    dialog = modal({ title: again ? '欢迎使用 Chattera' : '欢迎使用 Chattera', body, wide: true, onclose: () => { if (!again) finish(); } });
    dialog.el.classList.add('welcome-modal');
    const cancel = dialog.el.querySelector('.modal-foot .btn');
    if (cancel) cancel.textContent = '稍后再说';
}

export async function initSetup() {
    try {
        const info = await api('/api/chattera/setup');
        S.setup = info;
        if (info.needs_setup) showWelcome(info);
    } catch (e) { /* setup status is optional */ }
}
