// Attachment status and viewer. What is shown here is the local user's view; a model only receives what
// the frozen snapshot of its call contains (extracted text, OCR text, or native image input).
import { h, icon, ICONS, api, toast, modal, fmtSize, TOKEN } from './util.js';
import { activeRoom, emit } from './state.js';

const cache = new Map();   // url -> Promise<info>
const KIND = { pdf: 'PDF', docx: 'DOCX', text: 'TXT', markdown: 'MD', csv: 'CSV', json: 'JSON', xml: 'XML', latex: 'TeX', rtf: 'RTF', html: 'HTML' };
export const ACCEPT = '.png,.jpg,.jpeg,.webp,.pdf,.docx,.txt,.md,.rtf,.html,.htm,.csv,.json,.xml,.tex';
export const isImage = att => att.kind === 'image' || (!att.kind && /\.(png|jpe?g|webp)$/i.test(att.url || ''));
export const extOf = att => ((att.name || '').includes('.') ? att.name.split('.').pop() : 'file').toUpperCase().slice(0, 5);
export const validUrl = att => /^\/uploads\/[a-zA-Z0-9_.-]+$/.test(att?.url || '');

/** Parse (and OCR if needed) once per file; result says how the content can reach a model. */
export function inspect(att, room = activeRoom()) {
    if (!cache.has(att.url)) {
        cache.set(att.url, api(`/api/chat/rooms/${room.id}/attachments/inspect`, { url: att.url, name: att.name })
            .catch(error => { cache.delete(att.url); throw error; }));
    }
    return cache.get(att.url);
}

/** One-line status: what was extracted and by which method. */
export function summarize(info) {
    if (!info) return '';
    if (info.kind === 'image') return `图片 ${info.width}×${info.height} · 以原生图片提供给支持视觉的模型`;
    const parts = [];
    if (info.format === 'pdf' || info.unit_type === 'page') {
        parts.push(`${info.page_count ?? info.units} 页`);
        const ocr = info.ocr_pages?.length || 0, skipped = info.ocr_skipped_pages?.length || 0, blank = info.pages_without_text?.length || 0;
        if (!blank) parts.push('文本层');
        else if (ocr) parts.push(`文本层 + OCR ${ocr} 页`);
        else parts.push('文本层');
        if (skipped) parts.push(`${skipped} 页无文字且未识别`);
    } else {
        parts.push(KIND[info.format] || '文本');
        if (info.line_count) parts.push(`${info.line_count} 行`);
        else if (info.units) parts.push(`${info.units} 段`);
    }
    parts.push(`${(info.characters || 0).toLocaleString('en-US')} 字`);
    if (info.truncated) parts.push('超长，已截断');
    return parts.join(' · ');
}

export function fileGlyph(att) {
    return h('span', { class: 'att-ext', dataset: { ext: extOf(att).toLowerCase() } }, extOf(att));
}

async function blobUrl(path) {
    const response = await fetch(path, { headers: { 'X-Session-Token': TOKEN } });
    if (!response.ok) {
        let reason = '';
        try { reason = (await response.json()).error; } catch (e) { /* binary or empty */ }
        throw new Error(reason || `读取失败（${response.status}）`);
    }
    return URL.createObjectURL(await response.blob());
}

export async function download(att) {
    try {
        const url = await blobUrl(att.url);
        h('a', { href: url, download: att.name || 'attachment' }).click();
        setTimeout(() => URL.revokeObjectURL(url), 60000);
    } catch (e) { toast(e.message, 'error'); }
}

function imageViewer(att, siblings) {
    const images = (siblings || [att]).filter(isImage);
    let index = Math.max(0, images.findIndex(a => a.url === att.url));
    const img = h('img', { class: 'viewer-image', alt: '' });
    const caption = h('div', { class: 'viewer-caption' });
    const show = () => { img.src = images[index].url; img.alt = images[index].name || ''; caption.textContent = `${images[index].name || '图片'}${images.length > 1 ? ` · ${index + 1} / ${images.length}` : ''}`; };
    const step = d => { index = (index + d + images.length) % images.length; show(); };
    const dialog = modal({ title: '图片', wide: true, body: h('div', { class: 'viewer' },
        images.length > 1 ? h('button', { class: 'viewer-nav', 'aria-label': '上一张', onclick: () => step(-1) }, icon(ICONS.chevron, 20)) : null,
        h('div', { class: 'viewer-stage' }, img, caption),
        images.length > 1 ? h('button', { class: 'viewer-nav next', 'aria-label': '下一张', onclick: () => step(1) }, icon(ICONS.chevron, 20)) : null) });
    dialog.el.addEventListener('keydown', event => { if (event.key === 'ArrowLeft') step(-1); if (event.key === 'ArrowRight') step(1); });
    show();
}

