#!/usr/bin/env python3
"""Launch or reuse the local Skill Atlas and open its browser UI."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from urllib.parse import urlparse
from urllib.request import build_opener, ProxyHandler, Request
import webbrowser

SKILL = Path(__file__).resolve().parents[1]
APP = SKILL/'app'
sys.dont_write_bytecode = True
sys.path.insert(0, str(APP))
from platform_support import background_options, file_lock


def read_json(path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def healthy(record, key):
    if not isinstance(record, dict) or record.get('instanceKey') != key:
        return False
    try:
        url = urlparse(record.get('url', ''))
        if url.scheme != 'http' or url.hostname != '127.0.0.1' or not url.port or url.path or url.query or url.fragment or url.username:
            return False
        with build_opener(ProxyHandler({})).open(record['url']+'/api/health', timeout=1) as response:
            data = json.load(response)
        return (data.get('service') == 'codex-skill-atlas' and data.get('instanceKey') == key
                and isinstance(record.get('pid'), int) and record['pid'] > 1 and data.get('pid') == record['pid'])
    except (OSError, ValueError, TypeError):
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--no-open', action='store_true', help='Return URL for an agent to open in its browser')
    parser.add_argument('--port', type=int, default=8766, help='Preferred loopback port; chooses a free port if occupied')
    parser.add_argument('--codex-home', type=Path, default=Path(os.environ.get('CODEX_HOME', Path.home()/'.codex')))
    parser.add_argument('--agents-home', type=Path, default=Path.home()/'.agents')
    parser.add_argument('--data-dir', type=Path)
    parser.add_argument('--skills-root', type=Path, action='append', default=[])
    parser.add_argument('--stop', action='store_true', help='Stop only the matching verified Skill Atlas service')
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error('port must be between 0 and 65535')
    home = args.codex_home.expanduser().resolve()
    agents = args.agents_home.expanduser().resolve()
    data_dir = (args.data_dir or home/'skill-atlas').expanduser().resolve()
    if data_dir == SKILL or SKILL in data_dir.parents:
        parser.error('Runtime data must be outside the installed skill directory.')
    roots = sorted(str(p.expanduser().resolve()) for p in args.skills_root)
    config = [str(APP), str(home), str(agents), str(data_dir), roots]
    key = hashlib.sha256(json.dumps(config).encode()).hexdigest()
    os.umask(0o077)
    data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    metadata = data_dir/'server.json'
    with file_lock(data_dir/'launch.lock'):
        record = read_json(metadata)
        running = healthy(record, key)
        if args.stop:
            if running:
                token = record.get('shutdownToken')
                if token:
                    request = Request(record['url']+'/api/shutdown',
                                      data=json.dumps({'token': token}).encode(),
                                      headers={'Content-Type': 'application/json', 'Origin': record['url']})
                    with build_opener(ProxyHandler({})).open(request, timeout=3) as response:
                        response.read()
                elif os.name != 'nt':
                    # Older POSIX versions did not yet have the authenticated stop endpoint.
                    os.kill(record['pid'], signal.SIGTERM)
                else:
                    parser.exit(1, 'This instance does not support safe shutdown.\n')
                deadline = time.monotonic()+15
                while time.monotonic() < deadline:
                    if (not token or not metadata.exists()) and not healthy(record, key):
                        break
                    time.sleep(.1)
                else:
                    parser.exit(1, 'Service did not stop; no forced termination attempted.\n')
                metadata.unlink(missing_ok=True)
            print(json.dumps({'status': 'stopped' if running else 'not-running'}))
            return
        # Another installation or profile may own this runtime folder. Never overwrite its state.
        if not running and isinstance(record, dict) and record.get('instanceKey') and healthy(record, record['instanceKey']):
            parser.exit(1, 'This data directory belongs to another running Skill Atlas. Use a separate --data-dir.\n')
        if not running:
            command = [sys.executable, '-B', str(APP/'server.py'), '--port', str(args.port), '--fallback-port',
                       '--codex-home', str(home), '--agents-home', str(agents), '--data-dir', str(data_dir),
                       '--instance-key', key, '--ready-file', str(metadata)]
            for root in roots:
                command += ['--skills-root', root]
            with (data_dir/'server.log').open('ab') as log:
                process = subprocess.Popen(command, cwd=APP, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                           **background_options(),
                                           env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUTF8': '1'})
            deadline = time.monotonic()+15
            while time.monotonic() < deadline:
                record = read_json(metadata)
                if healthy(record, key):
                    break
                if process.poll() is not None:
                    parser.exit(1, f'Startup failed. Inspect {data_dir / "server.log"}\n')
                time.sleep(.1)
            else:
                process.terminate()
                parser.exit(1, f'Startup timed out. Inspect {data_dir / "server.log"}\n')
    opened = False
    if not args.no_open:
        try:
            opened = bool(webbrowser.open(record['url']))
        except webbrowser.Error:
            pass
    print(json.dumps({'status': 'reused' if running else 'started', 'url': record['url'],
                      'pid': record['pid'], 'browserOpened': opened}, ensure_ascii=False))


if __name__ == '__main__':
    main()
