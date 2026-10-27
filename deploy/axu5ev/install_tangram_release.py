"""安装七巧板应用与独立求解器；保留原机械标定、权限、PL 和启动镜像。"""
from pathlib import Path
import hashlib
import json
import shutil
import subprocess
import sys
import sysconfig

import typer
from ec_26_e.tasks.execution_lock import TaskExecutionLock
from install_speed_release import BASE, CONFIG, PACKAGES, stop_service, start_service

app=typer.Typer(pretty_exceptions_show_locals=False)
FONT_FILES={
    'wqy-microhei-ui.ttf':Path('/usr/share/fonts/ttf/ec26e/wqy-microhei-ui.ttf'),
    'font-copyright':Path('/usr/share/doc/ec26e-ui-font/copyright'),
}


def restore_tangram(backup,site):
    for name in PACKAGES:
        destination=site/name
        if destination.parent != site or not site.is_relative_to(BASE):
            raise ValueError('回滚包路径越界')
        if destination.exists():shutil.rmtree(destination)
        shutil.copytree(backup/'packages'/name,destination)
    destination=CONFIG/'tasks/tangram.json'
    if (backup/'config/tasks/tangram.json').exists():
        shutil.copy2(backup/'config/tasks/tangram.json',destination)
    else:
        destination.unlink(missing_ok=True)
    for name,destination in FONT_FILES.items():
        source=backup/'fonts'/name
        if source.exists():shutil.copy2(source,destination)


@app.command()
def main(release: Path=typer.Option(...,'--release'), apply: bool=typer.Option(False,'--apply'),
         rollback: bool=typer.Option(False,'--rollback'),
         preserve_profile: bool=typer.Option(False,'--preserve-profile',help='软件修正时保留现有七巧板配置和试机权限')):
    release=release.resolve();site=Path(sysconfig.get_path('purelib')).resolve()
    if not release.is_relative_to(BASE) or not site.is_relative_to(BASE):
        raise ValueError('必须使用板端 /opt/ec26e 发布目录和 Python')
    backup=release/'production-before'
    if not apply:
        typer.echo(json.dumps(dict(backup=str(backup),rollback=rollback,motion=False,hardware_changed=False)))
        return
    with TaskExecutionLock():
        state=json.loads(Path('/run/ec26e/menu-status.json').read_text())
        if state.get('busy') or state.get('preview') or state.get('transition'):
            raise RuntimeError('菜单仍在执行任务或使用相机')
        if rollback:
            if not (backup/'complete').is_file():raise RuntimeError('缺少完整回滚备份')
            old_pid=stop_service()
            restore_tangram(backup,site)
            start_service(old_pid)
            return
        if backup.exists():raise RuntimeError('已有备份不可覆盖，请使用新发布目录')
        manifest=json.loads((release/'manifest.json').read_text())
        for name,digest in manifest['artifacts'].items():
            if Path(name).name != name or hashlib.sha256((release/name).read_bytes()).hexdigest()!=digest:
                raise RuntimeError('发布文件哈希不符')
        has_font='wqy-microhei-ui.ttf' in manifest['artifacts']
        if has_font and 'font-copyright' not in manifest['artifacts']:
            raise RuntimeError('字体发布缺少授权文件')
        before={str(p.relative_to(CONFIG)):hashlib.sha256(p.read_bytes()).hexdigest()
                for p in CONFIG.rglob('*') if p.is_file()}
        shutil.copytree(CONFIG,backup/'config')
        for name in PACKAGES:shutil.copytree(site/name,backup/'packages'/name)
        if has_font:
            (backup/'fonts').mkdir()
            for name,source in FONT_FILES.items():shutil.copy2(source,backup/'fonts'/name)
        (backup/'complete').write_text('complete\n')
        old_pid=stop_service()
        try:
            wheels=[str(release/name) for name in manifest['artifacts'] if name.endswith('.whl')]
            if len(wheels)!=2:raise RuntimeError('需要应用和独立求解器两个 wheel')
            subprocess.run([sys.executable,'-m','pip','install','--no-deps','--force-reinstall',*wheels],check=True)
            if has_font:
                for name,destination in FONT_FILES.items():shutil.copy2(release/name,destination)
            if not preserve_profile:
                profile=json.loads((release/'tangram.json').read_text())
                profile['motion_verified']=False
                (CONFIG/'tasks/tangram.json').write_text(json.dumps(profile,ensure_ascii=False,indent=2)+'\n')
            subprocess.run([sys.executable,'-c',
                'from pathlib import Path; from ec_26_e.gui.controller import load_profile; '
                'from ec_26_e.gui.menu_model import MODES; from ec_26_e.gui.warm_task import prepare; '
                "[load_profile(Path('/opt/ec26e/config/axu5ev/machine.env'),Path('/opt/ec26e/config/axu5ev/tasks'),label,'cpu') for label in MODES]; prepare()"],check=True)
            for relative,digest in before.items():
                if (preserve_profile or relative!='tasks/tangram.json') and hashlib.sha256((CONFIG/relative).read_bytes()).hexdigest()!=digest:
                    raise RuntimeError('原配置发生变化')
            start_service(old_pid)
        except BaseException:
            stop_service()
            restore_tangram(backup,site)
            start_service(old_pid)
            raise
        motion_enabled=json.loads((CONFIG/'tasks/tangram.json').read_text()).get('motion_verified') is True
        (release/'deployed.json').write_text(json.dumps(dict(artifacts=manifest['artifacts'],
            preserved_config_sha256=before,tangram_motion_enabled=motion_enabled,motion_tested=False,
            hardware_changed=False),indent=2))
        typer.echo('软件修正已部署；所有现有配置和试机权限保留。' if preserve_profile else
                   '七巧板已部署；新模式只规划，原机械参数与权限保留。')


if __name__=='__main__':app()
