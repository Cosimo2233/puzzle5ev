"""只读核对安装文件、机械配置、显示与自启；不访问相机或执行器。"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sysconfig
import time
import zipfile

import typer

from ec_26_e.tasks.execution_lock import TaskExecutionLock
from install_robust_release import BASE, CONFIG, INSTALL_FILES, config_hashes
from validate_menu_runtime import cursor_info

app = typer.Typer(pretty_exceptions_show_locals=False)


def read(path):
    return json.loads(path.read_text())


@app.command()
def main(release: Path = typer.Option(...), output: Path = typer.Option(...)):
    with TaskExecutionLock():
        deployed = read(release/'deployed.json')
        before = read(release/'hardware-before.json')
        assert config_hashes() == deployed['config_sha256'], '配置发生变化'
        assert Path('/proc/sys/kernel/random/boot_id').read_text().strip() == before['boot_id']
        assert read(Path('/run/ec26e/pl-status.json')) == before['pl_status']
        for name in INSTALL_FILES:
            assert hashlib.sha256((BASE/'install'/name).read_bytes()).hexdigest() == deployed['artifacts'][name]
        site = Path(sysconfig.get_path('purelib'))
        checked = 0
        for wheel in release.glob('*.whl'):
            assert hashlib.sha256(wheel.read_bytes()).hexdigest() == deployed['artifacts'][wheel.name]
            with zipfile.ZipFile(wheel) as archive:
                for name in archive.namelist():
                    if name.startswith(('ec_26_e/', 'puzzle_solver/', 'shapely/', 'shapely.libs/')) and not name.endswith('/'):
                        assert (site/name).read_bytes() == archive.read(name), name
                        checked += 1
        deadline = time.monotonic()+30
        while True:
            menu = read(Path('/run/ec26e/menu-status.json'))
            if menu.get('warm_task_ready') and not menu.get('busy'):
                break
            if time.monotonic() >= deadline:
                raise RuntimeError('菜单未就绪')
            time.sleep(.1)
        config = read(CONFIG/'menu.json')
        assert menu['motion_enabled'] == config['motion_enabled']
        assert menu['plan_only'] == config['plan_only']
        assert menu['cursor'] == 'arrow'
        service = read(Path('/run/ec26e/autostart-status.json'))
        assert service['state'] == 'running' and service['menu_pid'] == menu['pid']
        links = sorted(str(p) for p in Path('/etc').glob('rc*.d/*ec26e*'))
        assert links
        os.environ['DISPLAY'] = ':0'
        screen = subprocess.check_output(['xset', 'q'], text=True)
        assert 'DPMS is Disabled' in screen
        assert 'timeout:  0' in screen
        cursor = cursor_info()
        assert cursor['visible_pixels'] > 0
        import shapely
        report = dict(passed=True, checked_package_files=checked, config_unchanged=True,
            hardware_unchanged=True, rebooted=False, motion_executed=False,
            menu=menu, service=service, autostart_links=links, cursor=cursor,
            screen_blanking_disabled=True, shapely=shapely.__version__, geos=shapely.geos_version_string)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    app()
