"""测预览首帧、首个有效轮廓和刷新率；仅打开相机，不启动任务。"""
import json
from pathlib import Path
import time

import numpy as np
import typer

from ec_26_e.gui.camera_pipe import CameraPipe
from ec_26_e.gui.menu_model import MODES

app = typer.Typer(pretty_exceptions_show_locals=False)


@app.command()
def main(output: Path = typer.Option(...), seconds: float = typer.Option(10, min=5, max=60)):
    menu = json.loads(Path('/run/ec26e/menu-status.json').read_text())
    if menu.get('busy') or menu.get('preview') or menu.get('transition'):
        raise RuntimeError('菜单忙，跳过测试')
    camera = CameraPipe()
    report = dict(motion_tested=False, first_frame_seconds=None, first_contour_seconds=None)
    samples = []
    try:
        primed = time.monotonic()
        if hasattr(camera, 'prime'):
            camera.prime()
            while not camera.ready and time.monotonic()-primed < 10:
                time.sleep(.02)
            if not camera.ready:
                raise RuntimeError('预热失败：'+str(camera.error))
        report['software_warmup_seconds'] = time.monotonic()-primed
        started = time.monotonic()
        wall = time.time()
        camera.start(list(MODES)[1])
        last_status = None
        while time.monotonic()-started < seconds:
            elapsed = time.monotonic()-started
            if camera.error:
                raise RuntimeError(camera.error)
            if camera.version and report['first_frame_seconds'] is None:
                report['first_frame_seconds'] = elapsed
            try:
                current = json.loads(Path('/run/ec26e/preview-status.json').read_text())
                if current['updated_at_unix'] >= wall and current['updated_at_unix'] != last_status:
                    last_status = current['updated_at_unix']
                    samples.append(current)
                    if current.get('valid') and report['first_contour_seconds'] is None:
                        report['first_contour_seconds'] = elapsed
            except (OSError, ValueError):
                pass
            time.sleep(.02)
        if camera.latest:
            output.with_suffix('.ppm').write_bytes(camera.latest)
        report.update(samples=samples, frames=camera.version)
        ages = [s['analysis_age_seconds'] for s in samples if s.get('overlay_current')]
        report['contour_age_median_seconds'] = float(np.median(ages)) if ages else None
        report['last_status'] = samples[-1] if samples else None
    finally:
        camera.close()
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k != 'samples'}))


if __name__ == '__main__':
    app()
