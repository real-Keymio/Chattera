"""Interface language of the server-side text (error messages, demo replies, labels the browser shows as they are).

Chinese source text is the key, as in the frontend: _('名称不能为空') returns the English entry of
static/mc/i18n/en.json when the language is English, otherwise the Chinese text. {0}, {1}… are filled from the
following arguments. One dictionary serves both the browser and the server.

The language setting lives in config\\settings.json as "language": "auto" (default), "zh" or "en". "auto" follows the
Windows display language (elsewhere the locale): Chinese gives zh, anything else en. Text written for models (prompts,
lens methods, discussion-action instructions) is English and does not depend on this setting.
"""
import json
import locale
import os
import re
from pathlib import Path

SETTINGS = ('auto', 'zh', 'en')
_DICT_PATH = Path(__file__).resolve().parent / 'static' / 'mc' / 'i18n' / 'en.json'
_state = {'setting': 'auto', 'lang': None}
_en = None


def system_language():
    """'zh' when the user's display language is Chinese, else 'en'."""
    name = ''
    if os.name == 'nt':
        try:
            import ctypes
            lcid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            name = locale.windows_locale.get(lcid, '')
        except Exception:
            name = ''
    if not name:
        for var in ('LC_ALL', 'LC_MESSAGES', 'LANG'):
            if os.environ.get(var):
                name = os.environ[var]; break
    if not name:
        try:
            name = locale.getlocale()[0] or ''
        except Exception:
            name = ''
    return 'zh' if name.lower().replace('-', '_').startswith(('zh', 'chinese')) else 'en'


def configure(setting):
    setting = setting if setting in SETTINGS else 'auto'
    _state['setting'] = setting
    _state['lang'] = system_language() if setting == 'auto' else setting
    return _state['lang']


def setting():
    return _state['setting']


def lang():
    if _state['lang'] is None:
        configure(_from_home())
    return _state['lang']


def _from_home():
    home = os.environ.get('CHATTERA_HOME')
    if not home:
        return 'auto'
    try:
        return json.loads((Path(home) / 'config' / 'settings.json').read_text('utf-8')).get('language', 'auto')
    except (OSError, ValueError, AttributeError):
        return 'auto'


def _dictionary():
    global _en
    if _en is None:
        try:
            _en = json.loads(_DICT_PATH.read_text('utf-8'))
        except (OSError, ValueError):
            _en = {}
    return _en


def N_(text):
    """Mark text for translation without translating it now (module-level tables); call _() where it is shown."""
    return text


def _(text, *args):
    out = _dictionary().get(text, text) if lang() == 'en' else text
    if args:
        out = re.sub(r'\{(\d+)\}', lambda m: '' if int(m.group(1)) >= len(args) or args[int(m.group(1))] is None else str(args[int(m.group(1))]), out)
    return out
