"""在隔离候选包中测真实录制几何、PL 传输与预览；不构造执行器。"""
import cProfile
import json
import logging
import os
from pathlib import Path
import time

import numpy as np
import typer

app = typer.Typer(pretty_exceptions_show_locals=False)


@app.command()
def cards(output: Path = typer.Argument(...), source: Path = typer.Argument(...),
          count: int = typer.Option(30), start: int = typer.Option(0),
          backend: str = typer.Option('pl'), profile: bool = typer.Option(False)):
    from ec_26_e.control import PuzzlePiece, PlanningOptions, plan_sheet_moves
    import ec_26_e.control.planner as bridge
    from ec_26_e.tasks.execution_lock import TaskExecutionLock
    os.environ['EC_RIGID_BACKEND'] = backend
    if backend not in ('pl', 'cpu'):
        raise ValueError('backend must be pl or cpu')
    os.environ['EC_CLEARANCE_BACKEND'] = 'cpu'
    data = json.loads(source.read_text())
    rows = data['observations'] if 'observations' in data else [r for r in data['rows'] if r['name'].startswith('live')]
    results = []
    output.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=str(output.with_suffix('.log')), level=logging.INFO)
    profiler = cProfile.Profile()
    with TaskExecutionLock():
        for row in rows[start:start+count]:
            if 'pieces' in row:
                pieces = tuple(PuzzlePiece(p['id'], tuple(map(tuple, p['vertices_mm']))) for p in row['pieces'])
            else:
                pieces = tuple(PuzzlePiece(str(i+1), tuple((y*207/539,(1-x/761)*292) for x,y in v)) for i,v in enumerate(row['vertices']))
            before = {key: getattr(bridge._backend,key,0) for key in ('request_count','batch_count','numerical_cpu_fallbacks','transfer_seconds','kernel_wait_seconds')}
            started = time.perf_counter()
            if profile: profiler.enable()
            try:
                plan = plan_sheet_moves(pieces, PlanningOptions(mode='random',noise_bound_mm=2,competition_rules=True))
                result = dict(frame=row.get('frame',row.get('name')),success=True,seconds=time.perf_counter()-started,plan=plan.to_dict())
            except Exception as error:
                result = dict(frame=row.get('frame',row.get('name')),success=False,seconds=time.perf_counter()-started,error=str(error))
            finally:
                if profile: profiler.disable()
            result['pl'] = {key:getattr(bridge._backend,key,0)-value for key,value in before.items()}
            results.append(result)
            output.write_text(json.dumps(dict(backend=backend,rows=results),indent=2))
            print(result['frame'], result['success'], round(result['seconds'],3), result['pl'], flush=True)
    if profile: profiler.dump_stats(str(output.with_suffix('.prof')))
    print('success',sum(r['success'] for r in results),'p50/p95/max',np.percentile([r['seconds'] for r in results],[50,95,100]),flush=True)


if __name__ == '__main__':
    app()
