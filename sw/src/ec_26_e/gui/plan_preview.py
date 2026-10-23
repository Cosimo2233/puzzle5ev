"""把正式 MovePlan 投影到采图快照；仅显示，不读取或执行任何运动指令。"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import re
import time

import cv2
import numpy as np

logger = logging.getLogger(__name__)
COLORS = ((95, 210, 65), (35, 180, 255), (75, 85, 255), (215, 90, 225))  # BGR


def sheet_to_camera(points, homography, target_size, rotation):
    """严格反转 review_visual_frame 的 A4 旋转和透视变换。"""
    points = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    if not np.isfinite(points).all() or rotation not in ('clockwise', 'counterclockwise'):
        raise ValueError('无效的预览坐标或 A4 方向')
    x, y = points.T
    u, v = ((y / 292, 1 - x / 207) if rotation == 'clockwise'
            else (1 - y / 292, x / 207))
    rect = np.column_stack((u * (target_size[0] - 1), v * (target_size[1] - 1)))
    return cv2.perspectiveTransform(rect.reshape(-1, 1, 2), np.linalg.inv(homography)).reshape(-1, 2)


def render_plan(raw, homography, target_size, rotation, plan):
    """同一颜色连接源轮廓、目标轮廓和摆放顺序，另绘制最终结果缩略图。"""
    canvas = raw.copy()
    fill = raw.copy()
    projected = []
    inset = np.full((160, 272, 3), (22, 30, 42), np.uint8)
    targets = np.concatenate([np.asarray(m.target_vertices_a4_mm) for m in plan.moves])
    low, high = targets.min(axis=0), targets.max(axis=0)
    scale = min(240 / max(high[0] - low[0], 1), 136 / max(high[1] - low[1], 1))
    offset = (np.array([272, 160]) - (high - low) * scale) / 2
    legend = []
    for index, move in enumerate(plan.moves):
        color = COLORS[index % len(COLORS)]
        source = sheet_to_camera(move.observed_vertices_a4_mm, homography, target_size, rotation)
        target = sheet_to_camera(move.target_vertices_a4_mm, homography, target_size, rotation)
        target = np.rint(target).astype(np.int32)
        cv2.fillPoly(fill, [target], color)
        projected.append((move, source, target, color))
        local = np.rint((np.asarray(move.target_vertices_a4_mm) - low) * scale + offset).astype(np.int32)
        cv2.fillPoly(inset, [local], color)
        cv2.polylines(inset, [local], True, (250, 250, 250), 1, cv2.LINE_AA)
        center = tuple(np.rint(local.mean(axis=0)).astype(int))
        cv2.putText(inset, str(move.order), center, cv2.FONT_HERSHEY_SIMPLEX, .6, (0, 0, 0), 2)
        legend.append(dict(order=move.order, piece_id=move.piece_id,
            rotation=round(move.rotation_deg_clockwise, 1),
            color='#%02x%02x%02x' % tuple(reversed(color))))
    cv2.addWeighted(fill, .28, canvas, .72, 0, canvas)
    for move, source, target, color in projected:
        source = np.rint(source).astype(np.int32)
        cv2.polylines(canvas, [source], True, (15, 15, 15), 6, cv2.LINE_AA)
        cv2.polylines(canvas, [target], True, (15, 15, 15), 7, cv2.LINE_AA)
        cv2.polylines(canvas, [source], True, color, 3, cv2.LINE_AA)
        cv2.polylines(canvas, [target], True, color, 3, cv2.LINE_AA)
        endpoints = sheet_to_camera((move.pick_xy_a4_mm, move.place_xy_a4_mm), homography, target_size, rotation)
        start, end = [tuple(np.rint(p).astype(int)) for p in endpoints]
        cv2.arrowedLine(canvas, start, end, (15, 15, 15), 5, cv2.LINE_AA, tipLength=.08)
        cv2.arrowedLine(canvas, start, end, color, 2, cv2.LINE_AA, tipLength=.08)
        for point, prefix in ((start, 'S'), (end, 'T')):
            cv2.putText(canvas, prefix + str(move.order), point, cv2.FONT_HERSHEY_SIMPLEX, .65, (0, 0, 0), 4, cv2.LINE_AA)
            cv2.putText(canvas, prefix + str(move.order), point, cv2.FONT_HERSHEY_SIMPLEX, .65, color, 2, cv2.LINE_AA)
    return cv2.resize(canvas, (880, 495), interpolation=cv2.INTER_AREA), inset, legend


def ppm(image):
    h, w = image.shape[:2]
    return f'P6\n{w} {h}\n255\n'.encode() + cv2.cvtColor(image, cv2.COLOR_BGR2RGB).tobytes()


class PlanPreviewPublisher:
    """单任务令牌和原子状态文件防止上次结果冒充本次预测。"""
    def __init__(self, directory, token, rotation):
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', token):
            raise ValueError('预览任务令牌无效')
        self.directory, self.token, self.rotation = Path(directory), token, rotation
        self.snapshot = None
        self.last_live_at = 0.0
        self.directory.mkdir(parents=True, exist_ok=True)
        self._prune_previous()
        self.state('acquiring')

    def _prune_previous(self):
        # /run 是内存文件系统。只保留三个旧任务，且只删除本模块生成的文件。
        previous = []
        for path in self.directory.glob('*.json'):
            if path.stem == self.token or not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', path.stem):
                continue
            try:
                report = json.loads(path.read_text(encoding='utf-8'))
                if report.get('token') == path.stem and 'updated_at' in report:
                    previous.append((path.stat().st_mtime, path.stem))
            except (OSError, ValueError, AttributeError):
                continue
        for _, token in sorted(previous, reverse=True)[3:]:
            for suffix in ('.json', '.ppm', '-result.ppm', '.json.tmp', '.ppm.tmp', '-result.ppm.tmp'):
                (self.directory / (token + suffix)).unlink(missing_ok=True)

    @classmethod
    def from_environment(cls, rotation):
        directory, token = os.environ.get('EC_TASK_DISPLAY_DIR'), os.environ.get('EC_TASK_DISPLAY_ID')
        return cls(directory, token, rotation) if directory and token else None

    def _write(self, suffix, data):
        path = self.directory / (self.token + suffix)
        temporary = path.with_suffix(path.suffix + '.tmp')
        temporary.write_bytes(data)
        temporary.replace(path)

    def state(self, state, **extra):
        self._write('.json', json.dumps(dict(token=self.token, state=state,
            updated_at=time.time(), **extra), ensure_ascii=False).encode('utf-8'))

    def capture(self, frame, rect):
        self.snapshot = (frame.image_bgr.copy(), rect.homography.copy(), rect.target_size)
        self._write('.ppm', ppm(cv2.resize(frame.image_bgr, (880, 495))))
        self.state('solving', sequence=frame.sequence_id)

    def acquiring(self, frame):
        """Show fresh input immediately; it is not a stable snapshot or a plan."""
        now = time.monotonic()
        if now - self.last_live_at < .5:
            return
        self._write('.ppm', ppm(cv2.resize(frame.image_bgr, (880, 495))))
        self.state('acquiring', sequence=frame.sequence_id)
        self.last_live_at = now

    def publish(self, plan):
        if self.snapshot is None:
            raise RuntimeError('预测结果缺少本次稳定快照')
        image, inset, legend = render_plan(*self.snapshot, self.rotation, plan)
        self._write('.ppm', ppm(image))
        self._write('-result.ppm', ppm(inset))
        self.state('ready', legend=legend, width_mm=plan.target_width_mm,
                   height_mm=plan.target_height_mm, confidence=plan.confidence)

    def fail(self, error):
        self.state('failed', reason=str(error))


def display_call(display, method, *args):
    """显示故障不改变求解和运动权限；失败状态不会发布目标位置。"""
    if display is not None:
        try:
            getattr(display, method)(*args)
        except Exception:
            logger.exception('任务预览更新失败：%s', method)
