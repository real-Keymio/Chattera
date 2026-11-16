// Attachment status and viewer. What is shown here is the local user's view; a model only receives what
// the frozen snapshot of its call contains (extracted text, OCR text, or native image input).
import { h, icon, ICONS, api, toast, modal, fmtSize, TOKEN } from './util.js';
import { activeRoom, emit } from './state.js';
import { t } from './i18n.js';

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
    if (info.kind === 'image') return t('图片 {0}×{1} · 以原生图片提供给支持视觉的模型', info.width, info.height);
    const parts = [];
    if (info.format === 'pdf' || info.unit_type === 'page') {
        parts.push(t('{0} 页', info.page_count ?? info.units));
        const ocr = info.ocr_pages?.length || 0, skipped = info.ocr_skipped_pages?.length || 0, blank = info.pages_without_text?.length || 0;
        if (!blank) parts.push(t('文本层'));
        else if (ocr) parts.push(t('文本层 + OCR {0} 页', ocr));
        else parts.push(t('文本层'));
        if (skipped) parts.push(t('{0} 页无文字且未识别', skipped));
    } else {
        parts.push(KIND[info.format] || t('文本'));
        if (info.line_count) parts.push(t('{0} 行', info.line_count));
        else if (info.units) parts.push(t('{0} 段', info.units));
    }
    parts.push(t('{0} 字', (info.characters || 0).toLocaleString('en-US')));
    if (info.truncated) parts.push(t('超长，已截断'));
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
        throw new Error(reason || t('读取失败（{0}）', response.status));
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
    const show = () => { img.src = images[index].url; img.alt = images[index].name || ''; caption.textContent = `${images[index].name || t('图片')}${images.length > 1 ? ` · ${index + 1} / ${images.length}` : ''}`; };
    const step = d => { index = (index + d + images.length) % images.length; show(); };
    const dialog = modal({ title: t('图片'), wide: true, body: h('div', { class: 'viewer' },
        images.length > 1 ? h('button', { class: 'viewer-nav', 'aria-label': t('上一张'), onclick: () => step(-1) }, icon(ICONS.chevron, 20)) : null,
        h('div', { class: 'viewer-stage' }, img, caption),
        images.length > 1 ? h('button', { class: 'viewer-nav next', 'aria-label': t('下一张'), onclick: () => step(1) }, icon(ICONS.chevron, 20)) : null) });
    dialog.el.addEventListener('keydown', event => { if (event.key === 'ArrowLeft') step(-1); if (event.key === 'ArrowRight') step(1); });
    show();
}

