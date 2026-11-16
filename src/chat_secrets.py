"""Dedicated API secrets only. Never reads CLI authentication stores."""
import json
import logging
import os
import threading
import traceback
from pathlib import Path

_values=set()
_lock=threading.Lock()
SENSITIVE={'authorization','x-api-key','x-goog-api-key','api_key','apikey','access_token','secret','key_value'}

def remember(value):
    if value:
        with _lock: _values.add(value)
    return value

def redact(value):
    if isinstance(value,dict): return {redact(k):('[REDACTED]' if str(k).lower() in SENSITIVE else redact(v)) for k,v in value.items()}
    if isinstance(value,list): return [redact(v) for v in value]
    if isinstance(value,str):
        with _lock: secrets=tuple(_values)
        for secret in secrets: value=value.replace(secret,'[REDACTED]')
    return value

# Dedicated key folders: `keys` inside the Chattera data home; `.multichat-private` for installs from before the rename.
KEY_DIRS={'keys','.multichat-private'}

def key_path(value):
    path=Path(value)
    if not path.is_absolute() or path.parent.name not in KEY_DIRS or path.suffix!='.key' or path.is_symlink():
        raise ValueError('Key file must be an absolute keys/*.key path inside the data folder')
    return path

def get_key(cfg):
    if cfg.get('mock'): return ''
    if cfg.get('key_file'):
        path=key_path(cfg['key_file'])
        if not path.exists(): return ''
        if path.stat().st_size>16384: raise ValueError('Invalid private key file')
        value=path.read_text('utf-8').strip()
    else: value=os.environ.get(cfg.get('key_env',''),'').strip()
    if value and (len(value)<8 or any(c.isspace() for c in value)): raise ValueError('Invalid API key format')
    return remember(value)

class RedactingFilter(logging.Filter):
    def filter(self,record):
        record.msg=redact(record.getMessage());record.args=()
        # Tracebacks from HTTP clients may include request details; no raw traceback in API logs.
        if record.name.startswith(('httpx','httpcore')): record.exc_info=None;record.exc_text=None
        elif record.exc_info:
            record.exc_text=redact(''.join(traceback.format_exception(*record.exc_info)));record.exc_info=None
        elif record.exc_text:record.exc_text=redact(record.exc_text)
        return True

def install_logging_filter():
    for handler in logging.getLogger().handlers:
        if not any(isinstance(f,RedactingFilter) for f in handler.filters):handler.addFilter(RedactingFilter())
