"""显式触发的七巧板单片实物验收；完整七片方案先验证，默认不运动。"""
from dataclasses import replace
import json
import logging
from pathlib import Path
import math

import cv2
import numpy as np
from scipy.optimize import linear_sum_assignment
from shapely.geometry import Polygon
from shapely.geometry.polygon import orient
import typer

app=typer.Typer(pretty_exceptions_show_locals=False)
logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')


def pose_error(expected,actual):
    a=np.array(orient(Polygon(expected),sign=1).exterior.coords[:-1])
    b=np.array(orient(Polygon(actual),sign=1).exterior.coords[:-1])
    if len(a)!=len(b):return None
    matches=[]
    for shift in range(len(b)):
        q=np.roll(b,shift,axis=0);ac,bc=a.mean(axis=0),q.mean(axis=0)
        u,_,vt=np.linalg.svd((a-ac).T@(q-bc));d=np.eye(2);d[-1,-1]=np.linalg.det(u@vt)
        r=u@d@vt;t=bc-ac@r
        residual=float(np.max(np.linalg.norm(a@r+t-q,axis=1)))
        angle=math.degrees(math.atan2(r[0,1],r[0,0]));angle=(angle+180)%360-180
        matches.append((residual,abs(angle)))
    residual,angle=min(matches)
    center=Polygon(expected).centroid.distance(Polygon(actual).centroid)
    return dict(center_error_mm=center,angle_error_deg=angle,shape_residual_mm=residual)


@app.command()
def main(output:Path=typer.Option(...,'--output'),execute:bool=typer.Option(False,'--execute-first-piece')):
    from ec_26_e.config.tangram import load_tangram_config
    from ec_26_e.tasks.tangram import run_tangram,validate_tangram_machine_plan
    from ec_26_e.tasks.execution_lock import TaskExecutionLock
    from ec_26_e.cam import FFmpegCamera
    from ec_26_e.vision import Resize
    from ec_26_e.vision.traditional import TraditionalVisionDetector
    from ec_26_e.vision.preprocess import CpuPreprocessor
    from ec_26_e.vision.factory import preprocess_config
    from ec_26_e.tasks.task1.review import review_visual_frame
    state=json.loads(Path('/run/ec26e/menu-status.json').read_text())
    menu=json.loads(Path('/opt/ec26e/config/axu5ev/menu.json').read_text())
    if state.get('busy') or state.get('preview'):raise RuntimeError('菜单忙碌')
    if not (menu.get('motion_enabled') and menu.get('wiring_and_calibration_verified')):
        raise RuntimeError('原程序机械接线/标定和运动权限未核验')
    config=load_tangram_config(Path('/opt/ec26e/config/axu5ev/tasks/tangram.json'),
        env_path=Path('/opt/ec26e/config/axu5ev/machine.env'))
    if config.actuation.bridge_port!='/dev/ec26e-m0-bridge':raise RuntimeError('必须使用现有 M0 桥接')
    output.mkdir(parents=True,exist_ok=True)
    report=dict(motion_executed=False,inherited_calibration=True,actuation_overrides={})
    def save():
        (output/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    with TaskExecutionLock():
        plan=run_tangram(config,execute_motion=False)
        validate_tangram_machine_plan(plan,config)
        (output/'complete-plan.json').write_text(json.dumps(plan.to_dict(),ensure_ascii=False,indent=2))
        first=plan.moves[0]
        report.update(piece_id=first.piece_id,expected_target=first.target_vertices_a4_mm)
        save()
        if not execute:
            typer.echo('完整七片方案已保存；未执行运动。');return
        from ec_26_e.tasks.task1.actuation import MovePlanActuator
        single=replace(plan,moves=(replace(first,order=1),))
        report['motion_started']=True;save()
        try:
            actuator=MovePlanActuator(config.actuation)
            actuator.execute(single)
            report['motion_executed']=True;report['position_valid']=actuator.position_valid
            save()
            # 只拍一张新照片用于动作后验收，不持续循环观察。
            with FFmpegCamera(config.camera) as camera:
                frame=camera.read_latest(timeout=3)
                cv2.imwrite(str(output/'after.png'),frame.image_bgr)
                rect=Resize(config.resize).resize(frame.image_bgr)
                params=config.detect1
                with TraditionalVisionDetector(params,expected_pieces=7,
                        preprocessor=CpuPreprocessor(preprocess_config(params))) as detector:
                    detected=detector.detect(rect.image_bgr)
                    if len(detected.pieces)!=7:
                        raise RuntimeError('动作后未识别到完整七片：'+str(detector.last_failure))
                    observations=review_visual_frame(frame,rect,detected,config.base.options.landscape_rotation).pieces
                before=np.array([Polygon(m.observed_vertices_a4_mm).centroid.coords[0] for m in plan.moves])
                after=np.array([Polygon(p.vertices_mm).centroid.coords[0] for p in observations])
                distances=np.linalg.norm(before[:,None,:]-after[None,:,:],axis=2)
                rows,cols=linear_sum_assignment(distances)
                report['source_scene_max_center_delta_mm']=float(distances[rows,cols].max())
                report['observations']=[dict(id=p.piece_id,vertices_mm=p.vertices_mm) for p in observations]
                matches=[pose_error(first.target_vertices_a4_mm,p.vertices_mm) for p in observations]
                matches=[m for m in matches if m is not None]
                measured=min(matches,key=lambda m:m['center_error_mm'])
                report['target_detected']=measured['center_error_mm']<=10
                if not report['target_detected']:
                    report.update(measured=None,passed=False);save()
                    raise RuntimeError('目标区没有检出搬运碎片；检查 XY/Z 和磁铁实际动作，禁止继续整图')
                report['measured']=measured
                report['passed']=measured['center_error_mm']<=2 and measured['angle_error_deg']<=3
                save()
                if not report['passed']:raise RuntimeError('单片落点或角度未通过验收，禁止继续整图')
        except BaseException as error:
            report.update(error=str(error),passed=False);save();raise
    typer.echo(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':app()
