"""Compare resident PL kernels against independent CPU geometry; never motion."""
import argparse
import json
import os
from pathlib import Path

import numpy as np

from ec_26_e.control.pl_rigid import PynqRigidFitBackend
from ec_26_e.control.pl_clearance import PynqClearanceScanBackend
from ec_26_e.tasks.execution_lock import TaskExecutionLock
from puzzle_solver.acceleration import CpuRigidFitBackend, RigidFitRequest


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    rng = np.random.default_rng(20261021)
    report = {'measurement': 'resident_board_pl_vs_cpu', 'motion_executed': False, 'batches': []}
    with TaskExecutionLock():
        rigid = PynqRigidFitBackend(os.environ['EC_PL_BITSTREAM'])
        try:
            for size in (0, 1, 63, 64, 65, 129):
                requests = []
                for index in range(size):
                    source = rng.uniform(-180, 180, (2+index % 7, 2))
                    angle = np.deg2rad(rng.uniform(-180, 180))
                    rotation = np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
                    target = source @ rotation.T + rng.uniform(-90, 90, 2)
                    target += rng.normal(0, .02, target.shape)
                    weights = None if index % 2 else rng.uniform(.1, 1, len(source))
                    requests.append(RigidFitRequest(source, target, weights))
                expected = CpuRigidFitBackend().fit_batch(requests)
                actual = rigid.fit_batch(requests)
                assert len(actual) == len(expected)
                errors = [max(abs((a.angle-b.angle+180) % 360-180),
                              float(np.max(np.abs(a.translation-b.translation))), abs(a.rms-b.rms))
                          for a, b in zip(expected, actual)]
                worst = max(errors, default=0.0)
                assert worst < .002, (size, worst)
                report['batches'].append({'size': size, 'max_parameter_error': worst})
            report['rigid_requests'] = rigid.request_count
            report['rigid_batches_including_probe'] = rigid.batch_count
            report['numerical_cpu_fallbacks'] = rigid.numerical_cpu_fallbacks
        finally:
            rigid.close()
        clearance = PynqClearanceScanBackend(os.environ['EC_PL_BITSTREAM'])
        try:
            square = np.array([[0., 0.], [20., 0.], [20., 20.], [0., 20.]])
            vertices = [square+offset for offset in ([0, 0], [20, 0], [0, 20], [20, 20])]
            distances = clearance.minimum_distances(vertices, np.array([v.mean(axis=0) for v in vertices]))
            error = float(np.max(np.abs(distances-np.linspace(0, 20, 401))))
            assert error < .02, error
            report['clearance_max_error_mm'] = error
            report['clearance_samples'] = len(distances)
        finally:
            clearance.close()
    report['passed'] = True
    args.output.write_text(json.dumps(report, indent=2))
    print(json.dumps(report))


if __name__ == '__main__':
    main()
