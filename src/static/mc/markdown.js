// Markdown -> sanitized DOM, with code highlighting and LaTeX math.
// Model output is untrusted: raw HTML is escaped before parsing, then the parsed tree is reduced to an
// allow-list of elements, attributes and URL schemes. Math and highlighted code are produced by the
// vendored KaTeX / highlight.js from plain text, after sanitising.
import { h, icon, ICONS, toast } from './util.js';

const TAGS = new Set(['P', 'BR', 'STRONG', 'B', 'EM', 'I', 'DEL', 'S', 'CODE', 'PRE', 'BLOCKQUOTE', 'UL', 'OL', 'LI', 'A',
    'H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'HR', 'TABLE', 'THEAD', 'TBODY', 'TR', 'TH', 'TD', 'INPUT', 'SPAN', 'DIV', 'SUP', 'SUB']);
const SAFE_URL = /^(https?:|mailto:|#)/i;
const OPEN = '', CLOSE = '';

function clean(node) {
    for (const child of [...node.childNodes]) {
        if (child.nodeType === Node.TEXT_NODE) continue;
        if (child.nodeType !== Node.ELEMENT_NODE || !TAGS.has(child.tagName)) {
            child.replaceWith(document.createTextNode(child.textContent || ''));
            continue;
        }
        const keep = {};
        if (child.tagName === 'A') {
            const href = child.getAttribute('href') || '';
            if (SAFE_URL.test(href)) keep.href = href;
        }
        if (child.tagName === 'CODE') {
            const lang = /(?:^|\s)language-([\w+#-]{1,24})/.exec(child.getAttribute('class') || '');
            if (lang) keep['data-lang'] = lang[1];
        }
        if (child.tagName === 'TH' || child.tagName === 'TD') {
            const align = child.getAttribute('align');
            if (['left', 'right', 'center'].includes(align)) keep.align = align;
        }
        if (child.tagName === 'OL' && /^\d+$/.test(child.getAttribute('start') || '')) keep.start = child.getAttribute('start');
        if (child.tagName === 'INPUT') {
            if (child.getAttribute('type') !== 'checkbox') { child.remove(); continue; }
            keep.type = 'checkbox'; keep.disabled = '';
            if (child.hasAttribute('checked')) keep.checked = '';
        }
        for (const attr of [...child.attributes]) child.removeAttribute(attr.name);
        for (const [name, value] of Object.entries(keep)) child.setAttribute(name, value);
        if (child.tagName === 'A') { child.target = '_blank'; child.rel = 'noopener noreferrer'; }
        clean(child);
    }
}

function mathNodes(root, formulas) {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const targets = [];
    while (walker.nextNode()) {
        const node = walker.currentNode;
        if (node.nodeValue.includes(OPEN) && !node.parentElement.closest('code, pre')) targets.push(node);
    }
    for (const node of targets) {
        const parts = node.nodeValue.split(new RegExp(`${OPEN}(\\d+)${CLOSE}`));
        const fragment = document.createDocumentFragment();
        parts.forEach((part, i) => {
            if (i % 2 === 0) { if (part) fragment.append(part); return; }
            const { tex, display } = formulas[+part];
            const span = h('span', { class: display ? 'math display' : 'math', title: tex });
            try { window.katex.render(tex, span, { displayMode: display, throwOnError: false, strict: 'ignore', output: 'html' }); }
            catch (e) { span.textContent = (display ? '$$' : '$') + tex + (display ? '$$' : '$'); }
            fragment.append(span);
        });
        node.replaceWith(fragment);
    }
}

export function renderMarkdown(text) {
    const host = h('div', { class: 'md' });
    if (!window.marked) { host.classList.add('plain'); host.textContent = text; return host; }
    const code = [], formulas = [];
    let source = String(text).replace(/(```[\s\S]*?```|`[^`\n]+`)/g, match => { code.push(match); return `\u0000${code.length - 1}\u0000`; });
    if (window.katex) {
        const hold = (tex, display) => { formulas.push({ tex: tex.trim(), display }); return `${OPEN}${formulas.length - 1}${CLOSE}`; };
        source = source
            .replace(/\$\$([\s\S]+?)\$\$/g, (_, tex) => hold(tex, true))
            .replace(/\\\[([\s\S]+?)\\\]/g, (_, tex) => hold(tex, true))
            .replace(/\\\(([\s\S]+?)\\\)/g, (_, tex) => hold(tex, false))
            // Inline $…$: must hug its content and not look like a currency amount.
            .replace(/(?<![\\$\w])\$(?!\s)([^$\n]{1,600}?)(?<![\s\\])\$(?![\d$])/g, (_, tex) => hold(tex, false));
    }
    // Escape raw HTML, but keep leading ">" so Markdown block quotes still work.
    source = source.replace(/</g, '&lt;')
        .replace(/^([ \t]*(?:>[ \t]?)*)(.*)$/gm, (_, quote, rest) => quote + rest.replace(/>/g, '&gt;'))
        .replace(/\u0000(\d+)\u0000/g, (_, i) => code[+i]);
    const template = document.createElement('template');
    template.innerHTML = window.marked.parse(source, { breaks: true, gfm: true });
    clean(template.content);
    host.append(template.content);
    if (formulas.length) mathNodes(host, formulas);
    for (const pre of host.querySelectorAll('pre')) {
        const element = pre.querySelector('code');
        const raw = element ? element.textContent : pre.textContent;
        const lang = element?.dataset.lang || '';
        const block = h('code');
        if (window.hljs && lang && window.hljs.getLanguage(lang)) {
            try { block.innerHTML = window.hljs.highlight(raw, { language: lang, ignoreIllegals: true }).value; }
            catch (e) { block.textContent = raw; }
        } else block.textContent = raw;
        pre.replaceWith(h('div', { class: 'code-block' },
            h('div', { class: 'code-bar' }, h('span', null, lang || 'text'),
                h('button', { class: 'code-copy', type: 'button', onclick: () => copyText(raw) }, icon(ICONS.copy, 13), '复制')),
            h('pre', null, block)));
    }
    for (const table of host.querySelectorAll('table')) {
        const wrap = h('div', { class: 'table-wrap' });
        table.before(wrap); wrap.append(table);
    }
    return host;
}

export async function copyText(text) {
    try { await navigator.clipboard.writeText(text); toast('已复制'); }
    catch (e) { toast('复制失败，请手动选择文本复制。', 'error'); }
}
