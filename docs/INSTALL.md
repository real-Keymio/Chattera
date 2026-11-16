# Installing Chattera

<a id="portable"></a>
## 1. Windows portable package (no Python needed)

1. Unzip `Chattera-Windows-x64.zip` anywhere (Desktop, Documents, a USB stick).
2. Double-click `Chattera.exe`. A small start-up window appears, then Chattera opens in its own window
   (a Microsoft Edge app window when Edge is installed, otherwise your default browser).
3. Chattera keeps running in the notification area (bottom right). Right-click its icon for: open, restart,
   user guide, open data folder, open log folder, create a desktop shortcut, **quit**.

Package contents:

```
Chattera.exe                    launcher (start, reopen, quit)
runtime\                        the Chattera server (Python and libraries bundled)
01_先看这个.pdf                  two-page quick start
02_Chattera完整使用指南.pdf      full user guide
README.txt  LICENSE.txt  THIRD_PARTY_NOTICES.txt  licenses\  VERSION
```

Upgrading: quit Chattera, replace the whole program folder with the new one, start again. Your data is not in the
program folder and is kept.

## 2. From source

Requirements: Python 3.11 or newer, Git.

```powershell
git clone <repository-url> chattera
cd chattera
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1          # check Python, .venv, install, test, start
```

Options: `-NoStart` (install and test only), `-SkipTests`, `-WithOcr` (text recognition for scanned PDFs; large; needed to build the portable package).
Later starts: `.venv\Scripts\python.exe src\chattera.py` — or build the launcher once
(`powershell -ExecutionPolicy Bypass -File launcher\Build-Launcher.ps1`) and double-click `Chattera.exe` in the
repository root; it uses `.venv` automatically.

macOS / Linux (untested): `scripts/setup.sh`, then `.venv/bin/python src/chattera.py`.

Command line of `src/chattera.py`:

| Option | Meaning |
|---|---|
| `--home <folder>` | use this data folder (also: environment variable `CHATTERA_HOME`) |
| `--port <n>` | preferred web port (default 8300; if taken, the next free one is used and logged) |
| `--print-home` | print the data folder that would be used, and exit |

### Building the portable package

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -NoStart     # once: .venv with PyInstaller
npm install --no-save playwright-core                                    # once: for the user-guide PDFs (uses installed Edge)
node scripts\build_guides.js                                             # docs\user-guide\*.html -> docs\user-guide\dist\*.pdf
powershell -ExecutionPolicy Bypass -File scripts\build_portable.ps1 -Out D:\builds
```

The script builds the server with PyInstaller (folder mode), compiles `Chattera.exe`, collects third-party licenses
(`scripts/collect_licenses.py`), adds the user guides from `docs/user-guide/dist`, and writes
`Chattera-Windows-x64.zip`. The package is the full version: text recognition (OCR) for scanned PDFs is included
(the build environment needs `requirements-ocr.txt` installed, which `setup.ps1 -WithOcr` does).

## 3. Where your data lives

Default: `%LOCALAPPDATA%\Chattera` (type it into the Explorer address bar).

```
config\    chattera.json (chat settings, first-run template), settings.json (port, extra CLI search paths)
data\      chats, rooms, participants, model registry, reply records, uploads, derived attachment cache, workspaces
keys\      API keys, one file per connection (folder access restricted to your Windows account)
library\   research library index and its file cache
cache\     temporary files of running replies (safe to delete while Chattera is closed)
logs\      server.log, launcher.log
run\       server.json while running (actual port and process id)
```

**Another data folder**: start `Chattera.exe --home D:\Somewhere\Chattera` (for example in a shortcut's target).
The launcher passes the folder to the server; a desktop shortcut created from the tray menu keeps it.

**Portable data mode** (USB stick): create an empty file named `portable.txt` next to `Chattera.exe`. From then on
everything is stored in the `data` folder beside the program. To move existing data there, quit Chattera and copy the
contents of `%LOCALAPPDATA%\Chattera` into that `data` folder.

**Backup**: quit Chattera and copy the whole data folder. **Uninstall**: delete the program folder; delete the data
folder too if you no longer need your chats.

## 4. Text recognition (OCR)

The **portable package includes OCR**: scanned PDFs and pictures of text are recognised automatically (RapidOCR,
running locally; nothing is sent anywhere for recognition).

From source, OCR is optional because it is large (about 240 MB of libraries):
`.venv\Scripts\python.exe -m pip install -r requirements-ocr.txt` or `scripts\setup.ps1 -WithOcr`. Without it, Chattera
reads PDFs that contain text and says when a scanned page would need OCR.

## 5. Troubleshooting

| Symptom | What to do |
|---|---|
| Start-up window shows an error | Click **打开日志** and read `server.log`; the most recent lines name the problem. |
| Port already in use | Chattera picks the next free port automatically; the launcher finds it. To fix a port, put `{"port": 8400}` into `config\settings.json`. |
| Windows SmartScreen warns about an unknown publisher | The package is not code-signed. Choose “More info → Run anyway” only if you downloaded it from the official release page. |
| Closed the window, Chattera still running | Expected; quit from the tray icon or 帮助 → 退出 Chattera. |
| A provider rejects the key | Re-enter it in 设置 → 模型与服务 → the connection → API Key. Check the account has API credit. |
