"""七片连续稳定识别、公共求解和显式标定后的搬运生命周期。"""
from __future__ import annotations

from dataclasses import replace
import json
import logging
import os
from pathlib import Path
import time

import cv2
import numpy as np
from scipy.optimize import linear_sum_assignment
from puzzle_solver import classify_tangram_piece, plan_tangram_moves, PlanningError

from ec_26_e.actuation.compiler import compile_move_plan
from ec_26_e.cam import FFmpegCamera, CameraTimeoutError
from ec_26_e.config.tangram import TangramConfig
from ec_26_e.gui.plan_preview import PlanPreviewPublisher, sheet_to_camera, display_call
from ec_26_e.tasks.contracts import TaskContext
from ec_26_e.tasks.task1.review import review_visual_frame
from ec_26_e.vision import Resize
from ec_26_e.vision.tangram import TangramPreprocessor
from ec_26_e.vision.traditional import TraditionalVisionDetector

logger = logging.getLogger(__name__)


def annotate_regions(raw, rect, rotation, planning, pixel_scale=(1.,1.)):
    canvas = raw.copy()
    for bounds, label, color in ((planning.source_bounds_mm,'SOURCE',(70,220,90)),
                                  (planning.target_bounds_mm,'TARGET - keep empty',(0,190,255))):
        x0,y0,x1,y1 = bounds
        points = np.rint(sheet_to_camera(((x0,y0),(x1,y0),(x1,y1),(x0,y1)),
            rect.homography,rect.target_size,rotation)*np.array(pixel_scale)).astype(np.int32)
        cv2.polylines(canvas,[points],True,color,2)
        cv2.putText(canvas,label,tuple(points[0]),cv2.FONT_HERSHEY_SIMPLEX,.65,color,2)
    return canvas


class TangramStability:
    """同形片采用最小顶点位移匹配，比较整个周期参考帧，避免慢漂移通过。"""
    def __init__(self, config):
        self.config = config
        self.reset()

    def reset(self):
        self.reference = self.rect = self.kinds = None
        self.count = 0
        self.last_reason = f'等待{self.config.stable_frames}帧观测'

    def add(self, pieces, rect):
        kinds = [classify_tangram_piece(p.vertices_mm,self.config.planning.side_mm,
            self.config.planning.vertex_tolerance_mm) for p in pieces]
        if sorted(kinds) != sorted(('large','large','small','small','medium','square','parallel')):
            raise PlanningError('七巧板片型不完整')
        if self.reference is not None:
            costs = np.full((7,7),1e6)
            for i, old in enumerate(self.reference):
                a = np.array(old.vertices_mm)
                for j, new in enumerate(pieces):
                    b = np.array(new.vertices_mm)
                    if self.kinds[i] == kinds[j] and len(a) == len(b):
                        costs[i,j] = min(float(np.max(np.linalg.norm(a-np.roll(b,k,axis=0),axis=1)))
                                         for k in range(len(b)))
            rows,cols = linear_sum_assignment(costs)
            # 透视变化同样在参考平面毫米坐标中审查。
            corners = cv2.perspectiveTransform(np.asarray(rect.source_corners_px,np.float32).reshape(1,4,2),
                                               self.rect.homography)[0]
            expected = np.array(((0,0),(rect.target_size[0]-1,0),
                (rect.target_size[0]-1,rect.target_size[1]-1),(0,rect.target_size[1]-1)))
            delta = (corners-expected)*np.array((292/(rect.target_size[0]-1),207/(rect.target_size[1]-1)))
            if costs[rows,cols].max() > self.config.stability_tolerance_mm or np.linalg.norm(delta,axis=1).max() > self.config.stability_tolerance_mm:
                self.reset()
                self.last_reason = f'轮廓跳变 {costs[rows,cols].max():.2f} mm，工作区跳变 {np.linalg.norm(delta,axis=1).max():.2f} mm'
        if self.reference is None:
            self.reference,self.rect,self.kinds = pieces,rect,kinds
        self.count += 1
        return self.count >= self.config.stable_frames


def validate_tangram_machine_plan(plan, config):
    """完整七片及拾取/放置头坐标在访问运动设备前通过软件限位。"""
    if plan.mode != 'tangram' or len(plan.moves) != 7 or plan.ambiguous or plan.confidence < .5:
        raise PlanningError('七巧板方案不完整或置信不足')
    compile_move_plan(plan,config.actuation)


