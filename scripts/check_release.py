#!/usr/bin/env python3
"""Audit an explicit source allowlist before publishing or building an archive."""
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = 'skills/codex-skill-atlas/'
PATTERNS = {
    'personal absolute path': re.compile(r'/(?:Users|home)/[^\s/"\'<>]+/|[A-Z]:\\Users\\[^\\\s]+\\'),
    'GitHub credential': re.compile(r'gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,}'),
    'API credential': re.compile(r'sk-(?:proj-)?[A-Za-z0-9_-]{24,}'),
    'private key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
}


def manifest_files():
    manifest = json.loads((ROOT/'release-files.json').read_text())
    package = manifest['skillFiles']
    repo = manifest['repositoryFiles'] + [SKILL_ROOT+p for p in package]
    for name in repo + package:
        p = Path(name)
        if p.is_absolute() or '..' in p.parts or '\\' in name:
            raise ValueError('Invalid manifest path: '+name)
    if len(set(repo)) != len(repo) or len(set(package)) != len(package):
        raise ValueError('Duplicate manifest path')
    return manifest, repo


def audit():
    manifest, files = manifest_files()
    for name in files:
        file = ROOT/name
        if not file.is_file():
            raise ValueError('Missing release file: '+name)
        if any((ROOT/Path(*Path(name).parts[:i])).is_symlink() for i in range(1, len(Path(name).parts)+1)):
            raise ValueError('Symlink is not publishable: '+name)
        text = file.read_text(encoding='utf-8')
        for reason, pattern in PATTERNS.items():
            if pattern.search(text):
                # Never print a matched credential into logs.
                raise ValueError(f'{reason} found in {name}')
    if (ROOT/'.git').exists():
        tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')
        unexpected = sorted(set(filter(None, tracked))-set(files))
        if unexpected:
            raise ValueError('Unexpected tracked files: '+', '.join(unexpected))
    return manifest, files


if __name__ == '__main__':
    manifest, files = audit()
    print(f'PASS: {len(files)} public source files; {len(manifest["skillFiles"])} skill package files.')
