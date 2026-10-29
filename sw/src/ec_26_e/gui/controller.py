"""界面线程之外的采集、PL 共享和任务调度。"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import logging
import threading
import time

import cv2
import numpy as np

from ec_26_e.cam import FFmpegCamera, CameraTimeoutError
from ec_26_e.config import load_task1_config, load_task2_config
from ec_26_e.control import plan_sheet_moves
from ec_26_e.draw import draw_live_detection, draw_detection_failure
from ec_26_e.tasks import TaskContext
from ec_26_e.tasks.execution_lock import TaskExecutionLock
from ec_26_e.vision import Resize
from ec_26_e.vision.factory import create_production_detector, preprocess_config
from ec_26_e.vision.traditional import TraditionalVisionDetector

logger = logging.getLogger(__name__)
from ec_26_e.gui.menu_model import MODES


class SharedPreprocessor:
    """预览和稳定检测共用一个 PL 核；串行 DMA、全局递增帧号。"""
    def __init__(self, backend):
        self.backend = backend
        self.lock = threading.Lock()
        self.sequence = 0

    def process(self, image, frame_id, config=None):
        with self.lock:
            self.sequence += 1
            if config is not None:
                self.backend.config = config
            return self.backend.process(image, self.sequence)

    def for_config(self, config):
        return ConfiguredPreprocessorLease(self, preprocess_config(config))

    def close(self):
        # 租用者结束不关闭实际设备，由 Controller 统一释放。
        pass


class ConfiguredPreprocessorLease:
    """每个调用方独立持有模式参数，写寄存器与提交帧在同一把锁中。"""
    def __init__(self, shared, config):
        self.shared, self.config = shared, config

    def process(self, image, frame_id):
        return self.shared.process(image, frame_id, self.config)

    def close(self):
        pass


class CameraLease:
    def __init__(self, camera):
        self.camera = camera

    def start(self):
        pass

    def close(self):
        pass

    def read_latest(self, after_sequence=None, timeout=None):
        return self.camera.read_latest(after_sequence=after_sequence, timeout=timeout)


class TaskPreviewObserver:
    """把任务已算出的帧送到界面，采集时不再并行重复视觉处理。"""
    def __init__(self, controller):
        self.controller = controller

    def start(self):
        # 等当前预览帧结束，之后只有任务使用视觉链路。
        with self.controller._vision_lock:
            self.controller._task_observing.set()

    def show_detection(self, frame, resize_result, detect_result):
        canvas = draw_live_detection(frame.image_bgr, resize_result, detect_result,
                                     sequence_id=frame.sequence_id)
        self.controller._publish(canvas, f"已识别 {len(detect_result.pieces)} 片 · 正在确认稳定轮廓")
        return False

    def show_failure(self, frame, *, stage, resize_result=None):
        detector = self.controller._task_detector
        canvas = draw_detection_failure(frame.image_bgr, sequence_id=frame.sequence_id,
                                        stage=stage, resize_result=resize_result)
        if resize_result is not None:
            inverse = np.linalg.inv(resize_result.homography)
            for contour in getattr(getattr(detector, 'detector', None), 'last_preview_contours', ()):
                source = cv2.perspectiveTransform(contour.astype(np.float32), inverse)
                cv2.polylines(canvas, [np.rint(source).astype(np.int32)], True, (0,180,255), 2)
        detail = getattr(getattr(detector, 'detector', detector), 'last_failure', None)
        self.controller._publish(canvas, detail or "正在确认工作区和纸片，请保持分开放置")
        return False

    def close(self):
        self.controller._task_observing.clear()


def load_profile(env_path, config_dir, label, backend):
    mode, count = MODES[label]
    if mode == 'tangram':
        from ec_26_e.config.tangram import load_tangram_config
        return load_tangram_config(config_dir / 'tangram.json',env_path=env_path)
    loader = load_task1_config if mode == "fixed" else load_task2_config
    config = loader(config_dir / f"{mode}.json", env_path=env_path)
    return replace(config, detect1=replace(config.detect1, expected_pieces=count,
                   preprocess_backend=backend))


class Controller:
    def __init__(self, env_path: Path, config_dir: Path, backend="pl", demo: Path | None = None, *, motion_enabled=False):
        self.env_path, self.config_dir, self.backend_name, self.demo = env_path, config_dir, backend, demo
        self.motion_enabled = motion_enabled
        self.lock = threading.RLock()
        self._vision_lock = threading.Lock()
        self._task_observing = threading.Event()
        self._task_detector = None
        self.stop = threading.Event()
        self.label = next(iter(MODES))
        self.busy = self.ready = False
        self.status = "正在初始化相机和图像处理器…"
        self.detail = ""
        self.image = None
        self.version = 0
        self.task = None
        self.camera = self.shared = None
        self.worker = threading.Thread(target=self._preview, name="puzzle-preview")

    def start(self):
        self.worker.start()

    def select(self, label):
        if label not in MODES:
            raise ValueError("未知模式")
        with self.lock:
            if self.busy:
                raise RuntimeError("任务运行时不能切换模式")
            self.label = label

    def snapshot(self):
        with self.lock:
            return self.version, self.image, self.status, self.detail, self.ready, self.busy

    def _status(self, text):
        with self.lock:
            self.status = text

    def _publish(self, canvas, detail):
        with self.lock:
            self.image = canvas
            self.version += 1
            self.detail = detail

    def _render_preview(self, raw, resizer, detector, sequence):
        try:
            if self.demo:
                canvas = raw.copy()
                result = detector.detect(raw)
                if not result.pieces:
                    cv2.drawContours(canvas, detector.last_preview_contours, -1, (0,180,255), 2)
                for piece in result.pieces:
                    polygon = np.rint(piece.corners_px).astype(np.int32)
                    cv2.polylines(canvas, [polygon], True, (70,240,90), 2)
                    for point in polygon:
                        cv2.circle(canvas, tuple(point), 4, (0,180,255), -1)
            else:
                rectified = resizer.resize(raw)
                result = detector.detect(rectified.image_bgr)
                canvas = draw_live_detection(raw, rectified, result, sequence_id=sequence)
                if not result.pieces:
                    inverse = np.linalg.inv(rectified.homography)
                    for contour in detector.last_preview_contours:
                        source = cv2.perspectiveTransform(contour.astype(np.float32), inverse)
                        cv2.polylines(canvas, [np.rint(source).astype(np.int32)], True, (0,180,255), 2)
            detail = detector.last_failure or f"已识别 {len(result.pieces)} 片 · 轮廓已更新"
            return canvas, detail
        except ValueError as error:
            return raw.copy(), f"工作区或轮廓无效：{error}"

    def _preview(self):
        execution_lock = None
        owner = None
        try:
            config = load_profile(self.env_path, self.config_dir, self.label, self.backend_name)
            if self.demo is None:
                execution_lock = TaskExecutionLock()
                execution_lock.acquire()
                self.camera = FFmpegCamera(config.camera)
                self.camera.start()
            else:
                raw = cv2.imread(str(self.demo))
                if raw is None:
                    raise ValueError("无法读取演示图像")
            owner = create_production_detector(config.detect1)
            self.shared = SharedPreprocessor(owner.detector.preprocessor)
            sequence, previous_label = None, None
            with self.lock:
                self.ready = True
                self.status = ("演示模式 · 不连接电机" if self.demo else
                    "就绪 · 选择模式后开始拼图" if self.motion_enabled else "实时预览 · 运动未启用")
            while not self.stop.is_set():
                if self._task_observing.is_set():
                    self.stop.wait(0.05)
                    continue
                started = time.perf_counter()
                with self.lock:
                    label = self.label
                if label != previous_label:
                    config = load_profile(self.env_path, self.config_dir, label, self.backend_name)
                    if MODES[label][0] == 'tangram':
                        from ec_26_e.vision.tangram import TangramPreprocessor
                        lease = TangramPreprocessor()
                    else:
                        lease = self.shared.for_config(config.detect1)
                    detector = TraditionalVisionDetector(config.detect1,
                        expected_pieces=config.detect1.expected_pieces,
                        preprocessor=lease)
                    resizer = Resize(config.resize)
                    previous_label = label
                try:
                    if self.demo is None:
                        frame = self.camera.read_latest(after_sequence=sequence, timeout=0.5)
                        sequence = frame.sequence_id
                        raw = frame.image_bgr
                    with self._vision_lock:
                        if self._task_observing.is_set():
                            continue
                        canvas, detail = self._render_preview(raw, resizer, detector, sequence)
                        elapsed = (time.perf_counter()-started)*1000
                        self._publish(canvas, f"{detail}  |  {self.backend_name.upper()}  |  本帧 {elapsed:.0f} ms")
                except CameraTimeoutError:
                    with self.lock:
                        self.image = None
                        self.version += 1
                        self.detail = "相机暂无新帧，等待恢复"
                self.stop.wait(max(0.0, 0.1-(time.perf_counter()-started)))
        except Exception as error:
            logger.exception("预览失败")
            with self.lock:
                self.ready = False
                self.image = None
                self.version += 1
                self.status = f"预览停止：{error}"
        finally:
            with self.lock:
                self.ready = False
            # 后端故障时也等待正在执行的任务退出，避免中途关闭共用设备。
            if self.task is not None:
                self.task.join()
            try:
                if owner is not None:
                    owner.close()
            finally:
                try:
                    if self.camera is not None:
                        self.camera.close()
                finally:
                    if execution_lock is not None:
                        execution_lock.release()

    def run(self):
        with self.lock:
            if self.busy or not self.ready or not self.motion_enabled or self.demo is not None or self.stop.is_set():
                raise RuntimeError("当前状态不能启动真实任务")
            config = load_profile(self.env_path, self.config_dir, self.label, self.backend_name)
            if MODES[self.label][0] == 'tangram' and not config.motion_verified:
                raise RuntimeError('七巧板尚未完成专用视觉/运动标定，请在板载菜单使用只规划功能')
            self.busy = True
            self.status = "正在确认连续稳定轮廓…"
            self.task = threading.Thread(target=self._run_task, args=(config,), name="puzzle-task")
            self.task.start()

    def _run_task(self, config):
        from ec_26_e.tasks.task1.application import execute_task1
        from ec_26_e.tasks.task2.application import execute_task2
        from ec_26_e.tasks.task1.actuation import MovePlanActuator
        from ec_26_e.vision.stabilizer import StableVisionDetector, StableVisionConfig
        def detector_factory(params):
            self._task_detector = StableVisionDetector(TraditionalVisionDetector(params,
                expected_pieces=params.expected_pieces, preprocessor=self.shared.for_config(params)),
                StableVisionConfig(window_size=6, required_frames=6))
            return self._task_detector
        def planner(pieces, options):
            self._status("轮廓稳定，正在求解…")
            return plan_sheet_moves(pieces, options)
        def actuator_factory(params):
            # 预览/PL 故障时，不允许新启动机械动作。
            with self.lock:
                if not self.ready:
                    raise RuntimeError("视觉后端已失效，禁止执行")
            self._status("正在拾放与拼接 · 请等待本次动作完成")
            return MovePlanActuator(params.actuation)
        try:
            from ec_26_e.config.tangram import TangramConfig
            if isinstance(config,TangramConfig):
                from ec_26_e.tasks.tangram import run_tangram
                with self._vision_lock:
                    self._task_observing.set()
                run_tangram(config,execute_motion=True,camera_factory=lambda _:CameraLease(self.camera))
                self._status('七巧板任务完成')
                return
            execute = execute_task1 if config.options.planning_options.mode == "fixed" else execute_task2
            execute(config, TaskContext(draw_enabled=True), observer_factory=lambda: TaskPreviewObserver(self),
                camera_factory=lambda _: CameraLease(self.camera),
                detector_factory=detector_factory, planner=planner, actuator_factory=actuator_factory)
            self._status("本次任务完成 · 可重新摆放纸片")
        except Exception as error:
            logger.exception("拼图任务失败")
            self._status(f"任务失败：{error}")
        finally:
            self._task_observing.clear()
            self._task_detector = None
            with self.lock:
                self.busy = False

    def request_close(self):
        with self.lock:
            if self.busy:
                return False
            self.ready = False
            self.stop.set()
            return True

    def close(self):
        """界面异常退出也等待正在执行的动作，之后释放共用设备。"""
        with self.lock:
            self.stop.set()
        if self.worker.ident is not None:
            self.worker.join()
