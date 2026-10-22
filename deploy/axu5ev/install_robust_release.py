"""安装菜单/求解优化包，完整备份依赖；不改标定、权限、PL 或启动镜像。"""
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
from install_speed_release import BASE, CONFIG, PACKAGES, start_service, stop_service

app = typer.Typer(pretty_exceptions_show_locals=False)
INSTALL_FILES = ('start_menu.sh', 'xorg-hdmi.conf')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def config_hashes():
    return {str(p.relative_to(CONFIG)): digest(p) for p in CONFIG.rglob('*') if p.is_file()}


def package_paths(site):
    return [site/name for name in PACKAGES] + [site/'shapely', site/'shapely.libs'] + list(site.glob('shapely-*.dist-info'))


def restore(backup, site):
    for path in package_paths(site):
        if path.parent != site or not site.is_relative_to(BASE):
            raise ValueError('软件包路径越界')
        if path.exists():
            shutil.rmtree(path)
    for path in (backup/'packages').iterdir():
        shutil.copytree(path, site/path.name)
    for name in INSTALL_FILES:
        shutil.copy2(backup/name, BASE/'install'/name)


@app.command()
def main(release: Path = typer.Option(...), apply: bool = typer.Option(False),
         rollback: bool = typer.Option(False)):
    release = release.resolve()
    site = Path(sysconfig.get_path('purelib')).resolve()
    if not release.is_relative_to(BASE) or not site.is_relative_to(BASE):
        raise ValueError('使用板端 /opt/ec26e Python 和发布目录')
    if os.environ.get('PYTHONPATH'):
        raise RuntimeError('部署前移除候选 PYTHONPATH')
    backup = release/'production-before'
    if not apply:
        print(json.dumps(dict(backup=str(backup), rollback=rollback,
            changes=['app', 'solver', 'Shapely', *INSTALL_FILES],
            configuration='unchanged', motion='no commands', hardware='unchanged')))
        return
    with TaskExecutionLock():
        state = json.loads(Path('/run/ec26e/menu-status.json').read_text())
        if state.get('busy') or state.get('preview') or state.get('transition'):
            raise RuntimeError('菜单正在运行任务或预览')
        before_config = config_hashes()
        boot_id = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        if rollback:
            if not (backup/'complete').is_file():
                raise RuntimeError('缺少完整回滚备份')
            old_pid = stop_service()
            restore(backup, site)
            start_service(old_pid)
            print('Software restored; current mechanics and permissions preserved.')
            return
        if backup.exists():
            raise RuntimeError('备份不可覆盖，请使用新发布目录')
        from ec_26_e.vision.linux_pl import verify_loaded_hardware
        verify_loaded_hardware(os.environ.get('EC_PL_BITSTREAM'))
        manifest = json.loads((release/'manifest.json').read_text())
        for name, expected in manifest['artifacts'].items():
            if Path(name).name != name or digest(release/name) != expected:
                raise RuntimeError(f'发布文件哈希不符: {name}')
        for name, expected in manifest['hardware'].items():
            if digest(Path(os.environ['EC_PL_BITSTREAM']).with_suffix('.'+name)) != expected:
                raise RuntimeError(f'硬件版本不符: {name}')
        shutil.copytree(CONFIG, backup/'config')  # 审计快照，不覆盖后续用户配置。
        for name in INSTALL_FILES:
            shutil.copy2(BASE/'install'/name, backup/name)
        for path in package_paths(site):
            shutil.copytree(path, backup/'packages'/path.name)
        (backup/'complete').write_text('complete\n')
        old_pid = stop_service()
        try:
            wheels = sorted(release/name for name in manifest['artifacts'] if name.endswith('.whl'))
            subprocess.run([sys.executable, '-m', 'pip', 'install', '--no-deps', '--force-reinstall',
                            *map(str, wheels)], check=True)
            for name in INSTALL_FILES:
                shutil.copy2(release/name, BASE/'install'/name)
            (BASE/'install/start_menu.sh').chmod(0o755)
            subprocess.run([sys.executable, '-c',
                'from ec_26_e.gui.warm_task import prepare; prepare(); '
                'from ec_26_e.gui.plan_preview import PlanPreviewPublisher; '
                'from ec_26_e.gui.board_profile import load_menu_config; load_menu_config(); '
                'import shapely; assert shapely.__version__ == "2.1.2"'], check=True)
            if config_hashes() != before_config:
                raise RuntimeError('配置在部署期间发生变化')
            start_service(old_pid)
        except BaseException:
            stop_service()
            restore(backup, site)
            start_service(old_pid)
            raise
        (release/'deployed.json').write_text(json.dumps(dict(
            artifacts=manifest['artifacts'], hardware=manifest['hardware'],
            boot_id=boot_id, config_sha256=before_config,
            motion_tested=False, hardware_changed=False), indent=2))
        print('Installed; menu restarted. Configuration, mechanics and permissions unchanged. No motion.')


if __name__ == '__main__':
    app()
