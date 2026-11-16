"""Model catalogues and local discovery for subscription CLIs, through their adapters (cli_adapters/).

Neither listing makes a model call, reads credential files, or drives a terminal. Sign-in status comes only from each
vendor's own status command.
"""
import os
import subprocess

import cli_adapters
from chat_backends import clean_environment, validate_cli

HOW = {'claude': 'Claude Code 的 SDK 初始化响应（与它自己的模型选择器同源）', 'codex': 'Codex App Server 的 model/list 接口'}


def cli_models(name, cfg, timeout=40):
    """Normalised listing for the interface. `ok` is false (with a reason) when the CLI could not be asked."""
    adapter = cli_adapters.get(name)
    how = HOW.get(name, (adapter.label if adapter else name) + ' 自己报告的模型列表')
    try:
        if adapter is None: raise ValueError('no adapter')
        version = validate_cli(name, cfg)['version']
        models = adapter.list_models(cfg, timeout)
        if not models: raise ValueError('empty model list')
        return dict(models=models, ok=True, reason=None, source=how + ' · ' + version,
                    note='这是本机 CLI 报告的可选模型与推理档位，不代表每个组合都已实际调用验证。')
    except Exception as exc:
        return dict(models=[], ok=False, source=how,
                    reason='没能从本机 CLI 读到模型列表（' + type(exc).__name__ + '）。下面的模型只是假设，可以手动添加模型 ID。')


def cli_discovery(name, cfg):
    """Older probe kept for the previous interface: version and --help only."""
    try:
        version = validate_cli(name, cfg)
        response = subprocess.run([cfg['executable'], '--help'], capture_output=True, timeout=10, env=clean_environment(cfg.get('secret_env_names', [])),
                                  creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        help_text = response.stdout.decode('utf-8', 'replace')
        adapter = cli_adapters.get(name)
        return dict(models=[], current_model=cfg.get('model'), model_list_status='UNKNOWN', manual_model_allowed=True,
                    client_effort_values=list(adapter.effort_values) if adapter else 'UNKNOWN', model_effort_support='UNKNOWN',
                    source='installed official CLI --version/--help; no account catalogue available through this probe',
                    status='VERIFIED', version=version, model_flag='--model' in help_text,
                    effort_mapping='--effort' if name == 'claude' else '-c model_reasoning_effort=...')
    except Exception:
        return dict(models=[], status='UNKNOWN', source='CLI help unavailable', manual_model_allowed=True)


def _version(path):
    try:
        done = subprocess.run([path, '--version'], capture_output=True, timeout=12, env=clean_environment(),
                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return done.stdout.decode('utf-8', 'replace').strip().splitlines()[0][:80] if done.returncode == 0 and done.stdout.strip() else None
    except (OSError, subprocess.TimeoutExpired, IndexError):
        return None


def local_clis(config, runners, extra_paths=(), auth=False):
    """Read-only scan: where each known CLI is, its version, and how far Chattera can use it.

    Three states, judged separately: discovered (an executable was found), adapted (Chattera has an adapter for it),
    usable (adapted, configured as a connection, switched on, and its executable is still there)."""
    import shutil
    found = []
    for name in cli_adapters.names():
        adapter = cli_adapters.get(name)
        configured = (config.get(name) or {}).get('executable')
        hit = adapter.discover(extra_paths)
        path = configured if configured and os.path.isfile(configured) else hit['executable']
        row = dict(name=name, label=adapter.label, path=path, discovered=bool(path), adapted=True, usable=False, version=_version(path) if path else None,
                   source='configured' if path and path == configured else hit['source'], note='', capabilities=adapter.describe()['capabilities'])
        runner = runners.get(name)
        row['configured'] = runner is not None
        row['usable'] = bool(path and runner is not None and (not hasattr(runner, 'available') or runner.available()))
        if auth and path:
            row['auth'] = adapter.auth_status(dict(config.get(name) or {}, executable=path))
        if row['usable']: row['note'] = '已适配，可以聊天。'
        elif path and runner is None: row['note'] = '已发现、已适配；重启 Chattera 后会出现在“连接”里（默认关闭，启用前请确认服务条款）。'
        elif path: row['note'] = '已适配，但当前已关闭。'
        else: row['note'] = '没有找到。'
        found.append(row)
    for name, label, commands, note in cli_adapters.KNOWN_UNADAPTED:
        path = next((shutil.which(c) or shutil.which(c + '.cmd') for c in commands if shutil.which(c) or shutil.which(c + '.cmd')), None)
        found.append(dict(name=name, label=label, path=path, discovered=bool(path), adapted=False, usable=False, configured=False,
                          version=_version(path) if path else None, source='PATH' if path else None,
                          note=(note or '已发现，但 Chattera 没有它的聊天适配器，不能作为参与者使用。') if path else '没有找到。'))
    return dict(clis=found, extra_paths=list(extra_paths))
