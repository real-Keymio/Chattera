# CLI adapter specification

Chattera can use a locally installed, official AI command-line tool ("subscription CLI") as a **connection**. Each CLI
is supported by one adapter in `src/cli_adapters/`. The adapters for **Claude Code** (`claude_code.py`) and **Codex**
(`codex.py`) are the reference implementations; the contract is `cli_adapters/base.py: CLIAdapter`.

## Ground rules

1. **An adapter only talks to its CLI.** It does not define chat identities, does not see permissions, history,
   snapshots, summaries or the library, and must not import those modules. The chat core gives it a prompt that is
   already built and authorised, plus an empty sandbox directory, and receives text and usage back.
2. **One CLI, many models; any model can be its own participant.** The adapter reports the models the CLI offers.
   Which participant speaks through which connection and model is the user's choice in Settings, not the adapter's.
3. **Adding a CLI changes no core module.** Write `src/cli_adapters/<name>.py`, call `register(<Adapter>())` in
   `src/cli_adapters/__init__.py`, add tests. The task manager, registry, model discovery and settings screens pick it
   up from the registry.
4. **Being able to call a CLI non-interactively is not permission to use a consumer subscription from a third-party
   app.** Whether a vendor's terms allow this is for the user to check. Discovered CLIs are added **switched off**, and
   the interface says so. Never describe an adapter as officially supported or as "a subscription instead of an API".

## Responsibilities

| Area | Method(s) | Requirements |
|---|---|---|
| **Discover** | `commands`, `default_locations()`, `resolve()`, `discover(extra_paths)`, `version()` | Find the executable in the user's extra search paths (Settings → 诊断 → 额外扫描位置), then `PATH`, then the vendor's default install locations. Resolve package-manager shims to the real executable when the package ships one. Report version via `--version`. Never install or update anything. |
| **Model List** | `list_models(cfg, timeout)` | Read the catalogue **programmatically from the CLI itself** (e.g. Claude Code's SDK `initialize` reply, Codex's app-server `model/list`, following pagination). Return `id`, `label`, `description`, `effort_values`, `is_default`. No model call. If the CLI cannot be asked, **raise**: the interface then labels any model list as an assumption (“假设”) and lets the user type an ID. |
| **Authentication** | `auth_status(cfg)` | Only through the vendor's **official status command** (`claude auth status`, `codex login status`). Return `signed-in` / `signed-out` / `UNKNOWN` and at most a method word. Never read, copy, print or log credential files or tokens; never start a login; never return account names or e-mail addresses. |
| **Generate** | `command()`, `prepare_input()`, `parse()`, `progress_event()`, `tool_event()` | Non-interactive invocation with machine-readable output (JSON lines). The prompt goes on stdin. No terminal emulation, no simulated key presses, no session persistence. `parse()` returns `text`, `model` (reported, or `UNKNOWN`), `requested_model`, `usage`, `usage_fields`, `tool_calls`; it **raises** when there is no successful final answer, so nothing is published. |
| **Cancel** | provided by `chat_backends.CLIBackend` + `ProcessTree` | The process is created suspended inside a Windows job object (kill-on-close) and resumed; cancellation and timeouts terminate the whole tree. Output is capped (2 MB). Adapters must not spawn detached helpers that escape the job. |
| **Tools** | `command()` | File-writing and command-execution tools **must be off**; the working directory is an empty sandbox per reply. The only tools offered are the room tool server (read-only chat history, research, library, calculator: `ROOM_TOOLS`) and, only for replies started with native search, the CLI's own web search. **If a CLI cannot switch off file and shell tools, it must not be added as a chat backend.** |
| **Capabilities** | `capabilities` | Images, files, search, context window — each with its source: `provider` (vendor documentation), `config` (set by the operator) or `tested` (exercised by tests or recorded calls). Unknown stays `UNKNOWN`; never infer from a model name. |
| **Usage** | `usage(raw)` | Map the CLI's usage report to the unified fields (`input_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`, `output_tokens`, `input_semantics`, `subscription_debit`, `billed_amount`). Fields the CLI does not report are `UNKNOWN`; subscription debit is always `UNKNOWN` unless the vendor reports it. |

