"""Where Chattera keeps the user's things, kept apart from the program folder.

Resolution order for the data home:
  1. --home <path> on the command line, or the CHATTERA_HOME environment variable;
  2. portable mode: a file named ``portable.txt`` next to the program (or the program's ``data`` folder already
     containing ``chattera-home.json``) puts everything in ``<program>\\data``;
  3. otherwise ``%LOCALAPPDATA%\\Chattera`` on Windows, ``~/Library/Application Support/Chattera`` on macOS and
     ``$XDG_DATA_HOME/chattera`` (``~/.local/share/chattera``) elsewhere (macOS and Linux are untested).

Layout of the home (created on first start; nothing outside it is written):
  config/   chattera.json (connections and chat settings), settings.json (port and launcher preferences)
  data/     chats, rooms, participants, model registry, reply records, uploads, derived attachment cache
  keys/     API keys, one file per connection; never sent to the browser or written to logs
  library/  research library index and its content-addressed file cache
  cache/    temporary files of running replies; safe to delete while Chattera is closed
  logs/     server and launcher logs
  run/      server.json while the server is running (port and process id), so the launcher can find it
"""
import json
import os
import sys
from pathlib import Path

APP = 'Chattera'
MARKER = 'chattera-home.json'
SUBDIRS = ('config', 'data', 'keys', 'library', 'cache', 'logs', 'run')


def program_dir():
    """The folder the user unpacked or cloned: next to Chattera.exe in a build, the repository root from source."""
    if getattr(sys, 'frozen', False):
        exe = Path(sys.executable).resolve().parent
        return exe.parent if exe.name.lower() == 'runtime' else exe
    return Path(__file__).resolve().parents[1]


def source_dir():
    """Application files (static pages, templates, default config)."""
    if getattr(sys, 'frozen', False):
        return Path(getattr(sys, '_MEIPASS', Path(sys.executable).parent))
    return Path(__file__).resolve().parent


def portable(program=None):
    program = Path(program or program_dir())
    return (program / 'portable.txt').exists() or (program / 'data' / MARKER).exists()


def default_home(program=None, environ=None):
    environ = os.environ if environ is None else environ
    if environ.get('CHATTERA_HOME'):
        return Path(environ['CHATTERA_HOME']).expanduser().resolve()
    program = Path(program or program_dir())
    if portable(program):
        return (program / 'data').resolve()
    if os.name == 'nt':
        base = environ.get('LOCALAPPDATA') or str(Path.home() / 'AppData' / 'Local')
        return Path(base) / APP
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support' / APP
    return Path(environ.get('XDG_DATA_HOME') or Path.home() / '.local' / 'share') / APP.lower()


def python_script(name):
    """Command that runs one of the helper scripts (tool server, document parser, image preparation) in a child process.
    From source: this Python and the .py file. In the packaged build: the server executable in worker mode."""
    if getattr(sys, 'frozen', False):
        return [sys.executable, '--worker', name]
    return [sys.executable, str(Path(__file__).with_name(name + '.py'))]


WORKERS = ('chat_task_mcp', 'attachment_worker', 'image_prepare')


class Home:
    def __init__(self, root):
        self.root = Path(root).resolve()
        for name in SUBDIRS:
            setattr(self, name, self.root / name)

    def ensure(self):
        for name in SUBDIRS:
            # keys/ is created by the key store itself the first time a key is saved, so that it can restrict the
            # folder to the current Windows account at creation (chat_registry.Registry.write_key)
            if name != 'keys':
                (self.root / name).mkdir(parents=True, exist_ok=True)
        (self.data / 'uploads').mkdir(exist_ok=True)
        marker = self.root / MARKER
        if not marker.exists():
            marker.write_text(json.dumps({'app': APP, 'layout': 1}, indent=1), 'utf-8')
        return self

    @property
    def chat_config(self):
        return self.config / 'chattera.json'

    @property
    def settings_file(self):
        return self.config / 'settings.json'

    def settings(self):
        try:
            return json.loads(self.settings_file.read_text('utf-8'))
        except (OSError, ValueError):
            return {}
