"""Running official subscription CLIs as chat backends. No API fallback or credential file access.

What is specific to one CLI (command line, input, output format, model list, sign-in status, usage) lives in its adapter
under cli_adapters/. This module only owns the process: sandbox directory, job-object ownership, output limits,
timeouts and cancellation, which are the same for every CLI.
"""
import ctypes
import json
import os
import subprocess
import threading
import time
from pathlib import Path

import cli_adapters
from cli_adapters import clean_environment, ROOM_TOOLS
from chattera_paths import python_script

TOOLS = list(ROOM_TOOLS)


def validate_cli(name, config):
    path = Path(config.get("executable", ""))
    if not path.is_absolute() or not path.is_file():
        raise ValueError(f"{name} CLI unavailable at {path}. Install it with its vendor's installer, or add its folder under "
                         "Settings → Models → Local CLI search paths; no automatic installation.")
    try:
        result = subprocess.run([str(path), "--version"], capture_output=True, timeout=10,
                                env=clean_environment(config.get('secret_env_names', [])), creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"{name} CLI version check failed at {path}: {type(exc).__name__}") from exc
    if result.returncode or not result.stdout.strip():
        raise ValueError(f"{name} CLI is not runnable at {path}; --version exit {result.returncode}")
    return dict(executable=str(path), version=result.stdout.decode('utf-8', 'replace').strip()[:200])


def usage_fields(name, usage):
    adapter = cli_adapters.get(name)
    return adapter.usage(usage) if adapter else cli_adapters.CLIAdapter().usage(usage)


class ProcessTree:
    """Windows job ownership: all descendants die on termination or parent-server exit."""
    def __init__(self, process):
        self.process, self.handle = process, None
        if os.name == "nt":
            from ctypes import wintypes as w
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.CreateJobObjectW.restype = w.HANDLE
            kernel.CreateJobObjectW.argtypes = [w.LPVOID, w.LPCWSTR]
            kernel.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD]
            kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
            kernel.TerminateJobObject.argtypes = [w.HANDLE, w.UINT]
            kernel.CloseHandle.argtypes = [w.HANDLE]
            class Basic(ctypes.Structure):
                _fields_ = [("per_process", ctypes.c_longlong), ("per_job", ctypes.c_longlong),
                            ("flags", w.DWORD), ("min_work", ctypes.c_size_t),
                            ("max_work", ctypes.c_size_t), ("active", w.DWORD),
                            ("affinity", ctypes.c_size_t), ("priority", w.DWORD), ("scheduling", w.DWORD)]
            class Extended(ctypes.Structure):
                _fields_ = [("basic", Basic), ("io", ctypes.c_ulonglong * 6),
                            ("process_mem", ctypes.c_size_t), ("job_mem", ctypes.c_size_t),
                            ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]
            limits = Extended()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            self.kernel = kernel
            self.handle = kernel.CreateJobObjectW(None, None)
            if not self.handle or not kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)) or not kernel.AssignProcessToJobObject(self.handle, w.HANDLE(process._handle)):
                process.kill()
                self.close()
                raise OSError("Cannot establish child-process ownership")

    def kill(self):
        if self.handle:
            self.kernel.TerminateJobObject(self.handle, 1)
        elif self.process.poll() is None:
            self.process.kill()

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None

    def resume(self):
        if os.name == "nt":
            from ctypes import wintypes as w
            resume = ctypes.WinDLL("ntdll").NtResumeProcess
            resume.argtypes = [w.HANDLE]
            if resume(w.HANDLE(self.process._handle)) != 0:
                self.kill()
                raise OSError("Cannot resume owned process")


