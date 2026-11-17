"""The contract every subscription-CLI adapter fulfils. See docs/CLI_ADAPTER.md.

An adapter only talks to one installed, official CLI. It does not define chat identities, does not see permissions,
history, snapshots or the library, and never reads, copies or prints credential files. The chat core hands it a
prepared, already-authorised prompt plus an empty sandbox directory and gets text and usage back.
"""
import glob
import json
import os
import shutil
import subprocess
from pathlib import Path

NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def clean_environment(secret_names=()):
    """Environment for a CLI child process: no API keys or provider overrides leak in from the server's own environment."""
    env = {k: v for k, v in os.environ.items() if not (
        k in secret_names or k.endswith('_API_KEY') or k.startswith(("ANTHROPIC_", "CLAUDE_CODE_USE_", "GOOGLE_API_", "GEMINI_API_"))
        or k in {"OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL", "CLAUDE_CODE_OAUTH_TOKEN",
                 "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CODEX_INTERNAL_ORIGINATOR_OVERRIDE"})}
    env.update(DISABLE_AUTOUPDATER="1", CLAUDE_CODE_DISABLE_AUTO_MEMORY="1")
    return env


def exchange(command, messages, done, timeout, cwd=None, secrets=()):
    """Send JSON lines to a CLI's machine interface and wait for the first reply that satisfies `done`."""
    import threading
    import time
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, cwd=cwd,
                               env=clean_environment(secrets), creationflags=NO_WINDOW)
    received = []
    def read():
        for line in process.stdout:
            try: received.append(json.loads(line))
            except ValueError: continue
    threading.Thread(target=read, daemon=True).start()
    try:
        for message in messages:
            process.stdin.write((json.dumps(message) + '\n').encode('utf-8'))
            process.stdin.flush()
        deadline = time.time() + timeout
        while time.time() < deadline:
            found = next((item for item in received if done(item)), None)
            if found is not None: return found
            if process.poll() is not None and not any(done(item) for item in received):
                time.sleep(.2)
                return next((item for item in received if done(item)), None)
            time.sleep(.05)
        return None
    finally:
        process.kill()


def status_command(command, timeout=15, secrets=()):
    """Run a vendor status command; return (exit code, stdout text). Output is parsed by the caller and never stored."""
    done = subprocess.run(command, capture_output=True, timeout=timeout, env=clean_environment(secrets), creationflags=NO_WINDOW)
    return done.returncode, done.stdout.decode('utf-8', 'replace')


