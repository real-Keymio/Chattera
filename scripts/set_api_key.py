"""Store one API key for a connection from a terminal (the settings screen does the same).

Explicit human input only: the key is read from a hidden prompt or stdin, never from argv, and never printed.
Usage:  python scripts/set_api_key.py --directory "%LOCALAPPDATA%\\Chattera\\keys" --profile deepseek
"""
import argparse
import getpass
import os
import re
import subprocess
import sys
from pathlib import Path

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--directory',required=True,help='Absolute path of the keys folder in the Chattera data home')
    parser.add_argument('--profile',required=True)
    parser.add_argument('--stdin',action='store_true',help='Read the provided key from stdin instead of terminal prompt')
    args=parser.parse_args()
    root=Path(args.directory)
    if not root.is_absolute() or root.name not in ('keys','.multichat-private') or root.is_symlink() or not re.fullmatch('[a-z][a-z0-9_-]{0,63}',args.profile):raise SystemExit('Invalid private location or profile')
    value=(sys.stdin.read() if args.stdin else getpass.getpass('API key (not displayed): ')).strip()
    if len(value)<8 or any(c.isspace() for c in value):raise SystemExit('Invalid key format')
    root.mkdir(parents=True,exist_ok=True)
    if os.name=='nt':
        identity=subprocess.check_output(['whoami'],text=True).strip()
        result=subprocess.run(['icacls',str(root),'/inheritance:r','/grant:r',identity+':(OI)(CI)F'],capture_output=True)
        if result.returncode:raise SystemExit('Private directory ACL failed; no key written')
    else:root.chmod(0o700)
    path=root/(args.profile+'.key')
    if path.exists():raise SystemExit('Key file already exists; explicitly preserve/rotate it first')
    with path.open('x',encoding='utf-8') as stream:stream.write(value)
    if os.name!='nt':path.chmod(0o600)
    print('Private API key saved; value omitted.')

if __name__=='__main__':main()
