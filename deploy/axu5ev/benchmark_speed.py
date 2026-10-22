"""Board-only, plan-only benchmark. No actuator imports or motion commands.

Run the same immutable corpus with baseline/optimized PYTHONPATH, CPU/PL.
Results are flushed per trial so interrupted runs retain their evidence.
"""
from pathlib import Path
import argparse
import json
import logging
import os
import time


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--corpus', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--backend', choices=('cpu', 'pl'), default='cpu')
    parser.add_argument('--variant', required=True)
    parser.add_argument('--limit', type=int, default=0)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    os.environ.update(EC_MOTION_ENABLED='false', EC_RIGID_BACKEND=args.backend,
                      EC_CLEARANCE_BACKEND=args.backend)
    import numpy as np
    from puzzle_solver import PuzzlePiece, PlanningOptions
    from ec_26_e.control import planner
    from ec_26_e.tasks.execution_lock import TaskExecutionLock
    logging.basicConfig(level=logging.WARNING)
    rows = json.loads(args.corpus.read_text())
    if args.limit:
        rows = rows[:args.limit]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    records = [json.loads(line) for line in args.output.read_text().splitlines()] if args.resume and args.output.exists() else []
    done = {row['case'] for row in records}
    with TaskExecutionLock(), args.output.open('a' if args.resume else 'w') as output:
        for row in rows:
            if row['case'] in done:
                continue
            pieces = tuple(PuzzlePiece(p['id'], tuple(map(tuple, p['vertices_mm']))) for p in row['pieces'])
            fields = dict(mode=row['mode'], noise_bound_mm=row.get('noise_bound_mm', 0))
            if 'competition_rules' in PlanningOptions.__dataclass_fields__:
                fields['competition_rules'] = row['mode'] == 'random'
            def counters():
                return {name: {key: getattr(device, key, 0) for key in (
                    'request_count', 'batch_count', 'scan_count', 'numerical_cpu_fallbacks',
                    'pack_seconds', 'transfer_seconds', 'kernel_wait_seconds')}
                    for name, device in [('rigid', planner._backend), ('clearance', planner._clearance_backend)]}
            before = counters()
            started = time.perf_counter()
            record = dict(case=row['case'], mode=row['mode'], count=len(pieces), variant=args.variant,
                          backend=args.backend, measurement='board_geometry_only')
            try:
                plan = planner.plan_sheet_moves(pieces, PlanningOptions(**fields))
                record.update(success=True, plan=plan.to_dict())
            except Exception as error:
                record.update(success=False, error=str(error))
            record['seconds'] = time.perf_counter()-started
            after = counters()
            record['pl'] = {name: {k: value-before[name][k] for k, value in data.items()} for name, data in after.items()}
            output.write(json.dumps(record, ensure_ascii=False)+'\n')
            output.flush()
            records.append(record)
            print(json.dumps({k:v for k,v in record.items() if k not in ('plan','pl')}), flush=True)
    summary = {}
    for mode, count in sorted({(r['mode'],r['count']) for r in records}):
        selected = [r for r in records if (r['mode'],r['count']) == (mode,count)]
        durations = [r['seconds'] for r in selected]
        summary[f'{mode}{count}'] = dict(trials=len(selected), successes=sum(r['success'] for r in selected),
            median_seconds=float(np.median(durations)), p95_seconds=float(np.percentile(durations,95)))
    args.output.with_suffix('.summary.json').write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary), flush=True)


if __name__ == '__main__':
    main()
