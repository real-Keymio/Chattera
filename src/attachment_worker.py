"""Bounded parser subprocess. No network, macros, links or document instructions executed.

Usage: attachment_worker.py <path> <dependency-dir> [<json options>]
Options: {"mode": "parse" | "page", "page": int, "ocr_seconds": float, "ocr_pages": int}
"""
import json
import re
import sys
import time
import zipfile
from pathlib import Path

LIMIT = 400000          # extracted characters per document
BLOCK = 1600            # characters per line-block unit for line-oriented formats
TEXT_FORMATS = {'.txt': 'text', '.md': 'markdown', '.csv': 'csv', '.json': 'json', '.xml': 'xml',
                '.tex': 'latex', '.rtf': 'rtf', '.html': 'html', '.htm': 'html'}


def decode(raw):
    """Text files are not always UTF-8 on this machine; only two documented encodings are tried."""
    for encoding in ('utf-8-sig', 'gb18030'):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise ValueError('Text encoding is neither UTF-8 nor GB18030')


def blocks(lines):
    """Group consecutive lines into readable units while keeping exact line numbers for citation."""
    units, current, size, start, remaining = [], [], 0, 1, LIMIT
    def flush(end):
        nonlocal current, size, remaining
        if not current: return
        text = '\n'.join(current)[:remaining]; remaining -= len(text)
        units.append({'unit': len(units) + 1, 'lines': [start, end], 'text': text})
        current, size = [], 0
    number = 0
    for number, line in enumerate(lines, 1):
        if remaining <= 0: break
        if size + len(line) > BLOCK and current:
            flush(number - 1)
        if not current: start = number
        current.append(line); size += len(line) + 1
    flush(number)
    return units, remaining <= 0


def rtf_text(source):
    """Minimal RTF reader: keeps paragraphs, tabs, Unicode and code-page characters; drops formatting groups."""
    skip_groups = {'fonttbl', 'colortbl', 'stylesheet', 'info', 'pict', 'object', 'header', 'footer', 'generator',
                   'datastore', 'themedata', 'colorschememapping', 'latentstyles', 'listtable', 'listoverridetable',
                   'rsidtbl', 'xmlnstbl', 'fldinst', 'filetbl', 'revtbl', 'footnote', 'annotation', 'mmathPr'}
    codepage = 'cp1252'
    found = re.search(r'\\ansicpg(\d+)', source)
    if found:
        try: ''.encode('cp' + found[1]); codepage = 'cp' + found[1]
        except LookupError: pass
    out, stack, ignorable, uc, skip, pending = [], [], False, 1, 0, bytearray()
    def flush():
        if pending:
            out.append(bytes(pending).decode(codepage, 'replace')); pending.clear()
    pattern = re.compile(r"\\([a-z]{1,32})(-?\d{1,10})?[ ]?|\\'([0-9a-fA-F]{2})|\\([^a-z])|([{}])|[\r\n]+|(.)", re.I | re.S)
    for match in pattern.finditer(source):
        word, arg, hexa, char, brace, plain = match.groups()
        if brace:
            flush(); skip = 0
            if brace == '{': stack.append((uc, ignorable))
            elif stack: uc, ignorable = stack.pop()
        elif char:
            flush(); skip = 0
            if char == '~' and not ignorable: out.append('\u00a0')
            elif char in '{}\\' and not ignorable: out.append(char)
            elif char == '*': ignorable = True
        elif word:
            flush(); skip = 0
            if word in skip_groups: ignorable = True
            elif ignorable: pass
            elif word in ('par', 'line', 'sect', 'page', 'row'): out.append('\n')
            elif word in ('tab', 'cell'): out.append('\t')
            elif word == 'uc': uc = int(arg or 1)
            elif word == 'u' and arg:
                code = int(arg)
                out.append(chr(code + 65536 if code < 0 else code)); skip = uc
        elif hexa:
            if skip > 0: skip -= 1
            elif not ignorable: pending.append(int(hexa, 16))
        elif plain:
            flush()
            if skip > 0: skip -= 1
            elif not ignorable: out.append(plain)
    flush()
    return ''.join(out)