class CLIAdapter:
    #: connection id used in configuration; also the built-in participant it serves by default, if one has that id
    name = ''
    label = ''
    #: executable names looked up on PATH and in the search folders
    commands = ()
    #: reasoning levels the CLI accepts; [] when it has none
    effort_values = []
    default_effort = 'medium'
    #: what is known about the CLI, and how: 'provider' (vendor documentation), 'config' (set by the operator),
    #: 'tested' (exercised by this project's adapter tests or a recorded call). Never inferred from a model name.
    capabilities = dict(images=('UNKNOWN', ''), files=('UNKNOWN', ''), search=('UNKNOWN', ''), context_window=('UNKNOWN', ''))
    #: tool names of the room tool server the CLI may call (everything else, especially file writes and shell, stays off)
    room_tools = ()

    # ---- Discover ----
    def default_locations(self):
        """Extra places an official installer puts the executable (globs allowed). Newest match wins."""
        return []

    def discover(self, extra_paths=()):
        """Where the CLI is. Looks at explicit extra paths first (files or folders), then PATH, then default locations."""
        candidates = []
        for raw in extra_paths or ():
            path = Path(os.path.expandvars(str(raw))).expanduser()
            if path.is_file() and path.stem.lower() in {c.lower() for c in self.commands}: candidates.append((path, 'user search path'))
            elif path.is_dir():
                for command in self.commands:
                    for suffix in ('.exe', '.cmd', ''):
                        if (path / (command + suffix)).is_file(): candidates.append((path / (command + suffix), 'user search path'))
        for command in self.commands:
            found = shutil.which(command)
            if found: candidates.append((Path(found), 'PATH'))
        for pattern in self.default_locations():
            matches = sorted(glob.glob(os.path.expandvars(pattern)), key=lambda p: os.path.getmtime(p), reverse=True)
            candidates += [(Path(m), 'default install location') for m in matches]
        for path, source in candidates:
            path = self.resolve(path)
            if path and path.is_file():
                return dict(found=True, executable=str(path.resolve()), source=source)
        return dict(found=False, executable=None, source=None)

    def failure_reason(self, output):
        """A short, user-readable reason from the output of a run that exited with an error, if the CLI gave one."""
        return None

    def resolve(self, path):
        """Turn a package-manager shim into the real executable when the CLI ships one. Default: as found."""
        if path.suffix.lower() == '.ps1':
            for suffix in ('.cmd', '.exe'):
                if path.with_suffix(suffix).is_file(): return path.with_suffix(suffix)
            return None
        return path

    def version(self, executable, secret_names=()):
        done = subprocess.run([str(executable), '--version'], capture_output=True, timeout=12, env=clean_environment(secret_names),
                              creationflags=NO_WINDOW)
        text = done.stdout.decode('utf-8', 'replace').strip()
        if done.returncode or not text: raise ValueError(f'{self.label} --version exit {done.returncode}')
        return text.splitlines()[0][:200]

    # ---- Model List ----
    def list_models(self, cfg, timeout=40):
        """Models the CLI itself reports, as dicts with id, label, description, effort_values, is_default.
        Raise when the CLI cannot be asked: the interface then says the list is an assumption or lets the user type an ID."""
        raise NotImplementedError

    # ---- Authentication ----
    def auth_status(self, cfg):
        """Signed in or not, from the vendor's own status command only. Returns
        dict(status='signed-in'|'signed-out'|'UNKNOWN', method=str|None, source=str). Never returns account names or emails,
        never reads credential files, never starts a login."""
        return dict(status='UNKNOWN', method=None, source='no official status command known')

    # ---- Generate ----
    def command(self, cfg, directory, mcp, baseline=False, search=False):
        """Non-interactive command line. `mcp` is {"command": ..., "args": [...]} for the room tool server, to be offered
        unless `baseline`. Must not enable file-writing or shell tools; the working directory is an empty sandbox."""
        raise NotImplementedError

    def prepare_input(self, cfg, command, prompt, payload, snapshot):
        """Final command and stdin bytes for this reply (e.g. to attach images). Default: prompt as UTF-8 on stdin."""
        return command, prompt.encode('utf-8')

    def parse(self, cfg, output):
        """Turn the CLI's machine-readable output into dict(text, model, requested_model, usage, usage_fields, tool_calls, ...).
        Raise if there is no successful final answer: nothing is published then."""
        raise NotImplementedError

    def tool_event(self, event):
        """1 if this output event is one tool call (used only to stop a runaway loop), else 0."""
        return 0

    def progress_event(self, event):
        """True if this output event shows text being produced (keeps the reply alive in the interface)."""
        return False

    # ---- Usage ----
    def usage(self, raw):
        """Unified usage fields; anything the CLI does not report stays 'UNKNOWN'."""
        raw = raw if isinstance(raw, dict) else {}
        return dict(input_tokens=raw.get('input_tokens', 'UNKNOWN'), cache_creation_input_tokens='UNKNOWN',
                    cache_read_input_tokens='UNKNOWN', output_tokens=raw.get('output_tokens', 'UNKNOWN'),
                    input_semantics='UNKNOWN', cache_creation_detail='UNKNOWN', subscription_debit='UNKNOWN', billed_amount='UNKNOWN')

    # ---- helpers ----
    @staticmethod
    def json_lines(output):
        events = []
        for line in output.splitlines():
            if line.strip().startswith('{'):
                try: events.append(json.loads(line))
                except ValueError: pass
        return events

    def describe(self):
        return dict(name=self.name, label=self.label, commands=list(self.commands), effort_values=list(self.effort_values),
                    capabilities={k: dict(value=v[0], source=v[1]) for k, v in self.capabilities.items()})