def run_tangram(config, *, execute_motion=False, camera_factory=FFmpegCamera,
                resizer_factory=Resize, detector_factory=None, actuator_factory=None):
    if not isinstance(config,TangramConfig) or type(execute_motion) is not bool:
        raise TypeError('非法七巧板任务配置或运动选项')
    if execute_motion and (not config.motion_verified or config.actuation.bridge_port != '/dev/ec26e-m0-bridge'):
        raise PlanningError('七巧板接线、厚度/Z 高度、吸取偏心和旋转标定尚未核验，禁止运动')
    params = config.detect1
    detector = (detector_factory(params) if detector_factory else
                TraditionalVisionDetector(params,expected_pieces=7,preprocessor=TangramPreprocessor()))
    camera = None
    rotation = config.base.options.landscape_rotation
    display = PlanPreviewPublisher.from_environment(rotation)
    stable = TangramStability(config)
    last_error = '尚未识别到完整七片'
    try:
        camera = camera_factory(config.camera)
        resizer = resizer_factory(config.resize)
        camera.start()
        sequence = None
        deadline = time.monotonic()+config.acquisition_timeout_seconds
        while time.monotonic() < deadline:
            try:
                frame = camera.read_latest(after_sequence=sequence,timeout=min(1.,max(.001,deadline-time.monotonic())))
                if sequence is not None and frame.sequence_id <= sequence:
                    raise ValueError('相机重复帧不能计入稳定周期')
                sequence = frame.sequence_id
                rect = resizer.resize(frame.image_bgr)
                display_call(display,'acquiring',replace(frame,image_bgr=annotate_regions(frame.image_bgr,rect,rotation,config.planning)))
                result = detector.detect(rect.image_bgr)
                if len(result.pieces) != 7:
                    raise ValueError(detector.last_failure or '必须识别到七个分离的连通域')
                reviewed = review_visual_frame(frame,rect,result,rotation)
                pieces = reviewed.pieces
                if stable.add(pieces,rect):
                    display_call(display,'capture',replace(frame,image_bgr=annotate_regions(frame.image_bgr,rect,rotation,config.planning)),rect)
                    break
                last_error = f'{stable.last_reason}（{stable.count}/{config.stable_frames}）'
            except (CameraTimeoutError,ValueError,PlanningError) as error:
                if config.stable_frames == 1:
                    raise PlanningError(f'单帧识别或规划失败：{error}') from error
                stable.reset()
                last_error = str(error)
                logger.info('七巧板等待有效场景：%s',last_error)
        else:
            raise PlanningError(f'七巧板采集超时：{last_error}')
    except BaseException as error:
        display_call(display,'fail',error)
        raise
    finally:
        try:
            if camera is not None: camera.close()
        finally:
            detector.close()
    # 单张照片已冻结，计算期间停止相机采集，不再读取或分析其他帧。
    try:
        plan = plan_tangram_moves(pieces,config.planning)
        validate_tangram_machine_plan(plan,config)
        display_call(display,'publish',plan)
    except BaseException as error:
        display_call(display,'fail',error)
        raise
    # 相机已释放；全部检查完成才构造执行器。KeyboardInterrupt 沿用原执行器清理。
    if execute_motion:
        if actuator_factory is None:
            from ec_26_e.tasks.task1.actuation import MovePlanActuator
            actuator_factory = MovePlanActuator
        actuator_factory(config.actuation).execute(plan)
    return plan


def execute_tangram(config: TangramConfig, context: TaskContext) -> None:
    execute = os.environ.get('EC_MOTION_ENABLED','false').lower() == 'true'
    path = Path(os.environ.get('EC_TANGRAM_RESULT','/run/ec26e/tangram-result.json'))
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary = path.with_suffix('.tmp')
    def save(report):
        temporary.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        temporary.replace(path)
    save(dict(success=False,state='running',motion_requested=execute))
    try:
        plan = run_tangram(config,execute_motion=execute)
    except BaseException as error:
        save(dict(success=False,state='cancelled' if isinstance(error,KeyboardInterrupt) else 'failed',
                  motion_requested=execute,motion_executed=None if execute else False,error=str(error)))
        raise
    save(dict(success=True,state='complete',motion_executed=execute,
        temporal_stability_validated=config.stable_frames>1,acquisition_mode='single_frame' if config.stable_frames==1 else 'multi_frame',
        pattern_id=config.planning.pattern_id,plan=plan.to_dict()))
    logger.info('七巧板完成：%s，运动=%s',config.planning.pattern_id,execute)
