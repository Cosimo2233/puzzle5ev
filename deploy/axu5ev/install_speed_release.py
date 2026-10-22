"""Install the validated software release, preserving mechanics and motion authorization.

Run with the board's Python environment, without the validation PYTHONPATH.
No PL download, reboot, camera capture or motion command is issued here.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig
import time

from ec_26_e.tasks.execution_lock import TaskExecutionLock

BASE = Path('/opt/ec26e')
CONFIG = BASE/'config/axu5ev'
SERVICE = '/etc/init.d/ec26e-menu'
STATUS = Path('/run/ec26e/autostart-status.json')
PACKAGES = ('ec_26_e', 'puzzle_solver', 'ec_26_e-0.1.0.dist-info', 'puzzle_solver-0.1.0.dist-info')


def service_environment():
    # Non-login SSH on this image has only /usr/bin:/bin; SysV needs /sbin.
    environment = dict(os.environ)
    environment['PATH'] = environment.get('PATH', '') + ':/sbin:/usr/sbin:/bin:/usr/bin'
    if shutil.which('start-stop-daemon', path=environment['PATH']) is None:
        raise RuntimeError('SysV start-stop-daemon is unavailable')
    return environment


def wait_service(running, old_pid=None):
    deadline = time.monotonic()+45
    while time.monotonic() < deadline:
        state = json.loads(STATUS.read_text()) if STATUS.exists() else {}
        if running and state.get('state') == 'running' and state.get('pid') != old_pid:
            return
        if not running and (old_pid is None or not Path(f'/proc/{old_pid}').exists()):
            return
        time.sleep(.2)
    raise RuntimeError(f'Menu service did not reach running={running}: {state}')


def stop_service():
    state = json.loads(STATUS.read_text()) if STATUS.exists() else {}
    old_pid = state.get('pid')
    subprocess.run([SERVICE, 'stop'], check=True, env=service_environment())
    wait_service(False, old_pid)
    return old_pid


def start_service(old_pid):
    subprocess.run([SERVICE, 'start'], check=True, env=service_environment())
    wait_service(True, old_pid)


def restore(backup, site):
    for name in PACKAGES:
        destination = site/name
        assert destination.parent == site and site.is_relative_to(BASE)
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(backup/'packages'/name, destination)
    for relative in ('machine.env', 'menu.json', 'tasks/fixed.json', 'tasks/random.json'):
        shutil.copy2(backup/'config'/relative, CONFIG/relative)
    shutil.copy2(backup/'start_menu.sh', BASE/'install/start_menu.sh')


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--release', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--rollback', action='store_true')
    args = parser.parse_args()
    if not args.release.resolve().is_relative_to(BASE):
        raise ValueError('Release must be under /opt/ec26e')
    site = Path(sysconfig.get_path('purelib')).resolve()
    if not site.is_relative_to(BASE):
        raise ValueError('Use /opt/ec26e/venv-python311/bin/python')
    backup = args.release/'production-before'
    if not args.apply:
        print(json.dumps(dict(release=str(args.release), backup=str(backup), rollback=args.rollback,
            changes=['software packages', 'continuous review', 'PL backends', 'module preloading'],
            mechanics='preserved', motion_authorization='preserved', hardware='unchanged')))
        return
    with TaskExecutionLock():
        menu = json.loads((CONFIG/'menu.json').read_text())
        assert menu['board'] == 'axu5ev'
        if args.rollback:
            assert (backup/'complete').is_file(), 'Incomplete rollback snapshot'
            old_pid = stop_service()
            restore(backup, site)
            start_service(old_pid)
            print('Rolled back software and configuration; no task started.')
            return
        assert not backup.exists(), 'Existing backup is immutable; use a new release directory'
        manifest = json.loads((args.release/'manifest.json').read_text())
        for name, expected in manifest['artifacts'].items():
            assert hashlib.sha256((args.release/name).read_bytes()).hexdigest() == expected, name
        backup.mkdir()
        shutil.copytree(CONFIG, backup/'config')
        shutil.copy2(BASE/'install/start_menu.sh', backup/'start_menu.sh')
        for name in PACKAGES:
            shutil.copytree(site/name, backup/'packages'/name)
        (backup/'complete').write_text('complete\n')
        old_pid = stop_service()
        try:
            subprocess.run([sys.executable, '-m', 'pip', 'install', '--no-deps', '--force-reinstall',
                str(args.release/'ec_26_e-0.1.0-py3-none-any.whl'),
                str(args.release/'puzzle_solver-0.1.0-py3-none-any.whl')], check=True)
            for mode in ('fixed', 'random'):
                path = CONFIG/'tasks'/f'{mode}.json'
                task = json.loads(path.read_text())
                task['task'].update(minimum_stable_frames=6, continuous_review=True)
                if mode == 'random':
                    task['detect1']['max_vertices'] = 5
                    task['planning']['competition_rules'] = True
                path.write_text(json.dumps(task, ensure_ascii=False, indent=2)+'\n')
            changes = dict(TASK1_REVIEW_CYCLE_SECONDS='2', TASK2_REVIEW_CYCLE_SECONDS='2',
                           EC_PREPROCESS_BACKEND='pl', EC_RIGID_BACKEND='pl', EC_CLEARANCE_BACKEND='pl')
            lines = (CONFIG/'machine.env').read_text().splitlines()
            for index, line in enumerate(lines):
                key = line.partition('=')[0]
                if key in changes:
                    lines[index] = key+'='+changes.pop(key)
            lines.extend(key+'='+value for key, value in changes.items())
            (CONFIG/'machine.env').write_text('\n'.join(lines)+'\n')
            menu.update(preprocess_backend='pl', rigid_backend='pl', clearance_backend='pl', warm_tasks=True)
            (CONFIG/'menu.json').write_text(json.dumps(menu, ensure_ascii=False, indent=2)+'\n')
            shutil.copyfile(args.release/'start_menu.sh', BASE/'install/start_menu.sh')
            (BASE/'install/start_menu.sh').chmod(0o755)
            subprocess.run([sys.executable, '-c',
                'from ec_26_e.gui.board_profile import load_menu_config; '
                'from ec_26_e.gui.warm_task import prepare; prepare(); load_menu_config()'], check=True)
            start_service(old_pid)
        except BaseException:
            stop_service()
            restore(backup, site)
            start_service(old_pid)
            raise
        (args.release/'deployed.json').write_text(json.dumps(dict(
            artifacts=manifest['artifacts'], motion_tested=False, hardware_changed=False,
            preserved_menu_fields={k:v for k,v in menu.items() if k not in (
                'preprocess_backend','rigid_backend','clearance_backend','warm_tasks')}), indent=2))
        print('Installed; menu started. Mechanics and motion authorization preserved; no task started.')


if __name__ == '__main__':
    main()