function documentViewer(att, context) {
    const room = activeRoom();
    const head = h('div', { class: 'doc-head' }, fileGlyph(att), h('div', { class: 'doc-title' }, h('strong', null, att.name || '附件'),
        h('span', { class: 'doc-sub' }, '正在解析…')));
    const side = h('div', { class: 'doc-side' });
    const stage = h('div', { class: 'doc-stage' });
    const tools = h('div', { class: 'doc-tools' });
    const dialog = modal({ title: '附件', wide: true, body: h('div', { class: 'doc' }, head, h('div', { class: 'doc-body' }, side, h('div', { class: 'doc-main' }, tools, stage))) });
    dialog.el.classList.add('doc-modal');
    let unit = 1, view = 'page', info = null, token = 0;
    const isPdf = () => info?.format === 'pdf' || info?.unit_type === 'page';
    const unitName = () => isPdf() ? '页' : info?.unit_type === 'paragraph' ? '段' : '块';
    const sourceTag = number => !isPdf() ? null : info.ocr_pages?.includes(number) ? 'ocr' : info.ocr_skipped_pages?.includes(number) ? 'none' : info.pages_without_text?.includes(number) ? 'none' : 'text';
    const TAG = { ocr: 'OCR 识别', none: '无可提取文字', text: '文本层' };

    async function show() {
        const mine = ++token, total = info.units;
        side.querySelectorAll('button').forEach(b => b.classList.toggle('active', Number(b.dataset.unit) === unit));
        side.querySelector('button.active')?.scrollIntoView({ block: 'nearest' });
        const tag = sourceTag(unit);
        tools.replaceChildren(
            h('button', { class: 'icon-btn', disabled: unit <= 1, 'aria-label': '上一' + unitName(), onclick: () => { unit--; show(); } }, icon('m15 6-6 6 6 6', 18)),
            h('span', { class: 'doc-pos' }, `第 ${unit} / ${total} ${unitName()}`),
            h('button', { class: 'icon-btn', disabled: unit >= total, 'aria-label': '下一' + unitName(), onclick: () => { unit++; show(); } }, icon('m9 6 6 6-6 6', 18)),
            tag ? h('span', { class: 'cap ' + ({ ocr: 'warn', none: 'no', text: 'yes' }[tag]) }, TAG[tag]) : null,
            h('span', { class: 'grow' }),
            isPdf() ? h('div', { class: 'seg' }, [['page', '页面'], ['text', '提取的文字']].map(([key, text]) =>
                h('button', { class: view === key ? 'active' : '', onclick: () => { view = key; show(); } }, text))) : null,
            isPdf() ? h('button', { class: 'btn small', title: '把这一页渲染成图片加入输入框，支持视觉的模型可以直接看图表、公式和版式', onclick: attachPage }, icon(ICONS.plus, 13), '以图片附加本页') : null,
            h('button', { class: 'btn small', title: '在输入框中写入对这一位置的引用', onclick: cite }, icon(ICONS.reply, 13), `引用本${unitName()}`));
        stage.replaceChildren(h('p', { class: 'hint' }, '加载中…'));
        try {
            if (isPdf() && view === 'page') {
                const url = await blobUrl(`/api/chat/attachments/page?url=${encodeURIComponent(att.url)}&page=${unit}`);
                if (mine !== token) return;
                stage.replaceChildren(h('img', { class: 'doc-page', src: url, alt: `第 ${unit} 页` }));
            } else {
                const value = await api(`/api/chat/rooms/${room.id}/attachments/text?url=${encodeURIComponent(att.url)}&unit=${unit}`);
                if (mine !== token) return;
                stage.replaceChildren(
                    value.source === 'ocr' ? h('p', { class: 'msg-note warn' }, `这一页的文字来自 OCR 识别${value.ocr_confidence ? `（平均置信度 ${Math.round(value.ocr_confidence * 100)}%）` : ''}，可能有错字，公式和表格尤其不可靠。需要准确内容时请以页面图片为准。`) : null,
                    value.lines ? h('p', { class: 'hint' }, `原文第 ${value.lines[0]}–${value.lines[1]} 行`) : null,
                    value.text?.trim() ? h('pre', { class: 'doc-text' }, value.text)
                        : h('p', { class: 'hint' }, isPdf() ? '这一页没有可提取的文字（可能是扫描页或整页图表）。可以“以图片附加本页”，让支持视觉的模型直接阅读。' : '（空）'));
            }
        } catch (e) { if (mine === token) stage.replaceChildren(h('p', { class: 'form-error' }, e.message)); }
    }

    async function attachPage(event) {
        const button = event.currentTarget; button.disabled = true;
        try {
            const response = await fetch(`/api/chat/attachments/page?url=${encodeURIComponent(att.url)}&page=${unit}`, { headers: { 'X-Session-Token': TOKEN } });
            if (!response.ok) throw new Error('页面渲染失败');
            const base = (att.name || 'document').replace(/\.pdf$/i, '');
            emit('attach-files', [new File([await response.blob()], `${base} · 第${unit}页.png`, { type: 'image/png' })]);
            toast(`已把第 ${unit} 页作为图片加入输入框`);
        } catch (e) { toast(e.message, 'error'); } finally { button.disabled = false; }
    }
    function cite() {
        const where = isPdf() ? `第 ${unit} 页` : `第 ${unit} ${unitName()}`;
        emit('compose', { append: `关于附件《${att.name}》${where}：`, reply: context?.messageId != null ? { id: context.messageId, sender: context.sender, text: `附件 ${att.name}` } : undefined });
        dialog.close();
    }

    inspect(att, room).then(value => {
        info = value;
        head.querySelector('.doc-sub').textContent = summarize(info);
        if (info.kind !== 'text') { stage.replaceChildren(h('p', { class: 'hint' }, '没有可预览的文字内容。')); return; }
        const notes = [];
        if (info.ocr) notes.push(`第 ${info.ocr_pages.join('、')} 页没有文本层，文字由 OCR 识别，可能有误。`);
        if (info.ocr_skipped_pages?.length) notes.push(`第 ${info.ocr_skipped_pages.slice(0, 30).join('、')} 页没有可提取的文字，也未完成识别（${info.ocr_status}）。`);
        if (info.truncated) notes.push('文档很长，超出部分没有提取。');
        if (info.format_note) notes.push({ csv: '按原样保留每一行。', json: '按原样保留 JSON 文本。', xml: '按原样保留 XML 标记。', latex: '按原样保留 LaTeX 源码（含公式与注释）。', rtf: '由 RTF 转成纯文本，不含格式与图片。', html: '由 HTML 转成文本，保留标题、列表和表格单元格标记。' }[info.format] || '');
        if (isPdf()) notes.push('模型默认读取的是提取出的文字，看不到图表和版式；需要看图时请“以图片附加本页”。');
        if (notes.filter(Boolean).length) head.append(h('ul', { class: 'hint list doc-notes' }, notes.filter(Boolean).map(n => h('li', null, n))));
        if (info.outline?.length) {
            side.append(h('div', { class: 'doc-side-title' }, '目录'), ...info.outline.slice(0, 80).map(o =>
                h('button', { class: 'doc-outline', style: { paddingLeft: 10 + o.level * 12 + 'px' }, title: o.title, onclick: () => { unit = Math.min(info.units, o.page); show(); } },
                    h('span', null, o.title), h('span', { class: 'doc-pg' }, o.page))));
        }
        side.append(h('div', { class: 'doc-side-title' }, isPdf() ? '页面' : '分段'),
            h('div', { class: 'doc-pages' }, Array.from({ length: Math.min(info.units, 500) }, (_, i) => {
                const tag = sourceTag(i + 1);
                return h('button', { class: 'doc-pagebtn' + (tag && tag !== 'text' ? ' ' + tag : ''), dataset: { unit: i + 1 }, title: tag ? TAG[tag] : '', onclick: () => { unit = i + 1; show(); } }, i + 1);
            })));
        if (!info.units) { stage.replaceChildren(h('p', { class: 'hint' }, '没有提取到内容。')); return; }
        show();
    }).catch(error => {
        head.querySelector('.doc-sub').textContent = '解析失败';
        stage.replaceChildren(h('p', { class: 'form-error' }, `无法解析这个附件：${error.message}`),
            h('p', { class: 'hint' }, '文件本身仍已保存，可以下载查看。加密的 PDF、损坏的文件和旧版 .doc 不能解析。'));
    });
    dialog.el.querySelector('.modal-head h2').after(h('span', { class: 'grow' }),
        h('button', { class: 'btn small', onclick: () => download(att) }, '下载原文件'));
}

export function openAttachment(att, context = {}) {
    if (!validUrl(att)) return;
    if (isImage(att)) imageViewer(att, context.siblings); else documentViewer(att, context);
}