def html_text(raw):
    """Readable text with heading, list and table structure kept as plain-text markers. Scripts/styles dropped."""
    from lxml import html as lxml_html
    root = lxml_html.fromstring(raw)
    for bad in root.xpath('//script|//style|//noscript|//template|//svg|//head/*[not(self::title)]'):
        bad.drop_tree()
    lines = []
    title = root.xpath('string(//title)').strip()
    if title: lines.append('# ' + title)
    block = {'p', 'div', 'section', 'article', 'li', 'tr', 'br', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'pre', 'blockquote',
             'figcaption', 'caption', 'dt', 'dd', 'table', 'ul', 'ol', 'header', 'footer', 'main', 'aside', 'nav'}
    current = []
    def end():
        text = re.sub(r'[ \t\u00a0]+', ' ', ''.join(current)).strip()
        if text: lines.append(text)
        current.clear()
    def walk(node):
        tag = node.tag if isinstance(node.tag, str) else ''
        if tag == 'title' or not tag: return
        if tag in block: end()
        if re.fullmatch(r'h[1-6]', tag): current.append('#' * int(tag[1]) + ' ')
        elif tag == 'li': current.append('- ')
        elif tag in ('figcaption', 'caption'): current.append('[caption] ')
        if node.text: current.append(node.text)
        for child in node:
            walk(child)
            if child.tag in ('td', 'th'): current.append(' | ')
            if child.tail: current.append(child.tail)
        if tag in block: end()
    walk(root); end()
    return '\n'.join(lines)


def ocr_engine():
    from rapidocr_onnxruntime import RapidOCR
    return RapidOCR()


def ocr_image(engine, image):
    import numpy
    result, _ = engine(numpy.asarray(image.convert('RGB')))
    if not result: return '', None
    boxes = sorted(((min(p[1] for p in box), max(p[1] for p in box), min(p[0] for p in box), text, float(score))
                    for box, text, score in result), key=lambda b: b[0])
    # Reading order: boxes whose vertical centre falls inside the current line share that line, left to right.
    lines, line, line_bottom = [], [], None
    for top, bottom, left, text, score in boxes:
        if line and (top + bottom) / 2 > line_bottom:
            lines.append(' '.join(t for _, t in sorted(line))); line = []
        if not line: line_bottom = bottom
        line.append((left, text))
    if line: lines.append(' '.join(t for _, t in sorted(line)))
    return '\n'.join(lines), round(sum(b[4] for b in boxes) / len(boxes), 3)


def render_page(path, number, scale=2.0):
    import pypdfium2
    pdf = pypdfium2.PdfDocument(str(path))
    try:
        if not 1 <= number <= len(pdf): raise ValueError('Page out of range')
        page = pdf[number - 1]
        width, height = page.get_size()
        scale = min(scale, 2400 / max(width, height, 1))
        return page.render(scale=scale).to_pil()
    finally:
        pdf.close()


def parse_pdf(path, options):
    from pypdf import PdfReader
    reader = PdfReader(path)
    if reader.is_encrypted or len(reader.pages) > 500: raise ValueError('Encrypted PDF or page limit')
    units, remaining, empty = [], LIMIT, []
    for number, page in enumerate(reader.pages, 1):
        if remaining <= 0: break
        text = (page.extract_text() or '')[:remaining]; remaining -= len(text)
        units.append({'unit': number, 'text': text, 'source': 'text-layer'})
        # A page whose text layer is (nearly) empty is most likely a scan or a full-page figure.
        if len(text.strip()) < 24: empty.append(number)
    outline = []
    def collect(items, depth=0):
        for item in items:
            if isinstance(item, list): collect(item, depth + 1)
            elif len(outline) < 120:
                try: outline.append({'title': str(item.title)[:160], 'page': reader.get_destination_page_number(item) + 1, 'level': depth})
                except Exception: pass
    try: collect(reader.outline)
    except Exception: pass
    info = {'kind': 'text', 'unit_type': 'page', 'units': units, 'truncated': remaining <= 0, 'ocr': False, 'format': 'pdf',
            'page_count': len(reader.pages), 'outline': outline, 'pages_without_text': empty,
            'ocr_pages': [], 'ocr_skipped_pages': [], 'ocr_status': 'not needed'}
    if not empty: return info
    deadline = time.monotonic() + float(options.get('ocr_seconds', 0))
    budget = int(options.get('ocr_pages', 0))
    if budget <= 0 or deadline <= time.monotonic():
        info['ocr_skipped_pages'] = empty; info['ocr_status'] = 'skipped: no OCR budget in this call'
        return info
    try:
        engine = ocr_engine()
    except Exception:
        info['ocr_skipped_pages'] = empty; info['ocr_status'] = 'unavailable: OCR engine not installed'
        return info
    scores = []
    for number in empty:
        if len(info['ocr_pages']) >= budget or time.monotonic() >= deadline or remaining <= 0:
            info['ocr_skipped_pages'].append(number); continue
        try:
            text, score = ocr_image(engine, render_page(path, number))
        except Exception:
            info['ocr_skipped_pages'].append(number); continue
        text = text[:remaining]; remaining -= len(text)
        unit = units[number - 1]
        if text.strip():
            unit.update(text=text, source='ocr', ocr_confidence=score); info['ocr_pages'].append(number)
            if score is not None: scores.append(score)
        else:
            unit['source'] = 'no-text'  # rendered and OCRed, nothing recognised: figure-only or blank page
    info['ocr'] = bool(info['ocr_pages'])
    info['ocr_engine'] = 'RapidOCR (onnxruntime); recognition may contain errors, especially in formulas and tables'
    info['ocr_mean_confidence'] = round(sum(scores) / len(scores), 3) if scores else None
    info['ocr_status'] = 'complete' if not info['ocr_skipped_pages'] else 'partial: page or time budget reached'
    info['truncated'] = remaining <= 0
    return info


