"""应用已验收的焦点与速度配置；持任务锁备份，可单独回滚。"""
import hashlib
from dataclasses import replace
import json
import os
from pathlib import Path

import typer

from ec_26_e.config import load_task1_config, load_task2_config
from ec_26_e.tasks.execution_lock import TaskExecutionLock
from install_speed_release import BASE, CONFIG, start_service, stop_service

app = typer.Typer(pretty_exceptions_show_locals=False)
SETTINGS = dict(CAMERA_FOCUS_ABSOLUTE='68', MOTION_XY_SPEED_LEVEL='80',
                MOTION_XY_SPEED_MM_PER_SECOND='100', FOC_RPM='20')
# User-requested 1.5x of the deployed 80 / 100 mm/s / 25 RPM profile.
# Rotation accepts integer RPM only; 37.5 rounds to 38. Raise only its needed cap.
AXES_SPEED = dict(MOTION_XY_SPEED_LEVEL='120', MOTION_XY_SPEED_MM_PER_SECOND='150',
                  FOC_RPM='38', FOC_MAX_RPM='38')
# Servo42C has seven speed bits; 200 is not representable. Use its valid maximum.
AXES_MAX_SPEED = dict(MOTION_XY_SPEED_LEVEL='127', MOTION_XY_SPEED_MM_PER_SECOND='158.75',
                      FOC_RPM='50', FOC_MAX_RPM='50', MOTION_Z_SPEED_LEVEL='80')
# 颜色参数独立验收；调焦/提速不能把扑克设置写入所有任务。
FIXED_VISION = dict(TASK1_BLUE_RATIO_Q8='275', TASK1_BLUE_BACKGROUND_FRACTION_Q8='0',
                    TASK1_BLUE_PROFILE='fixed_auto')


def patch_environment(text, settings=SETTINGS):
    remaining = dict(settings)
    lines = text.splitlines()
    for index, line in enumerate(lines):
        key = line.partition('=')[0]
        if key in settings:
            if key not in remaining:
                raise ValueError(f'配置重复：{key}')
            lines[index] = key+'='+remaining.pop(key)
    lines.extend(key+'='+value for key, value in remaining.items())
    return ('\n'.join(lines)+'\n').encode('utf-8')


def digest(data):
    return hashlib.sha256(data).hexdigest()


