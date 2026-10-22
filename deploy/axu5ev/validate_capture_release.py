"""持任务锁回放同一组原始帧；不创建执行器或发送运动命令。"""
import cProfile
import io
import json
from pathlib import Path
import pstats
import time

import cv2
import numpy as np
import typer

from ec_26_e.gui.board_profile import load_menu_config, profile_paths, preview_backend
from ec_26_e.gui.controller import load_profile
from ec_26_e.gui.menu_model import MODES
from ec_26_e.tasks.execution_lock import TaskExecutionLock
from ec_26_e.vision import Resize
from ec_26_e.vision.factory import create_production_detector

app = typer.Typer(pretty_exceptions_show_locals=False)


@app.command()
def main(input_dir: Path = typer.Option(...), output: Path = typer.Option(...),
         profile: bool = typer.Option(False), threads: int = typer.Option(1, min=1, max=4)):
    cv2.setNumThreads(threads)
    cv2.cvtColor(np.zeros((8, 8, 3), np.uint8), cv2.COLOR_BGR2LAB)
    images = [cv2.imread(str(p)) for p in sorted(input_dir.glob('frame-*.png'))]
    if not images or any(image is None for image in images):
        raise ValueError('缺少有效回放帧')
    board = load_menu_config()
    config = load_profile(*profile_paths(board), list(MODES)[1], preview_backend(board))
    rows = []
    profiler = cProfile.Profile()
    with TaskExecutionLock():
        menu = json.loads(Path('/run/ec26e/menu-status.json').read_text())
        if menu.get('busy') or menu.get('preview') or menu.get('transition'):
            raise RuntimeError('菜单忙，未执行回放')
        with create_production_detector(config.detect1) as owner:
            resizer = Resize(config.resize)
            if profile:
                profiler.enable()
            for raw in images:
                start = time.perf_counter()
                rect = resizer.resize(raw)
                middle = time.perf_counter()
                result = owner.detector.detect(rect.image_bgr)
                end = time.perf_counter()
                rows.append(dict(resize_ms=(middle-start)*1000, detect_ms=(end-middle)*1000,
                    pieces=len(result.pieces), failure=owner.detector.last_failure,
                    corners=rect.source_corners_px, polygons=[p.corners_px for p in result.pieces]))
            if profile:
                profiler.disable()
    summary = dict(rows=rows, motion_tested=False,
        resize_median_ms=float(np.median([r['resize_ms'] for r in rows[1:]])),
        detect_median_ms=float(np.median([r['detect_ms'] for r in rows[1:]])))
    output.write_text(json.dumps(summary, indent=2))
    if profile:
        stream = io.StringIO()
        pstats.Stats(profiler, stream=stream).sort_stats('cumulative').print_stats(30)
        output.with_suffix('.profile.txt').write_text(stream.getvalue())
    print(json.dumps({k: v for k, v in summary.items() if k != 'rows'}))


if __name__ == '__main__':
    app()
