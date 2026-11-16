"""Chattera entry point: prepares the data home, the chat configuration and free ports, then starts the server.

    python src/chattera.py                 # data in %LOCALAPPDATA%\\Chattera (or <program>\\data in portable mode)
    python src/chattera.py --home D:\\x     # any other data folder
    python src/chattera.py --port 8400     # preferred port (the next free one is used if it is taken)

In the packaged build the same code runs as runtime\\chattera-server.exe; `--worker <name>` runs one of the helper
scripts (tool server, document parser, image preparation) in that executable.
"""
import argparse
import atexit
import json
import logging
import logging.handlers
import os
import socket
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

DEFAULT_PORT = 8300
VERSION_FILE_NAMES = ('VERSION',)


def run_worker(argv):
    """`chattera-server.exe --worker chat_task_mcp <args>`: run a helper module as if it were `python <module>.py <args>`."""
    import runpy
    from chattera_paths import WORKERS
    name = argv[0] if argv else ''
    if name not in WORKERS:
        sys.exit('unknown worker')
    sys.argv = [name + '.py', *argv[1:]]
    runpy.run_module(name, run_name='__main__', alter_sys=True)


def version():
    from chattera_paths import program_dir, source_dir
    for folder in (program_dir(), source_dir()):
        path = folder / 'VERSION'
        if path.is_file():
            return path.read_text('utf-8').strip()
    return 'UNKNOWN'


def free(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, getattr(socket, 'SO_EXCLUSIVEADDRUSE', socket.SO_REUSEADDR), 1)
        try:
            probe.bind(('127.0.0.1', port))
            return True
        except OSError:
            return False


def pick_port(preferred, taken=(), span=60):
    """The preferred port if it is free, else the next free one above it. Ports in `taken` are skipped."""
    for port in range(preferred, preferred + span):
        if port not in taken and free(port):
            return port
    raise SystemExit(f'没有可用的本机端口（{preferred}–{preferred + span - 1} 都被占用）。请在 config\\settings.json 里换一个 "port"。')


def first_config(home):
    """The chat configuration; created from the template on first start. Never overwritten later."""
    from chattera_paths import source_dir
    if not home.chat_config.exists():
        template = json.loads((source_dir() / 'defaults' / 'chattera.json').read_text('utf-8'))
        home.chat_config.write_text(json.dumps(template, ensure_ascii=False, indent=1), 'utf-8')
    return json.loads(home.chat_config.read_text('utf-8-sig'))


def attach_clis(cfg, home, settings, log):
    """Subscription CLIs found on this computer become connections, switched OFF until the user turns them on.

    Discovery looks at the user's extra search paths first, then PATH, then each vendor's default install location.
    A CLI found for the first time is written into config/chattera.json (with the model its own listing marks as
    default, when it can be asked), so later starts do not need to probe again."""
    import cli_adapters
    extra = list(settings.get('cli_search_paths') or []) + list(cfg.get('cli_search_paths') or [])
    saved = json.loads(home.chat_config.read_text('utf-8-sig'))
    changed = False
    for name in cli_adapters.names():
        adapter = cli_adapters.get(name)
        current = cfg['backends'].get(name)
        if current and current.get('type') in ('api', 'openai-compatible'):
            continue  # an API connection that happens to use this name
        if current and Path(current.get('executable', '')).is_file():
            continue
        hit = adapter.discover(extra)
        if not hit['found']:
            if current:
                log.warning('%s: configured executable no longer exists; connection left as is', adapter.label)
            continue
        defaults = dict(cfg.get('cli_defaults', {}).get(name) or {})
        entry = dict(executable=hit['executable'], model=defaults.get('model') or 'default', effort=defaults.get('effort', adapter.default_effort),
                     timeout=defaults.get('timeout', 180), enabled=False, discovered_from=hit['source'])
        if current:
            entry = dict(current, executable=hit['executable'])
        else:
            try:
                models = adapter.list_models(entry, timeout=25)
                default = next((m for m in models if m.get('is_default')), models[0] if models else None)
                if default: entry['model'] = default['id']
                entry['model_source'] = 'reported by the CLI'
            except Exception as exc:
                entry['model_source'] = 'assumed (the CLI could not be asked: %s)' % type(exc).__name__
        saved.setdefault('backends', {})[name] = entry
        if name in ('claude', 'codex'):
            # the built-in participant of the same name speaks through it by default (still off until switched on)
            saved.setdefault('default_owners', {}).setdefault(name, name)
        changed = True
        log.info('%s found (%s); added as a connection, switched off', adapter.label, hit['source'])
    if changed:
        temp = home.chat_config.with_suffix('.tmp')
        temp.write_text(json.dumps(saved, ensure_ascii=False, indent=1), 'utf-8')
        temp.replace(home.chat_config)
    return saved if changed else cfg


