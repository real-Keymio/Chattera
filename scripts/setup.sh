#!/usr/bin/env bash
# Chattera from source on macOS / Linux (UNTESTED on these systems).
#   scripts/setup.sh            install, test, start
#   scripts/setup.sh --no-start install and test only
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-python3}"
"$PY" -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11+ required"'
[ -x .venv/bin/python ] || "$PY" -m venv .venv
.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/python -m pip install -q -r requirements-dev.txt
.venv/bin/python -m pytest -q -p no:cacheprovider tests
[ "${1:-}" = "--no-start" ] && exit 0
exec .venv/bin/python src/chattera.py
