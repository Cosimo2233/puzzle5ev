"""Bounded AXU5EV hardware checks; does not open any actuator interface."""
from itertools import combinations
import hashlib
import json
import os
from pathlib import Path
import time

import cv2
import numpy as np
from shapely.geometry import Polygon
import typer

from ec_26_e.control.pl_clearance import PynqClearanceScanBackend
from ec_26_e.control.pl_rigid import PynqRigidFitBackend
from ec_26_e.vision.linux_pl import LinuxPreprocessor
from ec_26_e.vision.preprocess import CpuPreprocessor
from ec_26_e.tasks.execution_lock import TaskExecutionLock
from puzzle_solver.acceleration import CpuRigidFitBackend, RigidFitRequest

app = typer.Typer()


@app.command()
def main(bitstream: Path = typer.Option(...), output: Path = typer.Option(...),
         image: Path | None = typer.Option(None)):
    with TaskExecutionLock():
        validate(bitstream, output, image)


def validate(bitstream, output, image):
    if output.exists():
        raise ValueError('Choose a new report path')
    os.environ['EC_PL_ACCESS'] = 'linux'
    cv2.setNumThreads(1)
    report = {'motion_executed': False, 'passed': False, 'preprocess': [],
              'bit_sha256': hashlib.sha256(bitstream.read_bytes()).hexdigest(),
              'hwh_sha256': hashlib.sha256(bitstream.with_suffix('.hwh').read_bytes()).hexdigest()}
    try:
        rng = np.random.default_rng(20261020)
        frames = [rng.integers(0, 256, (h, w, 3), dtype=np.uint8)
                  for h, w in ((1, 1), (8, 8), (72, 128), (720, 1280))]
        if image is not None:
            frame = cv2.imread(str(image))
            if frame is None:
                raise ValueError('Cannot decode camera image')
            frames.append(frame)
        cpu = CpuPreprocessor()
        device = LinuxPreprocessor(str(bitstream))
        try:
            # Alternate dimensions and repeat transactions to expose stale frames.
            for index, frame in enumerate(frames + frames[::-1], 1):
                start = time.perf_counter()
                actual = device.process(frame, index)
                elapsed = time.perf_counter() - start
                expected = cpu.process(frame, index)
                mismatches = int(np.count_nonzero(actual != expected))
                report['preprocess'].append({
                    'shape': list(frame.shape), 'frame_id': index,
                    'mismatched_pixels': mismatches, 'transport_and_pl_ms': elapsed * 1000})
                if mismatches:
                    raise AssertionError('Preprocessing differs from CPU')
        finally:
            device.close()

        source = np.array([[12., 7.], [81., 9.], [35., 54.], [3., 47.]])
        requests = []
        for angle in np.linspace(-179.9, 179.9, 129):
            theta = np.deg2rad(angle)
            rotation = np.array([[np.cos(theta), -np.sin(theta)],
                                 [np.sin(theta), np.cos(theta)]])
            requests.append(RigidFitRequest(source, source @ rotation.T + [63., -42.],
                                            np.array([.1, .7, .9, .4])))
        device = PynqRigidFitBackend(str(bitstream))
        try:
            start = time.perf_counter()
            actual = device.fit_batch(requests)
            elapsed = time.perf_counter() - start
            expected = CpuRigidFitBackend().fit_batch(requests)
            angle_error = max(abs((a.angle - e.angle + 180) % 360 - 180)
                              for a, e in zip(actual, expected))
            translation_error = max(float(np.max(abs(a.translation - e.translation)))
                                    for a, e in zip(actual, expected))
            report['rigid'] = dict(requests=len(requests), batches=device.batch_count,
                                   numerical_cpu_fallbacks=device.numerical_cpu_fallbacks,
                                   max_angle_error_degrees=angle_error,
                                   max_translation_error_mm=translation_error,
                                   transport_and_pl_ms=elapsed * 1000)
            if angle_error > .002 or translation_error > .002 or device.numerical_cpu_fallbacks:
                raise AssertionError('Rigid-fit hardware acceptance failed')
        finally:
            device.close()

        square = np.array([[0., 0.], [20., 0.], [20., 20.], [0., 20.]])
        vertices = [square + offset for offset in ([0, 0], [20, 0], [0, 20], [20, 20])]
        centers = np.array([v.mean(axis=0) for v in vertices])
        device = PynqClearanceScanBackend(str(bitstream))
        try:
            start = time.perf_counter()
            actual = device.minimum_distances(vertices, centers)
            elapsed = time.perf_counter() - start
            expected = []
            for alpha in np.linspace(0, 1, 401):
                polygons = [Polygon(v + alpha * c) for v, c in zip(vertices, centers)]
                expected.append(min(a.distance(b) for a, b in combinations(polygons, 2)))
            error = float(np.max(abs(actual - expected)))
            report['clearance'] = dict(samples=401, max_distance_error_mm=error,
                                       transport_and_pl_ms=elapsed * 1000)
            if error > .001:
                raise AssertionError('Clearance scan differs from independent GEOS reference')
        finally:
            device.close()
        report['passed'] = True
    except BaseException as error:
        report['error'] = f'{type(error).__name__}: {error}'
        raise
    finally:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2), encoding='utf-8')
        typer.echo(json.dumps(report))


if __name__ == '__main__':
    app()