def setup_logging(home):
    handler = logging.handlers.RotatingFileHandler(home.logs / 'server.log', maxBytes=2_000_000, backupCount=3, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s [%(name)s] %(levelname)s: %(message)s'))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    return logging.getLogger('chattera')


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ['--worker']:
        return run_worker(argv[1:])
    parser = argparse.ArgumentParser(prog='chattera', description='Chattera — a group chat for you and your AIs.')
    parser.add_argument('--home', help='data folder (default: %%LOCALAPPDATA%%\\Chattera, or <program>\\data in portable mode)')
    parser.add_argument('--port', type=int, help='preferred web port (default 8300; the next free port is used if taken)')
    parser.add_argument('--print-home', action='store_true', help='print the data folder and exit')
    args = parser.parse_args(argv)

    from chattera_paths import Home, default_home, program_dir, source_dir
    home = Home(args.home or default_home()).ensure()
    if args.print_home:
        print(home.root)
        return
    log = setup_logging(home)
    settings = home.settings()

    cfg = first_config(home)
    cfg.setdefault('backends', {})
    cfg = attach_clis(cfg, home, settings, log)

    # Runtime copy of the configuration with this machine's paths filled in; the user's file is not rewritten.
    runtime = dict(cfg, private_dir=str(home.keys), parser_dependencies=cfg.get('parser_dependencies', ''))
    runtime.pop('_comment', None)
    if cfg.get('room_prompts_file'):   # relative to config\, where the user keeps it (the runtime copy lives in run\)
        runtime['room_prompts_file'] = str((home.config / cfg['room_prompts_file']).resolve())
    runtime_path = home.run / 'chattera.runtime.json'
    runtime_path.write_text(json.dumps(runtime, ensure_ascii=False, indent=1), 'utf-8')

    preferred = args.port or int(settings.get('port') or DEFAULT_PORT)
    web = pick_port(preferred)
    if web != preferred:
        log.warning('port %s is in use; using %s', preferred, web)

    os.environ.update(
        AGENTCHATTR_CHAT_CONFIG=str(runtime_path), AGENTCHATTR_DATA_DIR=str(home.data), AGENTCHATTR_UPLOAD_DIR=str(home.data / 'uploads'),
        AGENTCHATTR_PORT=str(web),
        AGENTCHATTR_REQUIRE_CHANNEL='1', AGENTCHATTR_RESEARCH='1', CHATTERA_LIBRARY_DIR=str(home.library),
        CHATTERA_HOME=str(home.root), CHATTERA_VERSION=version(), PYTHONUTF8='1',
        TEMP=str(home.cache), TMP=str(home.cache))

    state = home.run / 'server.json'
    state.write_text(json.dumps(dict(app='Chattera', version=version(), url=f'http://127.0.0.1:{web}/', port=web, pid=os.getpid(),
                                     started_at=time.time(), home=str(home.root), program=str(program_dir())), indent=1), 'utf-8')
    def cleanup():
        try:
            if json.loads(state.read_text('utf-8')).get('pid') == os.getpid():
                state.unlink()
        except (OSError, ValueError):
            pass
    atexit.register(cleanup)
    log.info('Chattera %s; data %s; http://127.0.0.1:%s', version(), home.root, web)

    os.chdir(source_dir())
    sys.argv = [sys.argv[0]]
    import run
    run.main()


if __name__ == '__main__':
    main()
