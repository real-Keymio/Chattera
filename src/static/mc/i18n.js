// Interface language. Chinese source text is the key (gettext style): t('新聊天') returns the English entry of
// i18n/en.json when the language is English, otherwise the Chinese text itself. {0}, {1}… are filled from the
// following arguments. The server decides the language (config\settings.json "language", default: the system
// language) and passes it in window.__CHATTERA_LANG__; changing it reloads the page.

export const LANG = window.__CHATTERA_LANG__ === 'en' ? 'en' : 'zh';
const DICT = LANG === 'en' ? await fetch('/static/mc/i18n/en.json?v=' + (window.__CHATTERA_VERSION__ || '')).then(r => r.json()).catch(() => ({})) : {};

export function t(text, ...args) {
    const out = (LANG === 'en' && DICT[text]) || text;
    return args.length ? out.replace(/\{(\d+)\}/g, (all, i) => (args[i] === undefined || args[i] === null ? '' : String(args[i]))) : out;
}

/** Translate the static text of index.html (text nodes and title / placeholder / aria-label). */
export function translatePage(root = document.body) {
    document.documentElement.lang = LANG === 'en' ? 'en' : 'zh-CN';
    if (LANG !== 'en') return;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
        const text = node.nodeValue.trim();
        if (text && DICT[text]) node.nodeValue = node.nodeValue.replace(text, DICT[text]);
    }
    for (const el of root.querySelectorAll('[title],[placeholder],[aria-label]')) {
        for (const name of ['title', 'placeholder', 'aria-label']) {
            const value = el.getAttribute(name);
            if (value && DICT[value]) el.setAttribute(name, DICT[value]);
        }
    }
}
