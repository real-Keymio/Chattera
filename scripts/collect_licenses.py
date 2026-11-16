"""Collect third-party license information for everything the portable package ships.

Run with the build environment's Python:
    .venv\\Scripts\\python.exe scripts\\collect_licenses.py <output folder>
Writes <output>/THIRD_PARTY_NOTICES.txt and <output>/licenses/<package>/... (every license / NOTICE / AUTHORS file the
installed distribution carries). The Python packages are the dependency closure of requirements.txt + requirements-ocr.txt (what PyInstaller
bundles, OCR included); Python itself, the PyInstaller bootloader and the browser libraries in src/static/mc/vendor are added by hand.
"""
import re
import shutil
import sys
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(sys.argv[1]).resolve()
ROOTS = ['fastapi', 'uvicorn', 'mcp', 'httpx', 'python-multipart', 'packaging', 'pypdf', 'pypdfium2', 'python-docx', 'lxml', 'pillow', 'rapidocr-onnxruntime']
EXTRAS = {'uvicorn': {'standard'}}
LICENSE_FILE = re.compile(r'(LICEN[CS]E|COPYING|NOTICE|AUTHORS|COPYRIGHT)', re.I)


def norm(name):
    return re.sub(r'[-_.]+', '-', name).lower()


def requires(dist, extras):
    found = []
    for req in dist.requires or []:
        name = re.split(r'[ ;<>=!~\[(]', req, maxsplit=1)[0]
        marker = req.split(';', 1)[1] if ';' in req else ''
        if 'extra ==' in marker:
            wanted = re.findall(r'extra == ["\']([^"\']+)', marker)
            if not set(wanted) & extras: continue
            marker = re.sub(r'(and|or)?\s*extra == ["\'][^"\']+["\']', '', marker).strip()
        if marker:
            try:
                from packaging.markers import Marker
                if not Marker(marker).evaluate(): continue
            except Exception:
                pass
        sub = set(re.findall(r'\[([^\]]+)\]', req.split(';')[0])[0].split(',')) if '[' in req.split(';')[0] else set()
        found.append((name, sub))
    return found


seen, queue = {}, [(r, EXTRAS.get(r, set())) for r in ROOTS]
while queue:
    name, extras = queue.pop()
    try:
        dist = metadata.distribution(name)
    except metadata.PackageNotFoundError:
        continue
    key = norm(dist.metadata['Name'])
    if key in seen and extras <= seen[key][1]: continue
    seen[key] = (dist, extras | (seen[key][1] if key in seen else set()))
    queue += requires(dist, extras)

rows = []
(OUT / 'licenses').mkdir(parents=True, exist_ok=True)
for key, (dist, _) in sorted(seen.items()):
    meta = dist.metadata
    lic = meta.get('License-Expression') or ''
    if not lic:
        classifiers = [c.split('::')[-1].strip() for c in meta.get_all('Classifier') or [] if c.startswith('License ::')]
        lic = '; '.join(classifiers) or (meta.get('License') or 'UNKNOWN').splitlines()[0][:80]
    url = meta.get('Home-page') or next((u.split(',', 1)[1].strip() for u in meta.get_all('Project-URL') or [] if ',' in u), '')
    target = OUT / 'licenses' / f"{meta['Name']}-{dist.version}"
    copied = []
    for item in dist.files or []:
        parts = item.parts
        in_tree = '.dist-info' in parts[0] and len(parts) > 2 and parts[1].lower() == 'licenses'   # PEP 639 licenses/ folder
        if in_tree or (LICENSE_FILE.search(item.name) and '.dist-info' in parts[0]):
            source = Path(dist.locate_file(item))
            if source.is_file():
                dest = target.joinpath(*parts[2:]) if in_tree else target / item.name
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, dest); copied.append(str(dest.relative_to(target)))
    # Some packages keep their license next to the code instead of in dist-info (e.g. onnxruntime's LICENSE and
    # ThirdPartyNotices.txt): take top-level license / notice files of the package folders too.
    for item in dist.files or []:
        parts = item.parts
        if len(parts) == 2 and '.dist-info' not in parts[0] and (LICENSE_FILE.search(item.name) or 'thirdpartynotices' in item.name.lower()):
            source = Path(dist.locate_file(item))
            if source.is_file() and item.name not in copied:
                target.mkdir(parents=True, exist_ok=True); shutil.copyfile(source, target / item.name); copied.append(item.name)
    # Apache-2.0 requires giving recipients a copy of the license; when a package ships none, add the standard text.
    if not copied and 'apache' in lic.lower():
        apache = next((Path(d.locate_file(f)) for d in [metadata.distribution('cryptography')] for f in d.files or [] if f.name == 'LICENSE.APACHE'), None)
        if apache and apache.is_file():
            target.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(apache, target / 'LICENSE-Apache-2.0.txt')
            (target / 'README.txt').write_text(f'{meta["Name"]} {dist.version} declares the Apache License 2.0 but ships no license file;\n'
                                               'the standard license text is provided here.\n', 'utf-8')
            copied += ['LICENSE-Apache-2.0.txt (standard text; the package ships none)']
    if len(copied) > 6: copied = copied[:5] + [f'… {len(copied) - 5} more']
    rows.append((meta['Name'], dist.version, lic, url, copied))

