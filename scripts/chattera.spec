# PyInstaller spec for the Chattera server (folder build, not one-file). Used by scripts/build_portable.ps1.
#   .venv\Scripts\pyinstaller.exe --noconfirm --distpath <out> --workpath <work> scripts\chattera.spec
# Output: <out>\chattera-server\chattera-server.exe plus its _internal folder; the build script renames the folder
# to `runtime` inside the portable package.
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

ROOT = Path(SPECPATH).parent
SRC = ROOT / 'src'

# Every application module: several are loaded by name (the tool server, document worker, image preparation run as
# `chattera-server.exe --worker <name>`), so static analysis alone would miss them.
local = sorted(p.stem for p in SRC.glob('*.py')) + ['cli_adapters', 'cli_adapters.base', 'cli_adapters.claude_code', 'cli_adapters.codex']
hidden = local + collect_submodules('uvicorn') + collect_submodules('rapidocr_onnxruntime') + collect_submodules('mcp', filter=lambda name: not name.startswith('mcp.cli')) + ['pypdfium2', 'pypdf', 'docx', 'lxml.html', 'PIL.Image', 'multipart']

datas = [
    (str(SRC / 'static'), 'static'),
    (str(SRC / 'defaults'), 'defaults'),
    (str(SRC / 'config.toml'), '.'),
    (str(ROOT / 'VERSION'), '.'),
] + collect_data_files('pypdfium2_raw') + collect_data_files('docx') + collect_data_files('rapidocr_onnxruntime')  # OCR models and config

# The package is the full version: text recognition (OCR) is included. Development tools are left out.
excludes = ['tkinter', 'pytest', 'PyInstaller', 'IPython', 'matplotlib']

a = Analysis([str(SRC / 'chattera.py')], pathex=[str(SRC)], hiddenimports=hidden, datas=datas, excludes=excludes, noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='chattera-server', console=True,
          icon=str(SRC / 'static' / 'mc' / 'favicon.ico'), version=None, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name='chattera-server', upx=False)