@app.command()
def main(release: Path = typer.Option(...), rollback: bool = typer.Option(False),
         fixed_vision: bool = typer.Option(False), rotation_speed: bool = typer.Option(False),
         axes_speed: bool = typer.Option(False), axes_max: bool = typer.Option(False)):
    if sum((fixed_vision, rotation_speed, axes_speed, axes_max)) > 1:
        raise ValueError('一次只修改一种配置')
    release = release.resolve()
    if not release.is_relative_to(BASE/'releases') or not (release/'deployed.json').exists():
        raise ValueError('先完成对应软件发布')
    path = CONFIG/'machine.env'
    backup = release/('machine-before-axes-max.env' if axes_max else
                      'machine-before-axes.env' if axes_speed else
                      'machine-before-rotation.env' if rotation_speed else
                      'machine-before-vision.env' if fixed_vision else 'machine-before-capture.env')
    record = release/('axes-max-settings.json' if axes_max else
                      'axes-settings.json' if axes_speed else
                      'rotation-settings.json' if rotation_speed else
                      'vision-settings.json' if fixed_vision else 'settings.json')
    settings = (AXES_MAX_SPEED if axes_max else AXES_SPEED if axes_speed else {'FOC_RPM': '25'} if rotation_speed
                else FIXED_VISION if fixed_vision else SETTINGS)
    with TaskExecutionLock():
        menu = json.loads(Path('/run/ec26e/menu-status.json').read_text())
        if menu.get('busy') or menu.get('preview') or menu.get('transition'):
            raise RuntimeError('菜单忙，未修改配置')
        original = path.read_bytes()
        if rollback:
            previous = json.loads(record.read_text())
            if digest(original) != previous['after_sha256']:
                raise RuntimeError('配置已被后续修改，请先合并回滚差异')
            updated = backup.read_bytes()
        else:
            if record.exists() or (backup.exists() and backup.read_bytes() != original):
                raise RuntimeError('不能覆盖已有配置备份')
            updated = patch_environment(original.decode('utf-8'), settings)
            if not backup.exists():
                backup.write_bytes(original)
        temporary = path.with_suffix('.capture.tmp')
        temporary.write_bytes(updated)
        try:
            for name, loader in (('fixed', load_task1_config), ('random', load_task2_config)):
                profile = loader(CONFIG/'tasks'/f'{name}.json', env_path=temporary,
                    environ={'EC_PL_BITSTREAM': os.environ.get('EC_PL_BITSTREAM', '')})
                if not rollback:
                    if axes_speed or axes_max:
                        previous = loader(CONFIG/'tasks'/f'{name}.json', env_path=path,
                            environ={'EC_PL_BITSTREAM': os.environ.get('EC_PL_BITSTREAM', '')})
                        before = previous.actuation
                        baseline = (120, 150, 38, 38) if axes_max else (80, 100, 25, 30)
                        if ((before.xy_speed_level, before.xy_speed_mm_per_second,
                                before.rotation.rpm, before.rotation.max_rpm) != baseline
                                or (axes_max and before.z_speed_level != 64)):
                            raise ValueError('当前速度已变化，不能套用本次速度配置')
                        expected = replace(previous, actuation=replace(before,
                            xy_speed_level=int(settings['MOTION_XY_SPEED_LEVEL']),
                            xy_speed_mm_per_second=float(settings['MOTION_XY_SPEED_MM_PER_SECOND']),
                            z_speed_level=int(settings.get('MOTION_Z_SPEED_LEVEL', before.z_speed_level)),
                            rotation=replace(before.rotation, rpm=int(settings['FOC_RPM']),
                                             max_rpm=int(settings['FOC_MAX_RPM']))))
                        if profile != expected:
                            raise ValueError('提速修改了其他配置，未应用')
                    elif rotation_speed:
                        previous = loader(CONFIG/'tasks'/f'{name}.json', env_path=path,
                            environ={'EC_PL_BITSTREAM': os.environ.get('EC_PL_BITSTREAM', '')})
                        expected = replace(previous, actuation=replace(previous.actuation,
                            rotation=replace(previous.actuation.rotation, rpm=25)))
                        assert profile == expected
                    elif fixed_vision:
                        previous = loader(CONFIG/'tasks'/f'{name}.json', env_path=path,
                            environ={'EC_PL_BITSTREAM': os.environ.get('EC_PL_BITSTREAM', '')})
                        assert profile.camera == previous.camera and profile.actuation == previous.actuation
                        if name == 'fixed':
                            assert (profile.detect1.blue_ratio_q8, profile.detect1.blue_background_fraction_q8) == (275, 0)
                            assert profile.detect1.blue_profile == 'fixed_auto'
                        else:
                            assert profile == previous
                    else:
                        assert profile.camera.focus_absolute == 68
                        assert profile.actuation.xy_speed_level == 80
                        assert profile.actuation.xy_speed_mm_per_second == 100
                        assert profile.actuation.rotation.rpm == 20
            old_pid = stop_service()
            try:
                temporary.replace(path)
                start_service(old_pid)
            except BaseException:
                path.write_bytes(original)
                start_service(old_pid)
                raise
        finally:
            temporary.unlink(missing_ok=True)
        report = dict(changes=settings, before_sha256=digest(original),
                      after_sha256=digest(updated), motion_executed=False, rollback=rollback)
        if not rollback:
            record.write_text(json.dumps(report, indent=2))
        print(json.dumps(report))


if __name__ == '__main__':
    app()
