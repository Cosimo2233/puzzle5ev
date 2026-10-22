"""真实相机稳定识别与求解诊断；不构造运动执行器。"""
from __future__ import annotations

import json
from pathlib import Path
import time

from ec_26_e.cam import FFmpegCamera
from ec_26_e.gui.controller import load_profile
from ec_26_e.gui.menu_model import MODES
from ec_26_e.tasks.execution_lock import TaskExecutionLock
from ec_26_e.tasks.task1.runner import run_task1
from ec_26_e.tasks.task2.runner import run_task2
from ec_26_e.vision import Resize
from ec_26_e.vision.factory import create_production_detector


def run_live_plan(env_path: Path, config_dir: Path, mode: str,
                  backend: str, output: Path) -> dict:
    if mode not in ('fixed', 'random') or backend not in ('cpu', 'pl'):
        raise ValueError('mode=fixed/random，backend=cpu/pl')
    label = next(label for label, spec in MODES.items() if spec[0] == mode)
    started = time.perf_counter()
    report = dict(mode=mode, backend=backend, success=False, state='running',
                  motion_executed=False, temporal_stability_validated=False)

    def save():
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix(output.suffix + '.tmp')
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(output)

    # Lock before replacing a result: a competing launch must not erase the
    # status of the active task. No actuator factory is part of this path.
    with TaskExecutionLock():
        save()
        try:
            config = load_profile(env_path, config_dir, label, backend)
            with create_production_detector(config.detect1) as detector:
                run = run_task1 if mode == 'fixed' else run_task2
                plan = run(FFmpegCamera(config.camera), Resize(config.resize), detector,
                           options=config.options)
            report.update(success=True, state='complete', temporal_stability_validated=True,
                          plan=plan.to_dict())
        except BaseException as error:
            report.update(state='cancelled' if isinstance(error, KeyboardInterrupt) else 'failed',
                          error=str(error) or type(error).__name__)
            raise
        finally:
            report['elapsed_seconds'] = time.perf_counter() - started
            save()
    return report
