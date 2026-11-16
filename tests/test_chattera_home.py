"""Data-home resolution, layout, portable mode, first-run config and port fallback (src/chattera_paths.py, src/chattera.py)."""
import json
import os
import socket

import chattera
from chattera_paths import Home, default_home, portable, SUBDIRS


def test_default_home_order(tmp_path):
    program = tmp_path / 'program'; program.mkdir()
    env = {'LOCALAPPDATA': str(tmp_path / 'local')}
    if os.name == 'nt':
        assert default_home(program, env) == tmp_path / 'local' / 'Chattera'
    assert default_home(program, dict(env, CHATTERA_HOME=str(tmp_path / 'chosen'))) == (tmp_path / 'chosen').resolve()
    (program / 'portable.txt').write_text('')
    assert portable(program) and default_home(program, env) == (program / 'data').resolve()


def test_home_layout_and_key_folder_left_to_key_store(tmp_path):
    home = Home(tmp_path / 'h').ensure()
    for name in SUBDIRS:
        assert (tmp_path / 'h' / name).is_dir() == (name != 'keys')
    assert (tmp_path / 'h' / 'data' / 'uploads').is_dir()
    assert json.loads((tmp_path / 'h' / 'chattera-home.json').read_text('utf-8'))['app'] == 'Chattera'
    assert home.settings() == {}


def test_first_config_is_created_once_and_never_overwritten(tmp_path):
    home = Home(tmp_path / 'h').ensure()
    cfg = chattera.first_config(home)
    assert cfg['backends'] == {} and 'private_dir' not in cfg
    home.chat_config.write_text(json.dumps({'backends': {}, 'mine': 1}), 'utf-8')
    assert chattera.first_config(home)['mine'] == 1


def test_port_fallback_skips_busy_and_taken():
    with socket.socket() as busy:
        busy.bind(('127.0.0.1', 0)); busy.listen(1)
        port = busy.getsockname()[1]
        chosen = chattera.pick_port(port, span=20)
        assert chosen != port and port < chosen < port + 20
        assert chattera.pick_port(port, taken={chosen}, span=20) not in (port, chosen)


def test_attach_clis_adds_found_cli_switched_off(tmp_path, monkeypatch):
    import cli_adapters, logging
    home = Home(tmp_path / 'h').ensure()
    cfg = chattera.first_config(home)
    folder = tmp_path / 'bin'; folder.mkdir(); (folder / 'codex.exe').write_bytes(b'')
    monkeypatch.setattr(cli_adapters.get('codex'), 'list_models', lambda cfg, timeout=40: [dict(id='m-default', is_default=True)])
    monkeypatch.setattr(cli_adapters.get('claude'), 'discover', lambda extra=(): dict(found=False, executable=None, source=None))
    result = chattera.attach_clis(cfg, home, {'cli_search_paths': [str(folder)]}, logging.getLogger('t'))
    entry = result['backends']['codex']
    assert entry['enabled'] is False and entry['model'] == 'm-default' and entry['discovered_from'] == 'user search path'
    assert json.loads(home.chat_config.read_text('utf-8'))['backends']['codex']['enabled'] is False
