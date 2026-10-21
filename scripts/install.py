#!/usr/bin/env python3
"""Install the bundled skill from a checked-out repository; never overwrite an existing skill."""
import argparse
import json
import os
from pathlib import Path
import shutil
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dest', type=Path, default=Path(os.environ.get('CODEX_HOME', Path.home()/'.codex'))/'skills/codex-skill-atlas')
    args = parser.parse_args()
    dest = args.dest.expanduser().absolute()
    if os.path.lexists(dest):
        parser.exit(1, 'Destination already exists; no files changed. Stop the service and back up the existing installation before updating.\n')
    source = ROOT/'skills/codex-skill-atlas'
    manifest = json.loads((ROOT/'release-files.json').read_text(encoding='utf-8'))
    dest.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.skill-atlas-install-', dir=dest.parent))
    try:
        for name in manifest['skillFiles']:
            file = source/name
            if any((source/Path(*Path(name).parts[:i])).is_symlink() for i in range(1, len(Path(name).parts)+1)):
                raise ValueError('Symlinks are not valid installation sources')
            if not file.is_file() or not file.resolve().is_relative_to(source.resolve()):
                raise ValueError('Invalid installation source')
            target = staging/name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(file, target)
        # Claim the destination exclusively, including protection against concurrent installs.
        dest.mkdir()
        try:
            for child in staging.iterdir():
                shutil.move(str(child), dest/child.name)
        except Exception:
            shutil.rmtree(dest)
            raise
    finally:
        shutil.rmtree(staging)
    print(json.dumps({'installed': str(dest), 'next': 'Restart the agent session, then ask it to use $codex-skill-atlas.'}))


if __name__ == '__main__':
    main()