class CLIBackend:
    def __init__(self, name, config):
        self.name, self.config = name, config
        self.adapter = cli_adapters.get(name)
        if self.adapter is None: raise ValueError(f'No CLI adapter named {name}')

    def available(self):
        return self.config.get('enabled', True) is not False

    def command(self, directory, baseline=False, search=False):
        script = python_script('chat_task_mcp')
        mcp = {"command": script[0], "args": [*script[1:], str(directory / "snapshot.json")]}
        return self.adapter.command(self.config, directory, mcp, baseline, search)

    def run(self, prompt, task, attach, cancel):
        if "executable" in self.config and not Path(self.config["executable"]).is_file():
            raise ValueError(f"{self.name} CLI path no longer exists; reinstall it or update its search path in the model settings")
        directory = Path(task["directory"])
        try: frozen = json.loads((directory / 'snapshot.json').read_text('utf-8'))
        except (OSError, ValueError): frozen = {}
        # Official web search of the CLI itself, only when this reply was started with it (frozen in the snapshot).
        native = (frozen.get('search') or {}).get('path') == 'native'
        from chat_search import CliTrace
        trace = CliTrace(self.name, getattr(attach, 'search', None))
        command = self.command(directory, task.get("baseline", False), **({'search': True} if native else {}))
        payload = json.loads(prompt)
        command, stdin_data = self.adapter.prepare_input(self.config, command, prompt, payload, frozen)
        def spawn():
            proc = subprocess.Popen(command, cwd=directory, env=clean_environment(self.config.get('secret_env_names', [])),
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    creationflags=(subprocess.CREATE_NO_WINDOW | 0x4) if os.name == "nt" else 0)
            tree = ProcessTree(proc)
            tree.resume()
            return tree
        if hasattr(attach, "spawn"):
            tree = attach.spawn(spawn)
        else:
            if cancel.is_set():
                raise TimeoutError("Cancelled before process creation")
            tree = spawn()
            attach(tree)
        proc = tree.process
        chunks, errors, byte_count = [], [], [0]
        def drain(stream, output):
            tool_count = 0
            while block := stream.readline(1_000_000):
                byte_count[0] += len(block)
                if byte_count[0] > 2_000_000:
                    tree.kill()
                    break
                output.append(block)
                if output is chunks:
                    try:
                        event = json.loads(block)
                        if native: trace.feed(event)
                        if hasattr(attach, "progress") and self.adapter.progress_event(event):
                            attach.progress()
                        tool_count += self.adapter.tool_event(event)
                        if tool_count > (40 if native else 28):  # the tool server itself refuses politely after 8; this only stops a runaway loop
                            tree.kill()
                    except (ValueError, TypeError, AttributeError):
                        pass
        readers = [threading.Thread(target=drain, args=(proc.stdout, chunks), daemon=True),
                   threading.Thread(target=drain, args=(proc.stderr, errors), daemon=True)]
        for thread in readers:
            thread.start()
        try:
            if cancel.is_set():
                tree.kill()
            else:
                proc.stdin.write(stdin_data)
                proc.stdin.close()
            deadline = time.monotonic() + self.config.get("timeout", 120)
            while proc.poll() is None:
                if cancel.wait(.05) or time.monotonic() >= deadline:
                    tree.kill()
                    raise TimeoutError("Cancelled" if cancel.is_set() else "CLI total timeout (120s default); not an MCP/network timeout")
            for thread in readers:
                thread.join(5)
            if proc.returncode or byte_count[0] > 2_000_000:
                # No raw stderr logging: it can include unrelated configuration details.
                raise RuntimeError(f"{self.name} CLI exit {proc.returncode}; no reply committed")
            result = self.parse(b"".join(chunks).decode("utf-8", "replace"))
            if native: result['search_trace'] = trace.entries
            return result
        finally:
            tree.kill()
            proc.wait(timeout=5)
            tree.close()

    def parse(self, output):
        return self.adapter.parse(self.config, output)


class MockBackend:
    def run(self, prompt, task, attach, cancel):
        if cancel.wait(task.get("mock_delay", .2)):
            raise TimeoutError("Cancelled")
        value = json.loads(prompt)
        return dict(text="Mock response to #" + str(value["current"]["id"]), model="mock",
                    usage="UNKNOWN", rate_limits="UNKNOWN")
