"""Serialize board benchmarks; never run competing timing workloads."""
from pathlib import Path
import os
import subprocess
import sys
import time
import argparse
import json

ROOT = Path('/opt/ec26e/validation/speed-20261021')


def run(name, script, arguments, variant='optimized'):
    summary = ROOT/(name+'.summary.json')
    if summary.exists() and name != 'smoke-replay-pl':
        data = json.loads(summary.read_text())
        groups = [data] if 'trials' in data else list(data.values())
        if any(group['successes'] != group['trials'] for group in groups):
            raise RuntimeError('Previous run has failures; preserve evidence and investigate: '+name)
        print('already complete',name,flush=True)
        return
    env = dict(os.environ, PYTHONPATH=str(ROOT/variant), EC_MOTION_ENABLED='false',
               EC_PL_ACCESS='linux', EC_PL_BITSTREAM='/opt/ec26e/staging/20261020/axu5ev-reset-bundle/puzzle5ev.bit')
    with (ROOT/(name+'.log')).open('w') as log:
        result = subprocess.run([sys.executable, str(ROOT/script), *arguments],
                                env=env, stdout=log, stderr=subprocess.STDOUT)
    print(name, result.returncode, flush=True)
    if result.returncode:
        raise SystemExit(result.returncode)


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--prefix', default='final')
    parser.add_argument('--skip-live', action='store_true')
    args = parser.parse_args()
    if not args.prefix.replace('-', '').isalnum():
        parser.error('prefix must contain only letters, digits and hyphens')
    common = ['--env', str(ROOT/'machine.env'), '--config-dir', str(ROOT/'tasks'),
              '--frames', '/opt/ec26e/validation/vision-20261021', '--variant', 'optimized', '--warm-start']
    name = args.prefix+'-numeric-pl'
    run(name, 'validate_pl_numeric.py', ['--output', str(ROOT/(name+'.json'))])
    name = args.prefix+'-negative-pl'
    run(name, 'validate_negative_cases.py', ['--root', str(ROOT), '--output', str(ROOT/(name+'.json'))])
    for backend in ('cpu','pl'):
        name = args.prefix+'-geometry-'+backend
        run(name, 'benchmark_speed.py', ['--corpus',str(ROOT/'corpus.json'), '--output',str(ROOT/(name+'.jsonl')),
                                       '--variant','optimized','--backend',backend])
    for backend in ('cpu','pl'):
        name = args.prefix+'-replay-'+backend
        run(name,'benchmark_pipeline.py',common+['--backend',backend,'--output',str(ROOT/(name+'.jsonl'))])
    if not args.skip_live:
        name = args.prefix+'-live-pl'
        run(name,'benchmark_pipeline.py',['--env',str(ROOT/'machine.env'),'--config-dir',str(ROOT/'tasks'),
            '--live','--warm-start','--variant','optimized','--backend','pl','--output',str(ROOT/(name+'.jsonl'))])
    name = args.prefix+'-synthetic-mask-pl'
    run(name,'validate_scenes.py',['--frames',str(ROOT/'synthetic'),
        '--env',str(ROOT/'machine.env'),'--config-dir',str(ROOT/'tasks'),
        '--backend','pl','--output',str(ROOT/(name+'.json'))])
    name = args.prefix+'-synthetic-pipeline-pl'
    run(name,'benchmark_pipeline.py',['--env',str(ROOT/'machine.env'),
        '--config-dir',str(ROOT/'tasks'),'--manifest',str(ROOT/'synthetic/manifest.json'),
        '--mode','random','--warm-start','--variant','optimized','--backend','pl','--output',str(ROOT/(name+'.jsonl'))])
    (ROOT/(args.prefix+'-queue.complete')).write_text('done\n')


if __name__ == '__main__':
    main()
