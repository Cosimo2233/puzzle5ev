"""Package previously built wheels with hashes; this does not build or deploy."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    solver = root.parent/'puzzle-solver'
    args.output.mkdir(parents=True, exist_ok=True)
    artifacts = [root/'sw/dist/ec_26_e-0.1.0-py3-none-any.whl',
                 solver/'dist/puzzle_solver-0.1.0-py3-none-any.whl',
                 root/'deploy/axu5ev/start_menu.sh',
                 root/'deploy/axu5ev/install_speed_release.py']
    for source in artifacts:
        shutil.copy2(source, args.output/source.name)
    sources = {}
    for base, folder in ((root, 'sw/src'), (root, 'deploy/axu5ev'), (solver, 'src')):
        for path in (base/folder).rglob('*'):
            if path.is_file() and path.suffix in ('.py', '.sh', '.json'):
                sources[base.name+'/'+path.relative_to(base).as_posix()] = sha(path)
    manifest = dict(source_date='2026-10-21', sources=sources,
                    artifacts={p.name: sha(args.output/p.name) for p in artifacts},
                    motion_tested=False, hardware_changed=False)
    (args.output/'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(json.dumps(manifest['artifacts']))


if __name__ == '__main__':
    main()
