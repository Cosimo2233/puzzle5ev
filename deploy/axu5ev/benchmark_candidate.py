"""只求解同一输入集，记录候选核的传输、核时间与完整方案。"""
from collections import defaultdict
import json
import logging
import os
from pathlib import Path
import time

import numpy as np
import typer

app = typer.Typer(pretty_exceptions_show_locals=False)


@app.command()
def main(corpus: Path = typer.Option(...), output: Path = typer.Option(...),
         candidate: str = typer.Option('cpu'), variant: str = typer.Option(...),
         limit_per_count: int = typer.Option(30, min=1), rigid: str = typer.Option('pl')):
    if candidate not in ('cpu', 'pl') or rigid not in ('cpu', 'pl'):
        raise ValueError('后端必须为 cpu 或 pl')
    os.environ.update(EC_MOTION_ENABLED='false', EC_RIGID_BACKEND=rigid,
                      EC_CLEARANCE_BACKEND='cpu', EC_CANDIDATE_BACKEND=candidate)
    from puzzle_solver import PuzzlePiece, PlanningOptions
    from ec_26_e.control import planner
    from ec_26_e.tasks.execution_lock import TaskExecutionLock
    measurements = []

    class Records(logging.Handler):
        def emit(self, record):
            message = record.getMessage()
            for marker in ('PL_PERFORMANCE ', 'CANDIDATE_CLEARANCE_PERFORMANCE '):
                if message.startswith(marker):
                    measurements.append(dict(kind=marker.strip(), data=json.loads(message[len(marker):])))

    handler = Records()
    for name in ('ec_26_e.control.planner', 'puzzle_solver._engine.solver.random_clearance'):
        logger = logging.getLogger(name)
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
    rows = json.loads(corpus.read_text())
    selected, counts = [], defaultdict(int)
    for row in rows:
        n = len(row['pieces'])
        if row['mode'] == 'random' and counts[n] < limit_per_count:
            selected.append(row)
            counts[n] += 1
    if not selected:
        raise ValueError('没有随机测试输入')
    output.parent.mkdir(parents=True, exist_ok=True)
    results = []
    with TaskExecutionLock(), output.open('x') as stream:
        menu = json.loads(Path('/run/ec26e/menu-status.json').read_text())
        if menu.get('busy') or menu.get('preview') or menu.get('transition'):
            raise RuntimeError('菜单忙，未开始验证')
        for row in selected:
            pieces = tuple(PuzzlePiece(p['id'], tuple(map(tuple, p['vertices_mm']))) for p in row['pieces'])
            measurements.clear()
            started = time.perf_counter()
            record = dict(case=row['case'], count=len(pieces), variant=variant, motion_executed=False)
            try:
                plan = planner.plan_sheet_moves(pieces, PlanningOptions(mode='random',
                    noise_bound_mm=row.get('noise_bound_mm', 0), competition_rules=True))
                record.update(success=True, plan=plan.to_dict())
            except Exception as error:
                record.update(success=False, error=str(error))
            record.update(seconds=time.perf_counter()-started, measurements=list(measurements))
            stream.write(json.dumps(record, ensure_ascii=False)+'\n')
            stream.flush()
            results.append(record)
            print(json.dumps({k:v for k,v in record.items() if k not in ('plan', 'measurements')}), flush=True)
    summary = {str(n):dict(trials=sum(r['count']==n for r in results),
        successes=sum(r['count']==n and r['success'] for r in results),
        median_seconds=float(np.median([r['seconds'] for r in results if r['count']==n])),
        p95_seconds=float(np.percentile([r['seconds'] for r in results if r['count']==n], 95)))
        for n in sorted(counts)}
    output.with_suffix('.summary.json').write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary), flush=True)


if __name__ == '__main__':
    app()