def parse(path, options=None):
    options = options or {}
    ext = path.suffix.lower()
    if ext == '.pdf':
        return parse_pdf(path, options)
    if ext == '.docx':
        with zipfile.ZipFile(path) as z:
            if sum(i.file_size for i in z.infolist()) > 30_000_000 or len(z.infolist()) > 3000: raise ValueError('DOCX expansion limit')
        from docx import Document
        from docx.table import Table
        from docx.text.paragraph import Paragraph
        doc = Document(path)
        units, remaining = [], LIMIT
        # Body order with tables kept in place, so captions stay next to their table.
        for child in doc.element.body.iterchildren():
            if remaining <= 0: break
            if child.tag.endswith('}p'):
                paragraph = Paragraph(child, doc)
                text = paragraph.text
                style = (paragraph.style.name if paragraph.style is not None else '') or ''
                level = re.fullmatch(r'Heading (\d)', style)
                if level and text.strip(): text = '#' * int(level[1]) + ' ' + text
            elif child.tag.endswith('}tbl'):
                text = '[table]\n' + '\n'.join(' | '.join(cell.text.strip().replace('\n', ' ') for cell in row.cells)
                                               for row in Table(child, doc).rows)
            else:
                continue
            text = text[:remaining]; remaining -= len(text)
            units.append({'unit': len(units) + 1, 'text': text})
        return {'kind': 'text', 'unit_type': 'paragraph', 'units': units, 'truncated': remaining <= 0, 'format': 'docx'}
    if ext in TEXT_FORMATS:
        raw = path.read_bytes()
        fmt = TEXT_FORMATS[ext]
        if fmt == 'html':
            text = html_text(raw); encoding = 'html'
        else:
            text, encoding = decode(raw)
            if fmt == 'rtf': text = rtf_text(text)
        note = {'csv': 'rows kept verbatim; the first line is usually the header', 'json': 'original JSON text kept verbatim',
                'xml': 'original XML markup kept verbatim', 'latex': 'original LaTeX source kept verbatim, including math and comments',
                'rtf': 'text converted from RTF; formatting, images and embedded objects dropped',
                'html': 'text converted from HTML; headings, lists and table cells kept as markers; scripts and styles dropped'}.get(fmt, '')
        units, truncated = blocks(text.splitlines())
        return {'kind': 'text', 'unit_type': 'block', 'units': units, 'truncated': truncated, 'format': fmt,
                'line_count': len(text.splitlines()), 'encoding': encoding, 'format_note': note}
    if ext in ('.png', '.jpg', '.jpeg', '.webp'):
        from PIL import Image
        Image.MAX_IMAGE_PIXELS = 40_000_000
        with Image.open(path) as im:
            if im.format not in ('PNG', 'JPEG', 'WEBP') or im.width * im.height > 40_000_000: raise ValueError('Image limits')
            im.verify()
            return {'kind': 'image', 'media_type': Image.MIME[im.format], 'width': im.width, 'height': im.height}
    raise ValueError('Unsupported attachment format')


if __name__ == '__main__':
    # Dependency directory is server configuration, never model/file input.
    if len(sys.argv) > 2 and sys.argv[2]: sys.path.insert(0, sys.argv[2])
    try:
        options = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}
        if options.get('mode') == 'page':
            import base64, io
            if Path(sys.argv[1]).suffix.lower() != '.pdf': raise ValueError('Only PDF pages can be rendered')
            image = render_page(Path(sys.argv[1]), int(options['page']), float(options.get('scale', 2.0)))
            out = io.BytesIO(); image.convert('RGB').save(out, format='PNG', optimize=True)
            print(json.dumps({'width': image.width, 'height': image.height, 'png': base64.b64encode(out.getvalue()).decode()}))
        else:
            print(json.dumps(parse(Path(sys.argv[1]), options), ensure_ascii=True))
    except Exception:
        print(json.dumps({'error': 'Attachment parse failed or unsupported; no content extracted'})); sys.exit(1)
