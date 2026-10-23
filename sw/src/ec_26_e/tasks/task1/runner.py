"""编排相机、视觉稳定审查和纯控制规划的 Task1 入口。"""

from __future__ import annotations

import logging
import json
import os
import time

from ec_26_e.cam import (
    CameraFrame,
    CameraSource,
    CameraTimeoutError,
)
from ec_26_e.control import MovePlan, PuzzlePiece, plan_sheet_moves
from ec_26_e.tasks.task1.contracts import (
    Detect1Processor,
    PuzzlePlanner,
    ResizeProcessor,
    Task1FrameObserver,
    Task1Error,
    Task1Options,
    Task1TimeoutError,
)
from ec_26_e.tasks.contracts import TaskCancelledError
from ec_26_e.tasks.progress import report_stage
from ec_26_e.tasks.task1.review import ReviewedFrame, review_visual_frame
from ec_26_e.tasks.task1.stability import StableSceneReviewer
from ec_26_e.vision import Detect1Result

logger = logging.getLogger(__name__)


def _validate_dependencies(
    camera: CameraSource,
    resizer: ResizeProcessor,
    detector: Detect1Processor,
    planner: PuzzlePlanner,
    observer: Task1FrameObserver | None,
) -> None:
    """在访问硬件前确认注入对象提供 Task1 所需最小接口。"""

    for name, dependency, method_names in (
        ("camera", camera, ("start", "read_latest", "close")),
        ("resizer", resizer, ("resize",)),
        ("detector", detector, ("detect",)),
    ):
        for method_name in method_names:
            if not callable(getattr(dependency, method_name, None)):
                raise TypeError(
                    f"{name} 必须提供可调用的 {method_name} 方法"
                )
    if not callable(planner):
        raise TypeError("planner 必须是可调用对象")
    if observer is not None:
        for method_name in (
            "start",
            "show_detection",
            "show_failure",
            "close",
        ):
            if not callable(getattr(observer, method_name, None)):
                raise TypeError(
                    f"observer 必须提供可调用的 {method_name} 方法"
                )


def _format_timeout(
    options: Task1Options,
    processed_frames: int,
    invalid_events: int,
    reviewer: StableSceneReviewer,
    last_reason: str | None,
    task_label: str,
) -> Task1TimeoutError:
    """创建包含稳定审查现场信息的任务超时异常。"""

    reason = (
        last_reason
        or "尚未形成满足数量与持续时间的稳定碎片集合"
    )
    return Task1TimeoutError(
        f"{task_label} 在 "
        f"{options.acquisition_timeout_seconds:.3f} 秒内未获得稳定结果；"
        f"已处理帧={processed_frames}，"
        f"异常事件={invalid_events}，"
        f"最后原因={reason}；"
        f"{reviewer.diagnostics_text()}"
    )


def _acquire_stable_pieces(camera, resizer, detector, options, observer, task_label, metrics=None, display=None):
    """只返回当前连续稳定场景；超时或失效不回退到历史帧。"""
    reviewer = StableSceneReviewer(options)
    deadline = time.monotonic() + options.acquisition_timeout_seconds
    after_sequence = None
    processed_frames = invalid_events = 0
    last_reason = None
    metrics = metrics if metrics is not None else {}
    logger.info("正在观测纸片：稳定审查周期 %.1f 秒，采集期限 %.1f 秒",
                options.review_cycle_seconds, options.acquisition_timeout_seconds)
    while time.monotonic() < deadline:
        frame = resize_result = None
        try:
            phase = time.perf_counter()
            frame = camera.read_latest(after_sequence=after_sequence,
                timeout=min(options.frame_read_timeout_seconds, max(0.001, deadline-time.monotonic())))
            metrics['capture_seconds'] = metrics.get('capture_seconds', 0.0) + time.perf_counter() - phase
            if not isinstance(frame, CameraFrame):
                raise Task1Error("camera.read_latest 必须返回 CameraFrame")
            if after_sequence is not None and frame.sequence_id <= after_sequence:
                raise Task1Error("相机必须返回序号严格递增的新帧")
            after_sequence = frame.sequence_id
            if display is not None:
                from ec_26_e.gui.plan_preview import display_call
                display_call(display, 'acquiring', frame)
            processed_frames += 1
            logger.info("正在观测纸片：处理第 %d 帧（相机序号 %d）",
                        processed_frames, frame.sequence_id)
            phase = time.perf_counter()
            resize_result = resizer.resize(frame.image_bgr)
            metrics['resize_seconds'] = metrics.get('resize_seconds', 0.0) + time.perf_counter() - phase
            phase = time.perf_counter()
            result = detector.detect(resize_result.image_bgr)
            metrics['detect_seconds'] = metrics.get('detect_seconds', 0.0) + time.perf_counter() - phase
            single_detector = getattr(detector, 'detector', detector)
            diagnostics = getattr(single_detector, 'last_diagnostics', None)
            metrics['preprocess_seconds'] = metrics.get('preprocess_seconds', 0.0) + (
                getattr(diagnostics, 'stage_ms', {}).get('preprocess', 0.0) / 1000)
            # The continuous reviewer itself validates six fresh single-frame geometries.
            # Do not stack another six-frame warm-up ahead of that same gate.
            if options.continuous_review:
                result = getattr(detector, 'last_single_result', None) or result
            if not result.pieces:
                detail = getattr(getattr(detector, "detector", detector), "last_failure", None)
                raise ValueError(detail or "纸片尚未稳定，请保持分开放置")
            if options.planning_options.mode == "fixed" and len(result.pieces) != 4:
                raise ValueError("固定模式必须恰好四片")
            reviewed = review_visual_frame(frame, resize_result, result, options.landscape_rotation)
            phase = time.perf_counter()
            cycle = reviewer.add_frame(reviewed)
            metrics['review_seconds'] = metrics.get('review_seconds', 0.0) + time.perf_counter() - phase
            if observer is not None and observer.show_detection(frame, resize_result, result):
                raise TaskCancelledError("用户通过预览窗口中止任务")
            if time.monotonic() >= deadline:
                break
            if cycle is not None and cycle.accepted:
                if display is not None:
                    from ec_26_e.gui.plan_preview import display_call
                    display_call(display, 'capture', frame, resize_result)
                logger.info("稳定审查通过：%d 片纸片，已处理 %d 帧",
                            len(cycle.pieces), processed_frames)
                metrics.update(processed_frames=processed_frames, invalid_frames=invalid_events)
                preprocessor = getattr(single_detector, 'preprocessor', None)
                metrics['vision_pl'] = {key: getattr(preprocessor, key) for key in
                    ('frame_count', 'pack_seconds', 'dma_seconds', 'copy_seconds',
                     'calibration_seconds', 'last_blue_min')
                    if hasattr(preprocessor, key)}
                return cycle.pieces
            last_reason = "等待完整连续稳定周期" if cycle is None else cycle.diagnostics.to_text()
            logger.info("正在观测纸片：识别到 %d 片；%s", len(result.pieces), last_reason)
        except (CameraTimeoutError, ValueError) as error:
            invalid_events += 1
            last_reason = str(error)
            logger.info("正在观测纸片：第 %d 帧尚未通过，原因：%s",
                        processed_frames, last_reason)
            reviewer = StableSceneReviewer(options)
            # 预热空结果保留单帧历史；采集或工作区失败则清空。
            if resize_result is None and callable(getattr(detector, "reset", None)):
                detector.reset()
            if observer is not None and frame is not None and observer.show_failure(
                frame, stage="workspace" if resize_result is None else "detect1", resize_result=resize_result):
                raise TaskCancelledError("用户通过预览窗口中止任务")
            if isinstance(error, CameraTimeoutError):
                time.sleep(min(0.005, max(0.0, deadline-time.monotonic())))
    raise _format_timeout(options, processed_frames, invalid_events, reviewer, last_reason, task_label)


