"""Read-only post-install verification; no camera, PL request or motion command."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import sysconfig
import time
import zipfile

from ec_26_e.tasks.execution_lock import TaskExecutionLock


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--release', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    config = Path('/opt/ec26e/config/axu5ev')
    backup = args.release/'production-before/config'
    with TaskExecutionLock():
        menu = read_json(config/'menu.json')
        expected_menu = read_json(backup/'menu.json')
        expected_menu.update(preprocess_backend='pl', rigid_backend='pl',
                             clearance_backend='pl', warm_tasks=True)
        assert menu == expected_menu, 'Unexpected menu configuration changes'
        for mode in ('fixed', 'random'):
            task = read_json(config/'tasks'/f'{mode}.json')
            expected = copy.deepcopy(read_json(backup/'tasks'/f'{mode}.json'))
            expected['task'].update(minimum_stable_frames=6, continuous_review=True)
            if mode == 'random':
                expected['detect1']['max_vertices'] = 5
                expected['planning']['competition_rules'] = True
            assert task == expected, 'Unexpected task/mechanical changes: '+mode
        changes = dict(TASK1_REVIEW_CYCLE_SECONDS='2', TASK2_REVIEW_CYCLE_SECONDS='2',
                       EC_PREPROCESS_BACKEND='pl', EC_RIGID_BACKEND='pl', EC_CLEARANCE_BACKEND='pl')
        lines = (backup/'machine.env').read_text(encoding='utf-8').splitlines()
        for index, line in enumerate(lines):
            key = line.partition('=')[0]
            if key in changes:
                lines[index] = key+'='+changes.pop(key)
        lines.extend(k+'='+v for k, v in changes.items())
        assert (config/'machine.env').read_text(encoding='utf-8') == '\n'.join(lines)+'\n'
        manifest = read_json(args.release/'manifest.json')
        assert hashlib.sha256(Path('/opt/ec26e/install/start_menu.sh').read_bytes()).hexdigest() == manifest['artifacts']['start_menu.sh']
        site = Path(sysconfig.get_path('purelib'))
        checked_files = 0
        for wheel in args.release.glob('*.whl'):
            assert hashlib.sha256(wheel.read_bytes()).hexdigest() == manifest['artifacts'][wheel.name]
            with zipfile.ZipFile(wheel) as archive:
                for name in archive.namelist():
                    if name.startswith(('ec_26_e/', 'puzzle_solver/')) and not name.endswith('/'):
                        assert (site/name).read_bytes() == archive.read(name), 'Installed file differs: '+name
                        checked_files += 1
        deadline = time.monotonic()+30
        while True:
            status = read_json(Path('/run/ec26e/menu-status.json'))
            if status.get('warm_task_ready') and not status.get('busy'):
                break
            if time.monotonic() >= deadline:
                raise RuntimeError('Menu not ready: '+str(status))
            time.sleep(.1)
        assert status['motion_enabled'] == menu['motion_enabled']
        assert status['plan_only'] == menu['plan_only']
        assert Path(f"/proc/{status['pid']}").exists()
        service = read_json(Path('/run/ec26e/autostart-status.json'))
        assert service['state'] == 'running' and service['menu_pid'] == status['pid']
        links = sorted(str(p) for p in Path('/etc').glob('rc*.d/*ec26e*'))
        assert links, 'No menu autostart links'
        hardware = read_json(Path('/run/ec26e/pl-status.json'))
        before = read_json(args.release/'hardware-before.json')
        assert hardware == before['pl_status'], 'Loaded hardware status changed'
        assert Path('/proc/sys/kernel/random/boot_id').read_text().strip() == before['boot_id']
        args.output.write_text(json.dumps(dict(passed=True, installed_files_checked=checked_files,
            mechanics_preserved=True, motion_authorization_preserved=True, motion_executed=False,
            hardware_unchanged=True, rebooted=False, menu=status, autostart=service,
            autostart_links=links, artifacts=manifest['artifacts']), ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
