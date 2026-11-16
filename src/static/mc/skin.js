// Two visual modes over one DOM: 表版 "共鸣" (expressive) and 里版 "纸本" (focus).
// This module owns the skin/theme attributes and the switch choreography; the expressive skin's 3D stage lives in scene.js.
import { store } from './util.js';
import { S, on, emit, isAI } from './state.js';
import { initStage, setStage, refreshStage, kick } from './scene.js';

const root = document.documentElement;
export const skin = () => root.dataset.skin || 'expressive';
export const reducedMotion = () => root.dataset.motion === 'off' || (root.dataset.motion !== 'full' && matchMedia('(prefers-reduced-motion: reduce)').matches);
const systemLight = matchMedia('(prefers-color-scheme: light)');

export function applyAppearance() {
    root.dataset.skin = store.get('skin', 'expressive');
    // 里版 theme: light / dark / follow the system (attribute absent).
    const theme = store.get('theme', 'system');
    if (theme === 'system') delete root.dataset.theme; else root.dataset.theme = theme;
    // 表版 theme, chosen separately; always resolved to light or dark here so the styles and the stage need one switch only.
    const expressive = store.get('x-theme', 'dark');
    root.dataset.xtheme = expressive === 'system' ? (systemLight.matches ? 'light' : 'dark') : expressive;
    root.dataset.motion = store.get('motion', 'full');
    root.style.setProperty('--font-scale', store.get('font-scale', 1));
    root.dataset.width = store.get('reading-width', 'normal');
    setStage(skin() === 'expressive');
    refreshStage();
}

/** Run a DOM change as a view transition when the browser supports it and motion is allowed. */
export function transition(change) {
    if (!document.startViewTransition || reducedMotion()) { change(); return null; }
    try { return document.startViewTransition(change); } catch (e) { change(); return null; }
}

/** Switch skin with a circular reveal from the control that was pressed. Scroll anchors are restored by the panes. */
export function setSkin(next, origin) {
    if (next === skin()) return;
    emit('before-skin');
    const apply = () => { store.set('skin', next); applyAppearance(); emit('skin', next); };
    if (!document.startViewTransition || reducedMotion()) { apply(); return; }
    const rect = origin?.getBoundingClientRect();
    const x = rect ? rect.left + rect.width / 2 : innerWidth / 2, y = rect ? rect.top + rect.height / 2 : innerHeight / 2;
    const radius = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y));
    root.classList.add('skin-switch');
    const view = document.startViewTransition(apply);
    view.ready.then(() => root.animate(
        { clipPath: [`circle(0px at ${x}px ${y}px)`, `circle(${radius}px at ${x}px ${y}px)`] },
        { duration: 720, easing: 'cubic-bezier(.3,.7,.1,1)', pseudoElement: '::view-transition-new(root)' })).catch(() => {});
    view.finished.finally(() => root.classList.remove('skin-switch'));
}

export function initSkin() {
    initStage(document.getElementById('field'));
    applyAppearance();
    systemLight.addEventListener('change', applyAppearance);
    for (const name of ['tasks', 'models', 'skin', 'rooms']) on(name, refreshStage);
    on('room', () => kick('room'));
    on('message', msg => { if (S.historyReady && msg.channel === S.active && isAI(msg.sender)) kick('message'); });
    // Pane count changes (1 / 2 / 3 columns): the stage swings with the layout.
    const panes = document.getElementById('panes');
    if (panes) {
        let count = panes.dataset.count;
        new MutationObserver(() => { if (panes.dataset.count !== count) { kick('layout', panes.dataset.count > count ? 1 : -1); count = panes.dataset.count; setTimeout(refreshStage, 400); } })
            .observe(panes, { attributes: true, attributeFilter: ['data-count'] });
    }
    new ResizeObserver(refreshStage).observe(document.getElementById('chat'));
    const button = document.getElementById('skin-switch');
    button.addEventListener('click', () => setSkin(skin() === 'expressive' ? 'focus' : 'expressive', button));
}
