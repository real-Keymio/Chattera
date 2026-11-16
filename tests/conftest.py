"""Make the application modules in src/ importable for every test, without PYTHONPATH."""
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / 'src'
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# Server messages follow the interface language; tests assert the Chinese source text, whatever the machine language.
import chattera_i18n  # noqa: E402
chattera_i18n.configure("zh")
