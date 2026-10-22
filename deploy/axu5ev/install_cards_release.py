"""部署扑克牌分割修正；只重启菜单，不重载 PL，不发送运动命令。"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig

import typer

from ec_26_e.tasks.execution_lock import TaskExecutionLock
from install_speed_release import BASE, CONFIG, PACKAGES, restore, start_service, stop_service

app = typer.Typer(pretty_exceptions_show_locals=False)


@app.command()
def main(release: Path = typer.Option(...), apply: bool = typer.Option(False),
         rollback: bool = typer.Option(False)):
    release = release.resolve()
    site = Path(sysconfig.get_path('purelib')).resolve()
    if not release.is_relative_to(BASE) or not site.is_relative_to(BASE):
        raise ValueError('使用板端 /opt/ec26e Python 和发布目录')
    backup = release/'production-before'
    if not apply:
        print(json.dumps(dict(backup=str(backup), rollback=rollback,
            changes=['app wheel', 'random segmentation parameters', 'A4 image reference corners'],
            motion='preserved; no commands', hardware='unchanged')))
        return
    with TaskExecutionLock():
        state = json.loads(Path('/run/ec26e/menu-status.json').read_text())
        if state.get('busy') or state.get('preview'):
            raise RuntimeError('菜单正在运行任务或预览')
        if rollback:
            if not (backup/'complete').is_file():
                raise RuntimeError('缺少完整回滚备份')
            old_pid = stop_service()
            restore(backup, site)
            start_service(old_pid)
            return
        if backup.exists():
            raise RuntimeError('备份不可覆盖，请使用新发布目录')
        # 必须在停止菜单之前检查与菜单启动脚本相同的硬件路径。
        from ec_26_e.vision.linux_pl import verify_loaded_hardware
        verify_loaded_hardware(os.environ.get('EC_PL_BITSTREAM'))
        manifest = json.loads((release/'manifest.json').read_text())
        wheel = release/'ec_26_e-0.1.0-py3-none-any.whl'
        if hashlib.sha256(wheel.read_bytes()).hexdigest() != manifest['wheel_sha256']:
            raise RuntimeError('wheel 哈希不符')
        shutil.copytree(CONFIG, backup/'config')
        shutil.copy2(BASE/'install/start_menu.sh', backup/'start_menu.sh')
        for name in PACKAGES:
            shutil.copytree(site/name, backup/'packages'/name)
        (backup/'complete').write_text('complete\n')
        old_pid = stop_service()
        try:
            subprocess.run([sys.executable, '-m', 'pip', 'install', '--no-deps', '--force-reinstall', str(wheel)], check=True)
            changes = {'TASK2_BLUE_RATIO_Q8': '320', 'TASK2_BLUE_BACKGROUND_FRACTION_Q8': '218'}
            lines = (CONFIG/'machine.env').read_text().splitlines()
            for index, line in enumerate(lines):
                key = line.partition('=')[0]
                if key in changes:
                    lines[index] = key+'='+changes.pop(key)
            lines.extend(key+'='+value for key,value in changes.items())
            (CONFIG/'machine.env').write_text('\n'.join(lines)+'\n')
            for mode in ('fixed', 'random'):
                path = CONFIG/'tasks'/f'{mode}.json'
                config = json.loads(path.read_text())
                config['resize']['reference_corners_relative'] = manifest['reference_corners_relative']
                path.write_text(json.dumps(config, ensure_ascii=False, indent=2)+'\n')
            subprocess.run([sys.executable, '-c',
                'from ec_26_e.gui.warm_task import prepare; prepare(); '
                'from ec_26_e.gui.controller import load_profile; '
                'from ec_26_e.gui.menu_model import MODES; from pathlib import Path; '
                "[load_profile(Path('/opt/ec26e/config/axu5ev/machine.env'), "
                "Path('/opt/ec26e/config/axu5ev/tasks'), label, 'pl') for label in MODES]"], check=True)
            start_service(old_pid)
        except BaseException:
            stop_service()
            restore(backup, site)
            start_service(old_pid)
            raise
        (release/'deployed.json').write_text(json.dumps(manifest, indent=2))
        print('Installed; menu restarted. No motion, reboot or PL reload.')


if __name__ == '__main__':
    app()
