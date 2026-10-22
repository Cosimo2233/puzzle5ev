"""核对真实 PL 候选距离与独立 GEOS 向量；不访问执行器。"""
import hashlib
import json
from pathlib import Path
import time

import numpy as np
import typer

from ec_26_e.control.pl_candidate import PynqCandidateScanBackend, PAIRS
from ec_26_e.tasks.execution_lock import TaskExecutionLock

app = typer.Typer(pretty_exceptions_show_locals=False)


@app.command()
def main(vectors: Path = typer.Option(...), output: Path = typer.Option(...),
         bitstream: Path = typer.Option(..., envvar='EC_PL_BITSTREAM')):
    tokens = iter(vectors.read_text().split())
    cases = int(next(tokens))
    maximum = 0.0
    rows = []
    with TaskExecutionLock():
        menu = json.loads(Path('/run/ec26e/menu-status.json').read_text())
        if menu.get('busy') or menu.get('preview') or menu.get('transition'):
            raise RuntimeError('菜单忙，未开始验证')
        backend = PynqCandidateScanBackend(bitstream)
        try:
            for case in range(cases):
                count = int(next(tokens))
                packed = np.array([float(next(tokens)) for _ in range(580)], dtype=np.float32)
                reference = np.array([float(next(tokens)) for _ in range(count*6)]).reshape(count, 6)
                vertices = [packed[4+16*i:4+16*i+2*int(packed[i])].reshape(-1, 2)
                            for i in range(4) if packed[i] > 0]
                deltas = packed[68:68+8*count].reshape(count, 4, 2)[:, :len(vertices)]
                started = time.perf_counter()
                actual = backend.pair_distances(vertices, deltas)
                elapsed = time.perf_counter()-started
                active = [i for i, (_, b) in enumerate(PAIRS) if b < len(vertices)]
                error = float(np.max(np.abs(actual-reference[:, active])))
                assert error <= .005, (case, error)
                maximum = max(maximum, error)
                rows.append(dict(case=case, pieces=len(vertices), count=count, seconds=elapsed, max_error_mm=error))
            report = dict(passed=True, motion_executed=False, max_error_mm=maximum,
                vectors_sha256=hashlib.sha256(vectors.read_bytes()).hexdigest(),
                bit_sha256=hashlib.sha256(bitstream.read_bytes()).hexdigest(), rows=rows,
                counters={k:getattr(backend, k) for k in ('request_count', 'batch_count',
                    'transfer_seconds', 'kernel_wait_seconds', 'unsupported_count')})
        finally:
            backend.close()
    output.write_text(json.dumps(report, indent=2))
    print(json.dumps(report))


if __name__ == '__main__':
    app()