python_license = Path(sys.base_prefix) / 'LICENSE.txt'
if python_license.exists():
    (OUT / 'licenses' / 'Python').mkdir(exist_ok=True)
    shutil.copyfile(python_license, OUT / 'licenses' / 'Python' / 'LICENSE.txt')
vendor = ROOT / 'src' / 'static' / 'mc' / 'vendor'
for name, path in (('marked', vendor / 'marked.LICENSE'), ('KaTeX', vendor / 'katex' / 'LICENSE'), ('highlight.js', vendor / 'hljs' / 'LICENSE')):
    (OUT / 'licenses' / name).mkdir(exist_ok=True)
    shutil.copyfile(path, OUT / 'licenses' / name / 'LICENSE')

lines = ['Chattera — third-party notices', '=' * 30, '',
         'Chattera is MIT-licensed (see LICENSE). The portable package also contains the software below, each under its own',
         'license. Full license texts (and NOTICE files where a package has one) are in the "licenses" folder.', '',
         'Runtime', '-------',
         f'Python {sys.version.split()[0]} (embedded interpreter and standard library) — PSF License Agreement — https://www.python.org/  [licenses/Python]',
         'PyInstaller bootloader (starts chattera-server.exe) — GPL-2.0-or-later WITH the PyInstaller bootloader exception, which allows',
         '    distributing the built program under any license — https://pyinstaller.org/',
         '', 'Browser libraries (in the web interface)', '----------------------------------------',
         'marked 18.2.0 — MIT — https://github.com/markedjs/marked  [licenses/marked]',
         'KaTeX 0.19.1 (incl. its fonts) — MIT — https://katex.org/  [licenses/KaTeX]',
         'highlight.js 11.12.0 — BSD-3-Clause — https://highlightjs.org/  [licenses/highlight.js]',
         '', 'Text recognition models', '-----------------------',
         'The OCR models shipped with rapidocr-onnxruntime are converted from PaddleOCR (Apache-2.0, https://github.com/PaddlePaddle/PaddleOCR).',
         '', 'Original project', '----------------',
         'agentchattr — MIT — Copyright (c) 2026 Ben Curtis — https://github.com/bcurts/agentchattr (Chattera is built on it; see LICENSE)',
         '', 'Python packages', '---------------']
for name, version, lic, url, copied in rows:
    lines.append(f'{name} {version} — {lic} — {url}' + (f'  [licenses/{name}-{version}: {", ".join(copied)}]' if copied else '  [no license file shipped in the package metadata]'))
(OUT / 'THIRD_PARTY_NOTICES.txt').write_text('\n'.join(lines) + '\n', 'utf-8')
print(f'{len(rows)} Python packages; notices written to {OUT}')
missing = [r[0] for r in rows if not r[4]]
if missing: print('without license files:', ', '.join(missing))
