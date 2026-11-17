"""Interface languages (src/static/mc/i18n.js, src/chattera_i18n.py, src/static/mc/i18n/en.json).

Chinese source text is the key. These checks keep the English dictionary complete and stop Chinese text from being
added outside t() / _() / N_(). Run `pytest tests/test_i18n.py -q`: a failure lists exactly what to add or wrap.
"""
import ast
import json
import re
from html.parser import HTMLParser
from pathlib import Path

import chattera_i18n

SRC = Path(__file__).resolve().parents[1] / 'src'
MC = SRC / 'static' / 'mc'
EN = json.loads((MC / 'i18n' / 'en.json').read_text('utf-8'))
CJK = re.compile('[\u3000-\u303f\u4e00-\u9fff\uff00-\uffef]')
HAN = re.compile('[\u4e00-\u9fff]')
JS_T = re.compile(r"(?<![\w$.])t\(\s*'((?:\\.|[^'\\])*)'")
PLACEHOLDER = re.compile(r'\{\d+\}')
# Chinese that is meant to stay: regexes that accept both languages, and Unicode ranges.
JS_ALLOWED = ('/^(新聊天|New chat)', '/^(检索证据|Retrieved evidence)', '一-鿿', '|服务地址/', '|输出/', '|超时/', '/窗口|window/')


def unescape(text):
    return re.sub(r"\\(.)", lambda m: {'n': '\n', 't': '\t'}.get(m.group(1), m.group(1)), text)


def js_files():
    return sorted(p for p in MC.glob('*.js') if p.name != 'i18n.js')


def py_files():
    return sorted(p for p in SRC.rglob('*.py') if p.name != 'chattera_i18n.py')


def collect():
    keys = {}
    def add(key, where):
        if CJK.search(key): keys.setdefault(key, where)
    for f in js_files():
        for m in JS_T.finditer(f.read_text('utf-8')): add(unescape(m.group(1)), f.name)
    for f in py_files():
        for node in ast.walk(ast.parse(f.read_text('utf-8'))):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ('_', 'N_') and node.args
                    and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
                add(node.args[0].value, f.name)

    class Page(HTMLParser):
        def handle_data(self, data):
            if data.strip(): add(data.strip(), 'index.html')
        def handle_starttag(self, tag, attrs):
            for name, value in attrs:
                if name in ('title', 'placeholder', 'aria-label') and value: add(value, 'index.html')
    Page().feed(re.sub(r'<script>.*?</script>', '', (MC / 'index.html').read_text('utf-8'), flags=re.S))
    for action in json.loads((SRC / 'static' / 'discussion-actions.json').read_text('utf-8')):
        add(action['label'], 'discussion-actions.json'); add(action['instruction'], 'discussion-actions.json')
    return keys


def test_every_source_text_has_an_english_entry():
    missing = {k: w for k, w in collect().items() if k not in EN}
    assert not missing, 'add to en.json:\n' + '\n'.join(f'{json.dumps(k, ensure_ascii=False)}  # {w}' for k, w in missing.items())


def test_dictionary_has_no_stale_or_broken_entries():
    used = collect()
    assert not [k for k in EN if k not in used], 'unused keys in en.json'
    for key, value in EN.items():
        assert value.strip() and not HAN.search(value), key
        assert sorted(PLACEHOLDER.findall(key)) == sorted(PLACEHOLDER.findall(value)) or key in BREAKS_ORDER, key


# Entries whose English drops a placeholder on purpose (none today); listed here so the choice is explicit.
BREAKS_ORDER = set()


def test_no_chinese_outside_translation_calls_in_frontend():
    leftovers = []
    for f in js_files():
        for n, line in enumerate(f.read_text('utf-8').splitlines(), 1):
            code = re.sub(r'//.*$', '', JS_T.sub('', line.replace("\\'", '')))
            if HAN.search(code) and not code.strip().startswith(('*', '/*')) and 'i18n-ignore' not in line and not any(a in line for a in JS_ALLOWED):
                leftovers.append(f'{f.name}:{n}: {line.strip()[:100]}')
    assert not leftovers, 'wrap in t():\n' + '\n'.join(leftovers)


def test_no_local_variable_named_t_hides_the_translator():
    """`.map(([v, t]) => … t('…'))` made t a string there and broke the effort picker; no file using t() may bind its own t."""
    shadow = re.compile(r"\(\[[^\]]*\bt\b[^\]]*\]\)\s*=>|\(\s*t\s*[,)]\s*=>|(?<![\w$.])t\s*=>|\b(?:const|let|var)\s+t\b")
    found = []
    for f in js_files():
        text = f.read_text('utf-8')
        if "from './i18n.js'" not in text: continue
        for n, line in enumerate(text.splitlines(), 1):
            if shadow.search(JS_T.sub('', line)):
                found.append(f'{f.name}:{n}: {line.strip()[:100]}')
    assert not found, 'rename these local `t` bindings:\n' + '\n'.join(found)


def test_no_chinese_outside_translation_calls_in_server():
    leftovers = []
    for f in py_files():
        tree = ast.parse(f.read_text('utf-8'))
        parents = {c: p for p in ast.walk(tree) for c in ast.iter_child_nodes(p)}
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and HAN.search(node.value):
                parent = parents.get(node)
                if isinstance(parent, ast.Expr): continue  # docstrings
                if isinstance(parent, ast.Call) and isinstance(parent.func, ast.Name) and parent.func.id in ('_', 'N_'): continue
                if isinstance(parent, ast.Dict) and node in parent.keys: continue
                if f.name == 'chattera_setup.py' and node.value.endswith('.pdf'): continue  # guide file names
                leftovers.append(f'{f.relative_to(SRC)}:{node.lineno}: {node.value[:60]!r}')
    assert not leftovers, 'wrap in _() or N_():\n' + '\n'.join(leftovers)


def test_translation_function_and_language_setting(monkeypatch, tmp_path):
    monkeypatch.setattr(chattera_i18n, 'system_language', lambda: 'en')
    assert chattera_i18n.configure('zh') == 'zh' and chattera_i18n._('名称不能为空。') == '名称不能为空。'
    assert chattera_i18n.configure('en') == 'en' and chattera_i18n._('名称不能为空。') == EN['名称不能为空。']
    assert chattera_i18n._('新聊天 {0}', 3) == 'New chat 3'
    assert chattera_i18n._('not in the dictionary {0}', 'x') == 'not in the dictionary x'
    assert chattera_i18n.configure('auto') == 'en' and chattera_i18n.configure('fr') == 'en' and chattera_i18n.setting() == 'auto'
    chattera_i18n.configure('zh')