function documentViewer(att, context) {
    const room = activeRoom();
    const head = h('div', { class: 'doc-head' }, fileGlyph(att), h('div', { class: 'doc-title' }, h('strong', null, att.name || t('附件')),
        h('span', { class: 'doc-sub' }, t('正在解析…'))));
    const side = h('div', { class: 'doc-side' });
    const stage = h('div', { class: 'doc-stage' });
    const tools = h('div', { class: 'doc-tools' });
    const dialog = modal({ title: t('附件'), wide: true, body: h('div', { class: 'doc' }, head, h('div', { class: 'doc-body' }, side, h('div', { class: 'doc-main' }, tools, stage))) });
    dialog.el.classList.add('doc-modal');
    let unit = 1, view = 'page', info = null, token = 0;
    const isPdf = () => info?.format === 'pdf' || info?.unit_type === 'page';
    const unitName = () => isPdf() ? t('页') : info?.unit_type === 'paragraph' ? t('段') : t('块');
    const sourceTag = number => !isPdf() ? null : info.ocr_pages?.includes(number) ? 'ocr' : info.ocr_skipped_pages?.includes(number) ? 'none' : info.pages_without_text?.includes(number) ? 'none' : 'text';
    const TAG = { ocr: t('OCR 识别'), none: t('无可提取文字'), text: t('文本层') };

    async function show() {
        const mine = ++token, total = info.units;
        side.querySelectorAll('button').forEach(b => b.classList.toggle('active', Number(b.dataset.unit) === unit));
        side.querySelector('button.active')?.scrollIntoView({ block: 'nearest' });
        const tag = sourceTag(unit);
        tools.replaceChildren(
            h('button', { class: 'icon-btn', disabled: unit <= 1, 'aria-label': t('上一') + unitName(), onclick: () => { unit--; show(); } }, icon('m15 6-6 6 6 6', 18)),
            h('span', { class: 'doc-pos' }, t('第 {0} / {1} {2}', unit, total, unitName())),
            h('button', { class: 'icon-btn', disabled: unit >= total, 'aria-label': t('下一') + unitName(), onclick: () => { unit++; show(); } }, icon('m9 6 6 6-6 6', 18)),
            tag ? h('span', { class: 'cap ' + ({ ocr: 'warn', none: 'no', text: 'yes' }[tag]) }, TAG[tag]) : null,
            h('span', { class: 'grow' }),
            isPdf() ? h('div', { class: 'seg' }, [['page', t('页面')], ['text', t('提取的文字')]].map(([key, text]) =>
                h('button', { class: view === key ? 'active' : '', onclick: () => { view = key; show(); } }, text))) : null,
            isPdf() ? h('button', { class: 'btn small', title: t('把这一页渲染成图片加入输入框，支持视觉的模型可以直接看图表、公式和版式'), onclick: attachPage }, icon(ICONS.plus, 13), t('以图片附加本页')) : null,
            h('button', { class: 'btn small', title: t('在输入框中写入对这一位置的引用'), onclick: cite }, icon(ICONS.reply, 13), t('引用本{0}', unitName())));
        stage.replaceChildren(h('p', { class: 'hint' }, t('加载中…')));
        try {
            if (isPdf() && view === 'page') {
                const url = await blobUrl(`/api/chat/attachments/page?url=${encodeURIComponent(att.url)}&page=${unit}`);
                if (mine !== token) return;
                stage.replaceChildren(h('img', { class: 'doc-page', src: url, alt: t('第 {0} 页', unit) }));
            } else {
                const value = await api(`/api/chat/rooms/${room.id}/attachments/text?url=${encodeURIComponent(att.url)}&unit=${unit}`);
                if (mine !== token) return;
                stage.replaceChildren(
                    value.source === 'ocr' ? h('p', { class: 'msg-note warn' }, t('这一页的文字来自 OCR 识别{0}，可能有错字，公式和表格尤其不可靠。需要准确内容时请以页面图片为准。', value.ocr_confidence ? t('（平均置信度 {0}%）', Math.round(value.ocr_confidence * 100)) : '')) : null,
                    value.lines ? h('p', { class: 'hint' }, t('原文第 {0}–{1} 行', value.lines[0], value.lines[1])) : null,
                    value.text?.trim() ? h('pre', { class: 'doc-text' }, value.text)
                        : h('p', { class: 'hint' }, isPdf() ? t('这一页没有可提取的文字（可能是扫描页或整页图表）。可以“以图片附加本页”，让支持视觉的模型直接阅读。') : t('（空）')));
            }
        } catch (e) { if (mine === token) stage.replaceChildren(h('p', { class: 'form-error' }, e.message)); }
    }

    async function attachPage(event) {
        const button = event.currentTarget; button.disabled = true;
        try {
            const response = await fetch(`/api/chat/attachments/page?url=${encodeURIComponent(att.url)}&page=${unit}`, { headers: { 'X-Session-Token': TOKEN } });
            if (!response.ok) throw new Error(t('页面渲染失败'));
            const base = (att.name || 'document').replace(/\.pdf$/i, '');
            emit('attach-files', [new File([await response.blob()], t('{0} · 第{1}页.png', base, unit), { type: 'image/png' })]);
            toast(t('已把第 {0} 页作为图片加入输入框', unit));
        } catch (e) { toast(e.message, 'error'); } finally { button.disabled = false; }
    }
    function cite() {
        const where = isPdf() ? t('第 {0} 页', unit) : t('第 {0} {1}', unit, unitName());
        emit('compose', { append: t('关于附件《{0}》{1}：', att.name, where), reply: context?.messageId != null ? { id: context.messageId, sender: context.sender, text: t('附件 {0}', att.name) } : undefined });
        dialog.close();
    }

    inspect(att, room).then(value => {
        info = value;
        head.querySelector('.doc-sub').textContent = summarize(info);
        if (info.kind !== 'text') { stage.replaceChildren(h('p', { class: 'hint' }, t('没有可预览的文字内容。'))); return; }
        const notes = [];
        if (info.ocr) notes.push(t('第 {0} 页没有文本层，文字由 OCR 识别，可能有误。', info.ocr_pages.join(t('、'))));
        if (info.ocr_skipped_pages?.length) notes.push(t('第 {0} 页没有可提取的文字，也未完成识别（{1}）。', info.ocr_skipped_pages.slice(0, 30).join(t('、')), info.ocr_status));
        if (info.truncated) notes.push(t('文档很长，超出部分没有提取。'));
        if (info.format_note) notes.push({ csv: t('按原样保留每一行。'), json: t('按原样保留 JSON 文本。'), xml: t('按原样保留 XML 标记。'), latex: t('按原样保留 LaTeX 源码（含公式与注释）。'), rtf: t('由 RTF 转成纯文本，不含格式与图片。'), html: t('由 HTML 转成文本，保留标题、列表和表格单元格标记。') }[info.format] || '');
        if (isPdf()) notes.push(t('模型默认读取的是提取出的文字，看不到图表和版式；需要看图时请“以图片附加本页”。'));
        if (notes.filter(Boolean).length) head.append(h('ul', { class: 'hint list doc-notes' }, notes.filter(Boolean).map(n => h('li', null, n))));
        if (info.outline?.length) {
            side.append(h('div', { class: 'doc-side-title' }, t('目录')), ...info.outline.slice(0, 80).map(o =>
                h('button', { class: 'doc-outline', style: { paddingLeft: 10 + o.level * 12 + 'px' }, title: o.title, onclick: () => { unit = Math.min(info.units, o.page); show(); } },
                    h('span', null, o.title), h('span', { class: 'doc-pg' }, o.page))));
        }
        side.append(h('div', { class: 'doc-side-title' }, isPdf() ? t('页面') : t('分段')),
            h('div', { class: 'doc-pages' }, Array.from({ length: Math.min(info.units, 500) }, (_, i) => {
                const tag = sourceTag(i + 1);
                return h('button', { class: 'doc-pagebtn' + (tag && tag !== 'text' ? ' ' + tag : ''), dataset: { unit: i + 1 }, title: tag ? TAG[tag] : '', onclick: () => { unit = i + 1; show(); } }, i + 1);
            })));
        if (!info.units) { stage.replaceChildren(h('p', { class: 'hint' }, t('没有提取到内容。'))); return; }
        show();
    }).catch(error => {
        head.querySelector('.doc-sub').textContent = t('解析失败');
        stage.replaceChildren(h('p', { class: 'form-error' }, t('无法解析这个附件：{0}', error.message)),
            h('p', { class: 'hint' }, t('文件本身仍已保存，可以下载查看。加密的 PDF、损坏的文件和旧版 .doc 不能解析。')));
    });
    dialog.el.querySelector('.modal-head h2').after(h('span', { class: 'grow' }),
        h('button', { class: 'btn small', onclick: () => download(att) }, t('下载原文件')));
}

export function openAttachment(att, context = {}) {
    if (!validUrl(att)) return;
    if (isImage(att)) imageViewer(att, context.siblings); else documentViewer(att, context);
}