The environment of every CLI child process is cleaned by `clean_environment()`: API keys and provider overrides from
the server's own environment never reach the CLI.

## Three states

| State | Meaning | How it is decided |
|---|---|---|
| 已发现 (found) | An executable for a known CLI exists on this computer. | `discover()` finds a file; nothing else is implied. |
| 已适配 (adapted) | Chattera has an adapter for it. | The name is registered in `cli_adapters.ADAPTERS`. CLIs known but not adapted are listed in `KNOWN_UNADAPTED`. |
| 可以聊天 (can chat) | It can answer as a participant now. | Adapted **and** configured as a connection **and** switched on **and** its executable still exists. A successful call is recorded separately (Settings → 诊断 → 调用记录); “can chat” is not “verified”. |

## Tests an adapter must pass

Add them to `tests/` next to `test_cli_adapters.py`. All must run without the real CLI installed (use fixtures).

1. `discover()` finds the executable in an explicit extra path, ignores a missing path, and resolves shims correctly.
2. `command()` for a normal reply contains the non-interactive flags, the model, the effort, the room tool server, and
   **does not enable file-writing or shell tools**; for `baseline=True` no tool server is offered; for `search=True` only
   the CLI's own search is added.
3. `prepare_input()` attaches images the way the CLI expects (or leaves the prompt on stdin when there are none).
4. `parse()` returns the final text and usage from a recorded output sample, and **raises** on an error result, on a
   missing final message, and on empty output.
5. `usage()` maps every reported field and leaves the rest `UNKNOWN`.
6. `list_models()` parses a recorded catalogue reply (including pagination when the CLI paginates) and raises on a
   malformed reply.
7. `auth_status()` parses signed-in / signed-out / unrecognised status output, never returns anything that looks like an
   e-mail address, and returns `UNKNOWN` when the command fails.
8. `cli_adapters.register()` accepts the adapter and the task manager can build a `CLIBackend` for it.
9. A real call (manual, recorded in the pull request): one short reply through the adapter, and one cancellation.

## Minimal skeleton

```python
# src/cli_adapters/example.py
from .base import CLIAdapter, status_command

class ExampleCLI(CLIAdapter):
    name, label, commands = 'example', 'Example CLI', ('example',)
    effort_values = []
    capabilities = dict(images=('UNKNOWN', ''), files=(True, 'tested: text via room tools'),
                        search=('UNKNOWN', ''), context_window=('UNKNOWN', ''))

    def list_models(self, cfg, timeout=40):
        raise ValueError('this CLI has no machine-readable model list')   # the UI then treats models as assumptions

    def auth_status(self, cfg):
        code, out = status_command([cfg['executable'], 'auth', 'status'])
        return dict(status='signed-in' if code == 0 else 'UNKNOWN', method=None, source='example auth status')

    def command(self, cfg, directory, mcp, baseline=False, search=False):
        return [cfg['executable'], '--non-interactive', '--json', '--no-tools', '--model', cfg['model']]

    def parse(self, cfg, output):
        events = self.json_lines(output)
        final = next((e for e in reversed(events) if e.get('type') == 'final'), None)
        if not final or not final.get('text'): raise RuntimeError('no final answer')
        return dict(text=final['text'], model=final.get('model', 'UNKNOWN'), requested_model=cfg['model'],
                    usage=final.get('usage', 'UNKNOWN'), usage_fields=self.usage(final.get('usage')), tool_calls=[])

# src/cli_adapters/__init__.py
#   from .example import ExampleCLI
#   register(ExampleCLI())
```

Native web search tracing (`chat_search.CliTrace`) currently understands the Claude Code and Codex event streams only;
a new adapter gets native search records once a parser for its events is added there. Until then, leave its native
search off: replies can still use Chattera's own search tools.
