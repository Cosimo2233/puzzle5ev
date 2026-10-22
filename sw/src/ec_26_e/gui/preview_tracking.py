"""仅平滑显示顶点；正式识别、稳定审查和 MovePlan 不使用这些坐标。"""
from itertools import permutations

import numpy as np


class PreviewContours:
    def __init__(self):
        self.previous = None

    def update(self, report):
        if not report.get('valid'):
            self.previous = None
            return report
        current = [np.asarray(p, np.float64) for p in report['contours_source_px']]
        previous = self.previous
        if previous is not None and len(previous) == len(current) and 2 <= len(current) <= 4:
            centers = np.array([p.mean(axis=0) for p in current])
            old_centers = np.array([p.mean(axis=0) for p in previous])
            order = min(permutations(range(len(current))),
                        key=lambda p: float(np.sum((centers[list(p)]-old_centers)**2)))
            matched = [current[i] for i in order]
            aligned = []
            for old, new in zip(previous, matched):
                if old.shape != new.shape:
                    break
                new = min((np.roll(new, k, axis=0) for k in range(len(new))),
                          key=lambda p: float(np.sum((p-old)**2)))
                if float(np.max(np.linalg.norm(new-old, axis=1))) > 3.0:
                    break  # Movement or changed topology: show the fresh geometry.
                aligned.append(.55 * old + .45 * new)
            if len(aligned) == len(current):
                current = aligned
        self.previous = [p.copy() for p in current]
        return dict(report, contours_source_px=[p.tolist() for p in current])