def run_puzzle_task(
    camera: CameraSource,
    resizer: ResizeProcessor,
    detector: Detect1Processor,
    *,
    options: Task1Options = Task1Options(),
    planner: PuzzlePlanner = plan_sheet_moves,
    observer: Task1FrameObserver | None = None,
    task_label: str = "Task1",
) -> MovePlan:
    """运行一次共享拼图任务，并在释放相机后同步返回纯控制移动方案。

    本入口不读取配置文件，也不访问相机之外的硬件。菜单显式提供
    显示令牌时发布本次快照和预测图，不将显示结果用于执行。
    视觉场景错误会在采集期限内重试，
    控制规划错误原样向上传播。observer 为 ``None`` 时不会触发
    任何图像复制、绘制或窗口操作。
    """

    if not isinstance(options, Task1Options):
        raise TypeError("options 必须是 Task1Options 的任务选项")
    if not isinstance(task_label, str) or not task_label.strip():
        raise ValueError("task_label 必须是非空字符串")
    _validate_dependencies(
        camera,
        resizer,
        detector,
        planner,
        observer,
    )
    started = time.perf_counter()
    metrics = {'mode': options.planning_options.mode}
    display = None
    if os.environ.get('EC_TASK_DISPLAY_ID'):
        from ec_26_e.gui.plan_preview import PlanPreviewPublisher, display_call
        display = PlanPreviewPublisher.from_environment(options.landscape_rotation)

    try:
        if observer is not None:
            observer.start()
        phase = time.perf_counter()
        camera.start()
        metrics['camera_start_seconds'] = time.perf_counter() - phase
        logger.info("摄像头已启动，正在采集新画面")
        pieces = _acquire_stable_pieces(
            camera,
            resizer,
            detector,
            options,
            observer,
            task_label,
            metrics,
            display,
        )
    except BaseException as error:
        if display is not None:
            display_call(display, 'fail', error)
        metrics.update(success=False, stage='acquisition', error=str(error),
                       task_seconds=time.perf_counter()-started)
        logger.info('TASK_PERFORMANCE %s', json.dumps(metrics, sort_keys=True))
        raise
    finally:
        try:
            phase = time.perf_counter()
            camera.close()
            metrics['camera_close_seconds'] = time.perf_counter() - phase
        finally:
            if observer is not None:
                observer.close()

    with report_stage("正在求解拼图并计算运动规划"):
        phase = time.perf_counter()
        try:
            plan = planner(pieces, options.planning_options)
            if display is not None:
                display_call(display, 'publish', plan)
            metrics['success'] = True
            return plan
        except BaseException as error:
            if display is not None:
                display_call(display, 'fail', error)
            raise
        finally:
            metrics['solve_seconds'] = time.perf_counter() - phase
            metrics['task_seconds'] = time.perf_counter() - started
            launched = os.environ.get('EC_TASK_LAUNCHED_MONOTONIC')
            if launched:
                metrics['click_to_plan_seconds'] = time.monotonic() - float(launched)
            logger.info('TASK_PERFORMANCE %s', json.dumps(metrics, sort_keys=True))


def run_task1(
    camera: CameraSource,
    resizer: ResizeProcessor,
    detector: Detect1Processor,
    *,
    options: Task1Options = Task1Options(),
    planner: PuzzlePlanner = plan_sheet_moves,
    observer: Task1FrameObserver | None = None,
) -> MovePlan:
    """运行一次 Task1，并在释放相机后同步返回纯控制移动方案。"""

    return run_puzzle_task(
        camera,
        resizer,
        detector,
        options=options,
        planner=planner,
        observer=observer,
        task_label="Task1",
    )
