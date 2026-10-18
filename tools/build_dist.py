#!/usr/bin/env python3
"""Build deterministic, allowlisted local artifacts; never upload or publish."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
STAMP = (1980, 1, 1, 0, 0, 0)


def safe_read(root, name):
    path = PurePosixPath(name)
    if not name or path.is_absolute() or '..' in path.parts or '\\' in name or ':' in name or str(path) != name:
        raise ValueError('non-canonical distribution path')
    candidate = root / name
    candidate.resolve().relative_to(root.resolve())
    cursor = root
    for part in path.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError('symlinks are not distribution inputs')
    with candidate.open('rb') as handle:
        data = handle.read(2_000_001)
    if len(data) > 2_000_000 or b'\0' in data:
        raise ValueError('distribution input is not bounded text')
    return data.decode('utf-8').replace('\r\n', '\n').encode('utf-8')


def package_files(root):
    names = [line for line in safe_read(root, 'distribution/files.txt').decode().splitlines()
             if line and not line.startswith('#')]
    if len(names) != len(set(names)):
        raise ValueError('duplicate distribution path')
    blocked = {'.git', '.codex', '.agents', 'user-data', 'private', 'dist', 'artifacts', 'tests', '__pycache__'}
    for name in names:
        if blocked.intersection(PurePosixPath(name).parts) or (name != 'LICENSE' and Path(name).suffix not in {'.py', '.ps1', '.sh', '.md', '.json'}):
            raise ValueError('disallowed distribution input')
    files = {name: safe_read(root, name) for name in names}
    manifest = json.loads(files['plugin.json'])
    if manifest['name'] != 'persona-review' or not re.fullmatch(r'\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?', manifest['version']):
        raise ValueError('invalid package identity/version')
    for name in ('scripts/persona_review.py', 'scripts/persona-review.sh', 'scripts/persona-review.ps1',
                 'skills/review/SKILL.md', 'skills/review-config/SKILL.md', 'LICENSE'):
        if name not in files:
            raise ValueError('required runtime file is missing')
    return manifest, files


def zip_bytes(files):
    result = io.BytesIO()
    with zipfile.ZipFile(result, 'w', compression=zipfile.ZIP_STORED) as archive:
        for name, data in sorted(files.items()):
            entry = zipfile.ZipInfo(name, STAMP)
            entry.create_system = 3
            mode = 0o755 if name.endswith('.sh') else 0o644
            entry.external_attr = (stat.S_IFREG | mode) << 16
            archive.writestr(entry, data)
    return result.getvalue()


def artifacts(root=ROOT):
    manifest, files = package_files(root)
    marketplace = json.loads(safe_read(root, 'distribution/marketplace.json'))
    entries = marketplace['plugins']
    if not isinstance(entries, list) or len(entries) != 1:
        raise ValueError('marketplace must contain exactly one plugin')
    entry = entries[0]
    if entry['name'] != manifest['name'] or entry['source'] != {
        'source': 'local', 'path': './plugins/persona-review'}:
        raise ValueError('marketplace must resolve exactly to the bundled plugin')
    local = {'persona-review-marketplace/plugins/persona-review/'+name: data for name, data in files.items()}
    local['persona-review-marketplace/.agents/plugins/marketplace.json'] = (
        json.dumps(marketplace, ensure_ascii=False, indent=2)+'\n').encode('utf-8')
    version = manifest['version']
    outputs = {
        f'persona-review-{version}.zip': zip_bytes({'persona-review/'+name:data for name,data in files.items()}),
        f'persona-review-marketplace-{version}.zip': zip_bytes(local),
    }
    sums = ''.join(hashlib.sha256(data).hexdigest()+'  '+name+'\n' for name,data in sorted(outputs.items()))
    outputs['SHA256SUMS'] = sums.encode('ascii')
    return outputs


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'dist')
    parser.add_argument('--check', action='store_true', help='compare existing artifacts with a fresh deterministic build')
    args = parser.parse_args(argv)
    try:
        outputs = artifacts()
        # Validate every existing target before writing any new file.
        for name, data in outputs.items():
            target = args.output/name
            if target.exists() and target.read_bytes() != data:
                raise ValueError('existing artifact differs; choose a new output directory')
            if args.check and not target.is_file():
                raise ValueError('artifact is missing')
        if not args.check:
            args.output.mkdir(parents=True, exist_ok=True)
            for name, data in outputs.items():
                target = args.output/name
                if not target.exists():
                    with target.open('xb') as handle:
                        handle.write(data)
        print(json.dumps({'checked':args.check, 'artifacts':{
            name:hashlib.sha256(data).hexdigest() for name,data in outputs.items()}}))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print('Distribution error: '+str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
