#!/usr/bin/env python3
"""Build a reproducible, allowlisted installable Skill ZIP without runtime files."""
import hashlib
from pathlib import Path
import zipfile
from check_release import ROOT, SKILL_ROOT, audit


def main():
    manifest, _ = audit()
    name = f'codex-skill-atlas-v{manifest["version"]}.zip'
    output = ROOT/'dist';output.mkdir(exist_ok=True)
    target = output/name
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(manifest['skillFiles']):
            info = zipfile.ZipInfo('codex-skill-atlas/'+file, date_time=(2026, 1, 1, 0, 0, 0))
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, (ROOT/SKILL_ROOT/file).read_bytes())
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    (output/'SHA256SUMS.txt').write_text(f'{digest}  {name}\n')
    print(f'{target.name}: {target.stat().st_size} bytes, sha256 {digest}')


if __name__ == '__main__':
    main()
