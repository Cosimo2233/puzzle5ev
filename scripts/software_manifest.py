"""记录交付 wheel 的依赖、源码身份和哈希。"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from zipfile import ZipFile
from email.parser import Parser

def revision(path):
    commit = subprocess.run(['git', '-C', str(path), 'rev-parse', '--verify', 'HEAD'],
                            capture_output=True, text=True)
    return {
        'commit': commit.stdout.strip() if commit.returncode == 0 else None,
        'dirty': bool(subprocess.check_output(['git', '-C', str(path), 'status', '--porcelain'], text=True).strip()),
    }

def fingerprint(path):
    digest = hashlib.sha256()
    for p in sorted(path.rglob('*')):
        if p.is_file() and '__pycache__' not in p.parts:
            digest.update(p.relative_to(path).as_posix().encode())
            digest.update(b'\0')
            digest.update(p.read_bytes())
    return digest.hexdigest()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--solver', type=Path, required=True)
    parser.add_argument('--bridge', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    records = []
    for pattern in ['puzzle_solver-0.1.0-*.whl', 'puzzle_m0_bridge-0.1.0-*.whl', 'ec_26_e-0.1.0-*.whl']:
        matches = list(args.output.glob(pattern))
        if len(matches) != 1:
            raise RuntimeError(f'Expected exactly one current wheel: {pattern}')
        wheel = matches[0]
        with ZipFile(wheel) as archive:
            metadata = Parser().parsestr(archive.read(next(n for n in archive.namelist() if n.endswith('.dist-info/METADATA'))).decode())
        requirements = metadata.get_all('Requires-Dist', [])
        if any('file:' in item or '../' in item for item in requirements):
            raise RuntimeError('Deployment wheel contains a local dependency')
        records.append({'file': wheel.name, 'sha256': hashlib.sha256(wheel.read_bytes()).hexdigest(), 'requires_dist': requirements})
    data = {'hardware_validated': False, 'wheels': records,
            'solver': {**revision(args.solver), 'source_sha256': fingerprint(args.solver/'src')},
            'bridge': {**revision(args.bridge), 'source_sha256': fingerprint(args.bridge/'src')},
            'application': {**revision(args.root), 'source_sha256': fingerprint(args.root/'sw/src')}}
    (args.output/'manifest.json').write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

if __name__ == '__main__':
    main()
