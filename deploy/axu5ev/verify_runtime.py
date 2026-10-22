"""验证 AXU5EV CPU 运行环境；不访问 GPIO、DMA 或加载 PL。"""
from __future__ import annotations

import importlib
import importlib.metadata
import json
import os
import platform
import ssl
import subprocess
import sys
import time
from pathlib import Path


def main() -> None:
    result = {"platform": platform.platform(), "python": sys.version,
              "openssl": ssl.OPENSSL_VERSION, "motion_executed": False,
              "pl_loaded": False, "modules": {}}
    for module, distribution in [
        ("numpy", "numpy"), ("scipy", "scipy"), ("shapely", "shapely"),
        ("cv2", "opencv-python"), ("serial", "pyserial"), ("gpiod", "gpiod"),
        ("periphery", "python-periphery"), ("typer", "typer"), ("PIL", "pillow"),
        ("puzzle_solver", "puzzle-solver"), ("ec_26_e", "ec-26-e"),
    ]:
        importlib.import_module(module)
        result["modules"][module] = importlib.metadata.version(distribution)
    import numpy as np
    from scipy.linalg import solve
    from shapely.geometry import Polygon
    import cv2
    import tkinter as tk
    import imageio_ffmpeg
    from puzzle_solver import PuzzlePiece, PlanningOptions, plan_sheet_moves

    matrix = np.array([[3., 1.], [1., 2.]])
    rhs = np.array([9., 8.])
    np.testing.assert_allclose(matrix @ solve(matrix, rhs), rhs)
    assert Polygon([(0, 0), (2, 0), (2, 3), (0, 3)]).area == 6.0
    image = np.zeros((32, 32, 3), dtype=np.uint8)
    image[8:24, 8:24] = [0, 255, 0]
    ok, encoded = cv2.imencode('.png', image)
    assert ok
    np.testing.assert_array_equal(cv2.imdecode(encoded, cv2.IMREAD_COLOR), image)
    result['numerical_and_png_checks'] = 'passed'
    root = tk.Tk()
    root.withdraw()
    result['tk_patchlevel'] = root.tk.call('info', 'patchlevel')
    root.update()
    root.destroy()
    result['display'] = os.environ.get('DISPLAY')
    result['ffmpeg'] = imageio_ffmpeg.get_ffmpeg_exe()
    subprocess.run([result['ffmpeg'], '-nostdin', '-v', 'error', '-f', 'lavfi',
                    '-i', 'color=c=red:s=32x32:r=1', '-frames:v', '1',
                    '-f', 'null', '-'], check=True)
    payload = json.loads(Path(sys.argv[1]).read_text())
    pieces = tuple(PuzzlePiece(p['id'], tuple(tuple(v) for v in p['vertices_mm']))
                   for p in payload['pieces'])
    started = time.perf_counter()
    plan = plan_sheet_moves(pieces, PlanningOptions(mode=payload['mode'],
                            noise_bound_mm=payload.get('noise_bound_mm', 0.0)))
    result['cpu_solve_seconds'] = time.perf_counter() - started
    result['plan'] = plan.to_dict()
    assert len(result['plan']['moves']) == 4
    Path(sys.argv[2]).write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k != 'plan'}, indent=2))
    print('AXU5EV_CPU_RUNTIME_PASS')


if __name__ == '__main__':
    main()
