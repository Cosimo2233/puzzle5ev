"""Summarize measured timings and independently check exported plan geometry."""
import argparse
import json
from itertools import combinations
from pathlib import Path

import numpy as np
from shapely.geometry import Polygon


def read_rows(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def log_metrics(path):
    """Read emitted counters, never infer hardware activity from selected backend."""
    result = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        for marker in ('TASK_PERFORMANCE', 'PL_PERFORMANCE', 'FIXED_FIT_PERFORMANCE',
                       'RANDOM_FIT_PERFORMANCE', 'SOLVER_PERFORMANCE'):
            prefix = marker+' '
            if prefix in line:
                result[marker] = json.loads(line.split(prefix, 1)[1])
    return result


def summarize(rows):
    groups = {}
    for row in rows:
        key = row.get('mode', 'random')+str(row.get('count', 4))
        groups.setdefault(key, []).append(row)
    result = {}
    for key, values in groups.items():
        seconds = [v['seconds'] for v in values]
        result[key] = dict(trials=len(values), successes=sum(v['success'] for v in values),
            median_seconds=float(np.median(seconds)), p95_seconds=float(np.percentile(seconds, 95)),
            failed_cases=[v.get('case', v.get('trial')) for v in values if not v['success']])
        for core, counter in (('rigid', 'request_count'), ('rigid', 'batch_count'),
                              ('rigid', 'numerical_cpu_fallbacks'), ('clearance', 'scan_count')):
            result[key][core+'_'+counter] = sum(v.get('pl', {}).get(core, {}).get(counter, 0) for v in values)
    return result


def validate_plan(plan, expected_count):
    moves = plan['moves']
    assert len(moves) == expected_count, (len(moves), expected_count)
    assert np.isfinite(plan['confidence']) and 0 <= plan['confidence'] <= 1
    assert len({m['piece_id'] for m in moves}) == len(moves)
    polygons = []
    for move in moves:
        points = np.asarray(move['target_vertices_a4_mm'])
        assert np.isfinite(points).all()
        polygon = Polygon(points)
        assert polygon.is_valid and polygon.area > 0
        polygons.append(polygon)
        assert points[:, 0].min() >= -1e-4 and points[:, 0].max() <= 207+1e-4
        assert points[:, 1].min() >= 146-1e-4 and points[:, 1].max() <= 292+1e-4
        transform = move['rigid_transform']
        angle = np.deg2rad(transform['rotation_deg_clockwise'])
        rotation = np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
        predicted = np.asarray(move['observed_vertices_a4_mm']) @ rotation.T + transform['translation_a4_mm']
        np.testing.assert_allclose(predicted, points, atol=1e-4, rtol=0)
    overlap = max((a.intersection(b).area for a, b in combinations(polygons, 2)), default=0)
    assert overlap <= 1e-4, overlap


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--prefix', default='final')
    parser.add_argument('--require-complete', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = {'timing': {}, 'exported_plan_checks': {}, 'limits': [
        'Geometry-only timings exclude acquisition and vision.',
        'Recorded and synthetic scenes do not establish real-camera P95 or physical accuracy.',
        'All validation is plan-only; no actuator was started.']}
    rows_by_name = {}
    paths = [args.root/'baseline-cpu.jsonl', *sorted(args.root.glob(args.prefix+'-*.jsonl'))]
    for path in paths:
        if not path.exists():
            continue
        rows = read_rows(path)
        for row in rows:
            if 'trial' not in row:
                continue
            log = path.with_name(path.stem+f"-plan-{row['trial']:02d}.log")
            if log.exists():
                row['metrics'] = log_metrics(log)
                hardware = row['metrics'].get('PL_PERFORMANCE', {}).get('backends', {})
                row['pl'] = {key.removesuffix('_backend'): value for key, value in hardware.items()}
        rows_by_name[path.stem] = rows
        result['timing'][path.stem] = summarize(rows)
        checked, failures = 0, []
        for row in rows:
            if not row['success']:
                continue
            plan = row.get('plan')
            if plan is None and 'trial' in row:
                destination = path.with_name(path.stem+f"-plan-{row['trial']:02d}.json")
                plan = json.loads(destination.read_text(encoding='utf-8'))
            if plan is not None:
                try:
                    validate_plan(plan, row.get('count', 4))
                    checked += 1
                except Exception as error:
                    failures.append({'case': row.get('case', row.get('trial')), 'error': str(error)})
        result['exported_plan_checks'][path.stem] = {'passed': checked, 'failures': failures}
    result['pipeline_metrics'] = {}
    for name, rows in rows_by_name.items():
        measured = [r for r in rows if r.get('metrics')]
        if not measured:
            continue
        groups = {}
        for row in measured:
            groups.setdefault(row.get('mode', 'random')+str(row.get('count', 4)), []).append(row)
        result['pipeline_metrics'][name] = {}
        for group, values in groups.items():
            tasks = [r['metrics']['TASK_PERFORMANCE'] for r in values
                     if 'TASK_PERFORMANCE' in r['metrics']]
            aggregate = dict(measured_trials=len(tasks))
            for key in ('capture_seconds', 'resize_seconds', 'detect_seconds', 'preprocess_seconds',
                        'review_seconds', 'solve_seconds', 'click_to_plan_seconds'):
                samples = [t[key] for t in tasks if key in t]
                if samples:
                    aggregate[key] = dict(median=float(np.median(samples)), p95=float(np.percentile(samples, 95)))
            for key in ('frame_count', 'pack_seconds', 'dma_seconds', 'copy_seconds'):
                aggregate['vision_pl_'+key] = sum(t.get('vision_pl', {}).get(key, 0) for t in tasks)
            aggregate['fixed_cpu_tie_fallbacks'] = sum(r['metrics'].get('FIXED_FIT_PERFORMANCE', {}).get('cpu_tie_fallbacks', 0) for r in values)
            result['pipeline_metrics'][name][group] = aggregate
    masks = args.root/(args.prefix+'-synthetic-mask-pl.json')
    if masks.exists():
        rows = json.loads(masks.read_text(encoding='utf-8'))
        result['preprocessing'] = {'trials': len(rows), 'detected': sum(r['success'] for r in rows),
                                  'exact_masks': sum(r.get('exact_mask', False) for r in rows)}
        for kind in ('reference', 'selected'):
            for clock in ('cpu', 'wall'):
                key = kind+'_preprocess_'+clock+'_seconds'
                values = [r[key] for r in rows if key in r]
                if values:
                    result['preprocessing'][key+'_median'] = float(np.median(values))
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))
    assert not any(v['failures'] for v in result['exported_plan_checks'].values()), 'Exported plan check failed'
    if args.require_complete:
        required = {'geometry-cpu': ('fixed4', 'random2', 'random3', 'random4'),
                    'geometry-pl': ('fixed4', 'random2', 'random3', 'random4'),
                    'replay-cpu': ('fixed4',), 'replay-pl': ('fixed4',),
                    'synthetic-pipeline-pl': ('random2', 'random3', 'random4')}
        for name, groups in required.items():
            report = result['timing'][args.prefix+'-'+name]
            for group in groups:
                assert report[group]['trials'] == report[group]['successes'] == 30, (name, group)
        masks = result['preprocessing']
        assert masks['trials'] == masks['detected'] == masks['exact_masks'] == 90
        for name in ('numeric-pl', 'negative-pl'):
            report = json.loads((args.root/(args.prefix+'-'+name+'.json')).read_text(encoding='utf-8'))
            assert report['passed'], name


if __name__ == '__main__':
    main()
