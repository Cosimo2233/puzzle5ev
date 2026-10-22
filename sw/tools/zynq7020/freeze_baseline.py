"""在指定源码目录上冻结合成输入及算法/协议基线，不访问任何设备。

poetry run python tools/zynq7020/freeze_baseline.py --source build/upstream --output baselines/upstream
"""
import argparse
import hashlib
import json
import platform
import subprocess
import sys
import time
from pathlib import Path

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    source=args.source.resolve(); output=args.output.resolve()
    sys.path.insert(0,str(source/'src'))
    import cv2
    import numpy as np
    from ec_26_e.control import PuzzlePiece, PlanningOptions, plan_sheet_moves
    from importlib import import_module
    engine = ('ec_26_e.control._engine' if (source/'src/ec_26_e/control/_engine').is_dir()
              else 'puzzle_solver._engine')
    fixed_pieces = import_module(engine+'.generator.fixed_generator').fixed_pieces
    random_pieces = import_module(engine+'.generator.random_generator').random_pieces
    shuffle_and_place = import_module(engine+'.generator.shuffle').shuffle_and_place
    # Protocol-only imports on the Linux original need Linux-specific modules;
    # read the baseline protocol implementation without opening hardware.
    output.mkdir(parents=True,exist_ok=True)
    revision=subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()
    report={'source_revision':revision,'platform':platform.platform(),'source_kind':'synthetic','hardware_measured':False,'cases':[]}
    report['source_dirty']=bool(subprocess.check_output(['git','-C',str(source),'status','--porcelain'],text=True).strip())
    fingerprint=hashlib.sha256()
    for path in sorted((source/'src').rglob('*.py')):
        fingerprint.update(path.relative_to(source).as_posix().encode())
        fingerprint.update(b'\0')
        fingerprint.update(path.read_bytes())
    report['source_python_sha256']=fingerprint.hexdigest()
    if engine == 'puzzle_solver._engine':
        from importlib.metadata import version
        package_root = Path(import_module('puzzle_solver').__file__).parent
        solver_fingerprint = hashlib.sha256()
        for path in sorted(package_root.rglob('*.py')):
            solver_fingerprint.update(path.relative_to(package_root).as_posix().encode())
            solver_fingerprint.update(b'\0')
            solver_fingerprint.update(path.read_bytes())
        report['solver'] = {'version': version('puzzle-solver'),
                            'source_python_sha256': solver_fingerprint.hexdigest()}
    for name,mode,count,topology in [('fixed4','fixed',4,None),('random2','random',2,'two_polyline'),('random3','random',3,'three_y'),('random4','random',4,'four_fan')]:
        rng=np.random.default_rng(2600+count)
        if mode=='fixed': original=fixed_pieces()
        else: original,_,_=random_pieces(rng,piece_count=count,target_width=100,target_height=70,topology=topology)
        scattered=shuffle_and_place(original,rng)
        pieces=tuple(PuzzlePiece(p.public_id,tuple(p.clean_vertices)) for p in scattered)
        payload={'schema_version':'1.0','mode':mode,'noise_bound_mm':0.0,'pieces':[{'id':p.piece_id,'vertices_mm':p.vertices_mm} for p in pieces]}
        (output/f'{name}.json').write_text(json.dumps(payload,indent=2),encoding='utf-8')
        image=np.full((621,876,3),(180,60,30),np.uint8)
        for piece in pieces:
            xy=np.asarray(piece.vertices_mm)
            landscape=np.column_stack(((1-xy[:,1]/292)*875,xy[:,0]/207*620))
            cv2.fillPoly(image,[np.rint(landscape).astype(np.int32)],(240,240,240))
        cv2.imwrite(str(output/f'{name}.png'),image)
        start=time.perf_counter()
        item={'name':name,'mode':mode,'count':count}
        try:
            item['plan']=plan_sheet_moves(pieces,PlanningOptions(mode=mode)).to_dict()
            item['success']=True
        except Exception as error:
            item.update(success=False,error=str(error))
        item['solve_ms']=(time.perf_counter()-start)*1000
        report['cases'].append(item)
    report['files_sha256']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir() if p.suffix in {'.png','.json'} and p.name!='report.json'}
    (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')

if __name__=='__main__': main()
