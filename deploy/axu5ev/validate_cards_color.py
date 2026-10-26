"""Replay and fresh-camera checks; no actuator imports or motion commands."""
from dataclasses import replace
import json
from pathlib import Path
import time

import cv2
import numpy as np
import typer

from ec_26_e.cam import FFmpegCamera
from ec_26_e.gui.board_profile import load_menu_config, profile_paths
from ec_26_e.gui.controller import load_profile
from ec_26_e.gui.menu_model import MODES
from ec_26_e.tasks.execution_lock import TaskExecutionLock
from ec_26_e.tasks.task1.runner import run_puzzle_task
from ec_26_e.vision import Resize
from ec_26_e.vision.factory import create_production_detector
from ec_26_e.vision.preprocess import CpuPreprocessor, PreprocessConfig
from ec_26_e.vision.linux_pl import LinuxPreprocessor
from ec_26_e.vision.runtime import prepare_vision_runtime

app = typer.Typer(pretty_exceptions_show_locals=False)


@app.command()
def main(directory: Path = typer.Argument(...), live_frames: int = typer.Option(16)):
    prepare_vision_runtime()
    state = json.loads(Path('/run/ec26e/menu-status.json').read_text())
    if any(state[k] for k in ('busy', 'preview', 'transition')):
        raise RuntimeError('Menu is busy; no validation started')
    board = load_menu_config()
    configs = {label: load_profile(*profile_paths(board), label, 'pl') for label in MODES}
    report = dict(replay={}, live={}, solve={}, motion_executed=False)
    with TaskExecutionLock():
        first = next(iter(configs.values()))
        params = PreprocessConfig(blue_profile='background_auto')
        cpu = CpuPreprocessor(params)
        pl = LinuxPreprocessor(first.detect1.pl_bitstream, params)
        try:
            for i, path in enumerate(sorted(directory.glob('rect-*.png')), 1):
                image = cv2.imread(str(path))
                np.testing.assert_array_equal(cpu.process(image, i), pl.process(image, i))
            report['cpu_pl_identical_frames'] = i
        finally:
            pl.close()
        for label, config in configs.items():
            mode = MODES[label][0]
            detection = replace(config.detect1, blue_profile='background_auto')
            observations = []
            with create_production_detector(detection) as owner:
                for path in sorted(directory.glob('rect-*.png')):
                    r = owner.detector.detect(cv2.imread(str(path)))
                    observations.append(dict(file=path.name, pieces=len(r.pieces),
                        reason=owner.detector.last_failure,
                        corners=[p.corners_px for p in r.pieces]))
            report['replay'][mode] = observations
            records = []
            resizer = Resize(config.resize)
            with FFmpegCamera(config.camera) as camera, create_production_detector(detection) as owner:
                sequence = None
                for i in range(live_frames):
                    frame = camera.read_latest(after_sequence=sequence)
                    sequence = frame.sequence_id
                    try:
                        rect = resizer.resize(frame.image_bgr)
                        r = owner.detector.detect(rect.image_bgr)
                        records.append(dict(sequence=sequence, pieces=len(r.pieces),
                            reason=owner.detector.last_failure,
                            corners=[p.corners_px for p in r.pieces]))
                    except ValueError as error:
                        records.append(dict(sequence=sequence, pieces=0, reason=str(error)))
            report['live'][mode] = records
            # The real stable reviewer and planner, with no motion application.
            started = time.monotonic()
            try:
                with create_production_detector(detection) as owner:
                    plan = run_puzzle_task(FFmpegCamera(config.camera), Resize(config.resize), owner,
                        options=replace(config.options, acquisition_timeout_seconds=15))
                report['solve'][mode] = dict(success=True, pieces=len(plan.moves),
                    confidence=plan.confidence, elapsed_seconds=time.monotonic()-started)
            except Exception as error:
                report['solve'][mode] = dict(success=False, error=str(error),
                    elapsed_seconds=time.monotonic()-started)
            (directory/'candidate-validation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(dict(replay_pass={m:sum(x['pieces']==4 for x in r) for m,r in report['replay'].items()},
        live_pass={m:sum(x['pieces']==4 for x in r) for m,r in report['live'].items()},
        solve=report['solve'],cpu_pl_identical_frames=report['cpu_pl_identical_frames']),ensure_ascii=False))


if __name__ == '__main__':
    app()
