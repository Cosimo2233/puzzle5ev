"""Measure CPU packing into coherent DMA memory; no DMA submission or motion."""
import argparse
import json
import os
from pathlib import Path
import time

import numpy as np

from ec_26_e.tasks.execution_lock import TaskExecutionLock
from ec_26_e.vision.linux_pl import LinuxPreprocessor


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    image = np.random.default_rng(21).integers(0, 256, (540, 762, 3), dtype=np.uint8)
    packed = np.zeros((540, 762, 4), dtype=np.uint8)
    rows = []
    with TaskExecutionLock():
        device = LinuxPreprocessor(os.environ['EC_PL_BITSTREAM'])
        try:
            target = device._tx[:540*762].reshape(540, 762, 4)
            for trial in range(30):
                # Alternate order to reduce cache/order bias.
                for method in (('direct', 'staged') if trial % 2 else ('staged', 'direct')):
                    started, cpu = time.perf_counter(), time.process_time()
                    if method == 'direct':
                        target[:, :, :3] = image
                    else:
                        packed[:, :, :3] = image
                        np.copyto(target, packed)
                    rows.append(dict(trial=trial, method=method, seconds=time.perf_counter()-started,
                                     cpu_seconds=time.process_time()-cpu))
                    assert np.array_equal(target[:, :, :3], image)
                    assert not target[:, :, 3].any()
            del target
        finally:
            device.close()
    summary = {name: {key: float(np.median([r[key] for r in rows if r['method'] == name]))
                      for key in ('seconds', 'cpu_seconds')} for name in ('direct', 'staged')}
    args.output.write_text(json.dumps(dict(summary=summary, rows=rows), indent=2))
    print(json.dumps(summary))


if __name__ == '__main__':
    main()
