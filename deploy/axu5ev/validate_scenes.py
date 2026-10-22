"""Replay labelled synthetic camera scenes; optional exact CPU/PL mask comparison."""
from pathlib import Path
import argparse
import json
import time
import cv2
import numpy as np
from ec_26_e.gui.controller import load_profile
from ec_26_e.gui.menu_model import MODES
from ec_26_e.vision import Resize
from ec_26_e.vision.factory import create_production_detector, preprocess_config
from ec_26_e.vision.preprocess import CpuPreprocessor, PreprocessConfig
from ec_26_e.tasks.execution_lock import TaskExecutionLock


def main():
    parser=argparse.ArgumentParser(__doc__)
    parser.add_argument('--frames',type=Path,required=True)
    parser.add_argument('--env',type=Path,required=True)
    parser.add_argument('--config-dir',type=Path,required=True)
    parser.add_argument('--backend',choices=('cpu','pl'),default='cpu')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--solve',action='store_true')
    args=parser.parse_args()
    cv2.setNumThreads(1)
    profile=load_profile(args.env,args.config_dir,next(k for k,v in MODES.items() if v[0]=='random'),args.backend)
    cpu=CpuPreprocessor(preprocess_config(profile.detect1))
    resizer=Resize(profile.resize)
    records=[]
    with TaskExecutionLock(), create_production_detector(profile.detect1) as owner:
        detector=owner.detector
        for index,row in enumerate(json.loads((args.frames/'manifest.json').read_text())):
            started=time.perf_counter()
            try:
                from ec_26_e.cam import CameraFrame
                frame=CameraFrame(index+1,time.monotonic_ns(),cv2.imread(str(args.frames/row['file'])))
                rect=resizer.resize(frame.image_bgr)
                result=detector.detect(rect.image_bgr)
                record=dict(case=row['case'],expected_count=row['count'],count=len(result.pieces),
                    success=len(result.pieces)==row['count'],reason=detector.last_failure)
                # This diagnostic extra transfer is excluded from vision timing.
                record['vision_seconds']=time.perf_counter()-started
                wall_started, cpu_started = time.perf_counter(), time.process_time()
                reference=cpu.process(rect.image_bgr,index+1)
                record['reference_preprocess_wall_seconds'] = time.perf_counter()-wall_started
                record['reference_preprocess_cpu_seconds'] = time.process_time()-cpu_started
                wall_started, cpu_started = time.perf_counter(), time.process_time()
                actual=detector.preprocessor.process(rect.image_bgr,detector._frame_id+1)
                record['selected_preprocess_wall_seconds'] = time.perf_counter()-wall_started
                record['selected_preprocess_cpu_seconds'] = time.process_time()-cpu_started
                detector._frame_id+=1
                record['exact_mask']=bool(np.array_equal(reference,actual))
                record['stage_ms']=detector.last_diagnostics.stage_ms
                owner.reset()
                for _ in range(6): stable=owner.update(result,())
                record['stable_count']=len(stable.pieces)
                record['success'] &= len(stable.pieces)==row['count']
                if args.solve and record['success']:
                    from ec_26_e.tasks.task1.review import review_visual_frame
                    from ec_26_e.control import plan_sheet_moves
                    pieces=review_visual_frame(frame,rect,result,profile.options.landscape_rotation).pieces
                    solve_started=time.perf_counter()
                    plan=plan_sheet_moves(pieces,profile.options.planning_options)
                    record['solve_seconds']=time.perf_counter()-solve_started
                    record['plan']=plan.to_dict()
            except Exception as error:
                record=dict(case=row['case'],success=False,error=str(error))
            records.append(record)
    args.output.write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(trials=len(records),successes=sum(r['success'] for r in records),
        exact_masks=sum(r.get('exact_mask',False) for r in records))))
    for row in records:
        if not row['success']: print(json.dumps(row,ensure_ascii=False))
    if any(not row['success'] or not row.get('exact_mask', False) for row in records):
        raise SystemExit(1)


if __name__=='__main__': main()
