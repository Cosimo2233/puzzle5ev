"""Isolated process per trial: startup + acquisition + vision + solving, never motion."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def worker(args):
    import logging
    import cv2
    from ec_26_e.cam import CameraFrame, FFmpegCamera
    from ec_26_e.gui.controller import load_profile
    from ec_26_e.gui.menu_model import MODES
    from ec_26_e.tasks.execution_lock import TaskExecutionLock
    from ec_26_e.tasks.task1.runner import run_puzzle_task
    from ec_26_e.vision import Resize
    from ec_26_e.vision.factory import create_production_detector
    cv2.setNumThreads(1)
    logging.basicConfig(level=logging.INFO)
    label = next(key for key, value in MODES.items() if value[0] == args.mode)
    profile = load_profile(args.env, args.config_dir, label, args.backend)

    class RecordedCamera:
        def start(self):
            self.paths = [args.image] if args.image else sorted(args.frames.glob('frame-*.png'))
            if not self.paths:
                raise RuntimeError('No recorded frames')
            self.images = {}
            self.sequence = 0

        def read_latest(self, after_sequence=None, timeout=None):
            self.sequence += 1
            index = (self.sequence-1) % len(self.paths)
            if index not in self.images:
                self.images[index] = cv2.imread(str(self.paths[index]))
                if self.images[index] is None:
                    raise RuntimeError(f'Unreadable recorded frame: {self.paths[index]}')
            return CameraFrame(sequence_id=self.sequence, received_at_ns=time.monotonic_ns(),
                               image_bgr=self.images[index])

        def close(self):
            pass

    with TaskExecutionLock():
        camera = FFmpegCamera(profile.camera) if args.live else RecordedCamera()
        with create_production_detector(profile.detect1) as detector:
            plan = run_puzzle_task(camera, Resize(profile.resize), detector, options=profile.options)
        args.output.write_text(json.dumps(plan.to_dict()))


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--env', type=Path, required=True)
    parser.add_argument('--config-dir', type=Path, required=True)
    parser.add_argument('--frames', type=Path)
    parser.add_argument('--image', type=Path)
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--mode', choices=('fixed','random'), default='fixed')
    parser.add_argument('--backend', choices=('cpu','pl'), default='cpu')
    parser.add_argument('--variant', required=True)
    parser.add_argument('--trials', type=int, default=30)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--worker', action='store_true')
    parser.add_argument('--warm-start', action='store_true', help='Preload modules before the simulated menu click')
    parser.add_argument('--ready-file', type=Path)
    args = parser.parse_args()
    os.environ.update(EC_MOTION_ENABLED='false', EC_DRAW_ENABLED='false',
                      EC_RIGID_BACKEND=args.backend, EC_CLEARANCE_BACKEND=args.backend)
    if args.worker:
        if args.ready_file:
            from ec_26_e.gui.warm_task import prepare
            prepare()
            args.ready_file.write_text('ready\n')
            stamp = sys.stdin.readline().strip()
            if not stamp:
                return
            os.environ['EC_TASK_LAUNCHED_MONOTONIC'] = str(float(stamp))
        worker(args)
        return
    if not args.live and args.frames is None and args.manifest is None:
        parser.error('--frames or --live required')
    records = []
    scenes = json.loads(args.manifest.read_text()) if args.manifest else None
    if scenes: args.trials = len(scenes)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('w') as output:
        for trial in range(args.trials):
            destination = args.output.with_name(args.output.stem+f'-plan-{trial:02d}.json')
            command = [sys.executable, __file__, '--worker', '--env', str(args.env), '--config-dir', str(args.config_dir),
                '--backend', args.backend, '--variant', args.variant, '--mode', args.mode, '--output', str(destination)]
            if scenes:
                command += ['--image',str(args.manifest.parent/scenes[trial]['file'])]
            else:
                command += ['--live'] if args.live else ['--frames', str(args.frames)]
            started = time.monotonic()
            environment = dict(os.environ, EC_TASK_LAUNCHED_MONOTONIC=str(started))
            with destination.with_suffix('.log').open('w') as log:
                try:
                    if args.warm_start:
                        ready = destination.with_suffix('.ready')
                        ready.unlink(missing_ok=True)
                        process = subprocess.Popen(command+['--ready-file',str(ready)], env=environment,
                            stdin=subprocess.PIPE, text=True, stdout=log, stderr=subprocess.STDOUT)
                        try:
                            deadline = time.monotonic()+30
                            while not ready.exists():
                                if process.poll() is not None or time.monotonic() >= deadline:
                                    raise RuntimeError('Warm preload failed; inspect worker log')
                                time.sleep(.01)
                            started = time.monotonic()
                            process.stdin.write(str(started)+'\n'); process.stdin.flush(); process.stdin.close()
                            status = process.wait(timeout=110)
                        finally:
                            if process.poll() is None:
                                process.kill(); process.wait()
                            ready.unlink(missing_ok=True)
                    else:
                        result = subprocess.run(command, env=environment, stdout=log, stderr=subprocess.STDOUT, timeout=110)
                        status = result.returncode
                except subprocess.TimeoutExpired:
                    status = 124
            record = dict(trial=trial,variant=args.variant,backend=args.backend,mode=args.mode,
                measurement='live_process_to_plan' if args.live else 'recorded_process_to_plan',
                success=status==0,returncode=status,seconds=time.monotonic()-started)
            record['preloaded_modules'] = args.warm_start
            if args.warm_start:
                record['measurement'] = 'live_dispatch_to_plan' if args.live else 'recorded_dispatch_to_plan'
            if scenes:
                record.update(case=scenes[trial]['case'],count=scenes[trial]['count'],
                    measurement='synthetic_dispatch_to_plan' if args.warm_start else 'synthetic_process_to_plan')
            records.append(record)
            output.write(json.dumps(record)+'\n'); output.flush()
            print(json.dumps(record),flush=True)
            if args.live and status:
                break  # Do not spend 30 acquisition timeouts on a missing physical scene.
    import numpy as np
    summary = dict(trials=len(records),successes=sum(r['success'] for r in records),
        median_seconds=float(np.median([r['seconds'] for r in records])),
        p95_seconds=float(np.percentile([r['seconds'] for r in records],95)))
    args.output.with_suffix('.summary.json').write_text(json.dumps(summary,indent=2))
    print(json.dumps(summary),flush=True)
    if summary['successes'] != summary['trials']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
