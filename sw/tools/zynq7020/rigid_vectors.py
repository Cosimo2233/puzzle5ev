"""生成 HLS 数值对照向量；黄金值来自独立算法库 float64 SVD。"""
from pathlib import Path
import numpy as np
from puzzle_solver.acceleration import CpuRigidFitBackend, RigidFitRequest
from puzzle_solver._engine.transforms import transform_points


def generate(path: Path) -> None:
    rng = np.random.default_rng(20261011)
    lines = ['64']
    for index in range(64):
        n = 2 + index % 7
        source = rng.uniform(-100, 100, (n, 2))
        angle = [-180., -179.99, -.001, 0., 93.2, 179.99][index % 6]
        target = transform_points(source, angle, (65., -29.))
        if index % 4 == 1: target += rng.normal(0, .4, target.shape)
        if index % 4 == 2: target[0] += (7, -11)  # Outlier, not a robust estimate.
        if index % 4 == 3: target[:, 0] *= -1  # Proper rotation on reflected input.
        if index % 8 == 0:
            source += (3700., -3700.)
            target += (-3700., 3700.)
        weights = rng.uniform(.05, 1, n) if index % 2 else np.ones(n)
        # Golden values account for input quantization; compute with float64 SVD.
        source, target, weights = (v.astype(np.float32).astype(float) for v in (source, target, weights))
        fit = CpuRigidFitBackend().fit_batch([RigidFitRequest(source, target, weights)])[0]
        fitted = transform_points(source, fit.angle, fit.translation)
        maximum = np.linalg.norm(fitted-target, axis=1).max()
        lines.append(str(n))
        lines.extend(' '.join(f'{v:.12g}' for v in row) for row in np.column_stack((source, target, weights)))
        lines.append(' '.join(f'{v:.12g}' for v in [fit.angle, *fit.translation, fit.rms, maximum]))
    path.write_text('\n'.join(lines)+'\n', encoding='ascii')


if __name__ == '__main__':
    generate(Path(__file__).resolve().parents[3] / 'src/hls/rigid/rigid_vectors.txt')
