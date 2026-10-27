"""菜单预览复用正式任务的配置、工作区和 PL 检测器，不连接执行器。"""
from pathlib import Path
import json
import signal
import struct
import sys
import time
import threading

import cv2
import numpy as np

from ec_26_e.cam import FFmpegCamera, CameraTimeoutError
from ec_26_e.gui.controller import load_profile
from ec_26_e.gui.menu_model import MODES
from ec_26_e.gui.board_profile import load_menu_config, preview_backend, profile_paths
from ec_26_e.tasks.execution_lock import TaskExecutionLock, TaskBusyError
from ec_26_e.gui.camera_pipe import write_preview_error
from ec_26_e.vision import Resize
from ec_26_e.vision.factory import create_production_detector
from ec_26_e.gui.preview_tracking import PreviewContours
from ec_26_e.vision.runtime import prepare_vision_runtime


def annotate(raw, resizer, detector):
    """所有候选均编号显示，包括导致正式任务失败的额外区域。"""
    canvas = raw.copy()
    report = dict(paper_detected=False, piece_count=0, candidate_count=0,
                  valid=False, reason='')
    try:
        rect = resizer.resize(raw)
        report['paper_detected'] = True
        result = detector.detect(rect.image_bgr)
        contours = detector.last_preview_contours
        if result.pieces:
            # Use the subpixel polygons, not every stair-step of the binary mask.
            contours = [np.asarray(p.corners_px, np.float32).reshape(-1, 1, 2)
                        for p in result.pieces]
        report.update(piece_count=len(result.pieces), candidate_count=len(contours),
                      valid=bool(result.pieces), reason=detector.last_failure or '')
        cv2.polylines(canvas, [np.rint(rect.source_corners_px).astype(np.int32)],
                      True, (0, 220, 0), 2)
        report['workspace_source_px'] = np.asarray(rect.source_corners_px).tolist()
        report['homography'] = rect.homography.tolist()
        report['target_size'] = getattr(rect,'target_size',(rect.image_bgr.shape[1],rect.image_bgr.shape[0]))
        report['contours_source_px'] = []
        inverse = np.linalg.inv(rect.homography)
        for index, contour in enumerate(contours, 1):
            points = cv2.perspectiveTransform(contour.astype(np.float32), inverse)
            report['contours_source_px'].append(points.reshape(-1, 2).tolist())
            points = np.rint(points).astype(np.int32)
            cv2.polylines(canvas, [points], True, (0, 180, 255), 2)
            x, y, _, _ = cv2.boundingRect(points)
            cv2.putText(canvas, str(index), (x, max(20, y)),
                        cv2.FONT_HERSHEY_SIMPLEX, .8, (0, 0, 255), 2)
    except ValueError as error:
        report['reason'] = str(error)
    return canvas, report


def thumbnail(raw):
    return cv2.cvtColor(cv2.resize(raw, (160, 90), interpolation=cv2.INTER_LINEAR), cv2.COLOR_BGR2GRAY)


def overlay_is_current(current, analyzed, age):
    """只为未移动、足够新鲜的画面叠加轮廓；不把旧框当作实时检测。"""
    if age < 0 or age > 2.0 or current.shape != analyzed.shape:
        return False
    signed = current.astype(np.int16) - analyzed.astype(np.int16)
    offset = float(np.median(signed))
    if abs(offset) > 6:
        return False
    # Small uniform exposure changes do not move edges. Local changes still
    # invalidate the overlay immediately; the task's stability gate is separate.
    difference = np.abs(signed - offset)
    return float(np.mean(difference)) < 3.0 and float(np.mean(difference > 16)) < .01


class PreviewAnalysis:
    """识别线程只处理最新的一帧，无排队积压；独占一套 PL 检测器。"""
    def __init__(self, resizer, detector):
        self.resizer, self.detector = resizer, detector
        self.condition = threading.Condition()
        self.pending = self.result = self.error = None
        self.stopping = False
        self.frames = 0
        self.contours = PreviewContours()
        self.thread = threading.Thread(target=self._run, name='preview-analysis', daemon=True)
        self.thread.start()

    def submit(self, frame, small):
        with self.condition:
            self.pending = (frame, small, time.monotonic())
            self.condition.notify()

    def _run(self):
        try:
            while True:
                with self.condition:
                    self.condition.wait_for(lambda: self.stopping or self.pending is not None)
                    if self.stopping:
                        return
                    frame, small, timestamp = self.pending
                    self.pending = None
                _, report = annotate(frame.image_bgr, self.resizer, self.detector)
                report = self.contours.update(report)
                with self.condition:
                    self.frames += 1
                    self.result = (small, timestamp, report)
        except Exception as error:
            self.error = error  # PL timeout/protocol failures stop the helper.

    def close(self):
        with self.condition:
            self.stopping = True
            self.condition.notify()
        self.thread.join()


def draw_latest(raw, small, result, now):
    # Display interpolation only; all geometric detection still uses full input.
    image = cv2.resize(raw, (880, 495), interpolation=cv2.INTER_LINEAR)
    report = dict(candidate_count=0, valid=False, reason='识别更新中', overlay_current=False)
    if result is None:
        return image, report
    analyzed, timestamp, detection = result
    report.update(detection, analysis_age_seconds=now-timestamp)
    report['overlay_current'] = overlay_is_current(small, analyzed, now-timestamp)
    if not report['overlay_current']:
        report.update(valid=False, candidate_count=0, reason='画面变化，正在更新轮廓')
        return image, report
    scale = np.array([880/raw.shape[1], 495/raw.shape[0]])
    for index, points in enumerate(report.get('contours_source_px', []), 1):
        points = np.rint(np.asarray(points) * scale).astype(np.int32)
        cv2.polylines(image, [points], True, (0, 180, 255), 2, cv2.LINE_AA)
        x, y = points.min(axis=0)
        cv2.putText(image, str(index), (int(x), max(20, int(y))), cv2.FONT_HERSHEY_SIMPLEX, .6, (0, 255, 255), 2)
    if report.get('workspace_source_px'):
        points = np.rint(np.asarray(report['workspace_source_px']) * scale).astype(np.int32)
        cv2.polylines(image, [points], True, (0, 200, 0), 1, cv2.LINE_AA)
    return image, report


def preview_session(label, running):
    """一次预览独占硬件；结束后释放设备，保留软件依赖。"""
    requested_at = time.monotonic()
    board = load_menu_config()
    config = load_profile(*profile_paths(board), label, preview_backend(board))
    lock = TaskExecutionLock()
    lock.acquire()
    camera = FFmpegCamera(config.camera)
    try:
        with create_production_detector(config.detect1) as owner:
            resizer = Resize(config.resize)
            camera.start()
            camera_started_seconds = time.monotonic() - requested_at
            analysis = PreviewAnalysis(resizer, owner.detector)
            sequence = None
            frames = 0
            started = last_status = time.monotonic()
            last_frame = started
            try:
                while running():
                    if analysis.error is not None:
                        raise analysis.error
                    try:
                        frame = camera.read_latest(after_sequence=sequence, timeout=.5)
                    except CameraTimeoutError:
                        if time.monotonic() - last_frame > 3:
                            raise RuntimeError('摄像头连续 3 秒没有新帧，请检查 USB')
                        continue
                    now = time.monotonic()
                    last_frame = now
                    sequence = frame.sequence_id
                    small = thumbnail(frame.image_bgr)
                    analysis.submit(frame, small)
                    with analysis.condition:
                        result, count = analysis.result, analysis.frames
                    image, report = draw_latest(frame.image_bgr, small, result, now)
                    if MODES[label][0] == 'tangram' and report.get('overlay_current') and 'homography' in report:
                        from types import SimpleNamespace
                        from ec_26_e.tasks.tangram import annotate_regions
                        rect = SimpleNamespace(homography=np.array(report['homography']),target_size=report['target_size'])
                        image = annotate_regions(image,rect,config.base.options.landscape_rotation,config.planning,
                            (880/frame.image_bgr.shape[1],495/frame.image_bgr.shape[0]))
                    frames += 1
                    elapsed = max(.001, now-started)
                    report.update(frames=frames, analysis_frames=count, display_fps=frames/elapsed,
                                  analysis_fps=count/elapsed, sequence=sequence, updated_at_unix=time.time(),
                                  camera_started_seconds=camera_started_seconds)
                    if now - last_status >= .5:
                        status = Path('/run/ec26e/preview-status.json')
                        status.parent.mkdir(parents=True, exist_ok=True)
                        temporary = status.with_suffix('.tmp')
                        public_report = {k:v for k,v in report.items() if k not in ('contours_source_px','workspace_source_px')}
                        temporary.write_text(json.dumps(public_report, ensure_ascii=False), encoding='utf-8')
                        temporary.replace(status)
                        last_status = now
                    text = 'LIVE {:.1f} fps | contours {:.1f} fps | {}'.format(
                        report['display_fps'], report['analysis_fps'],
                        '{} candidates'.format(report['candidate_count']) if report['overlay_current'] else 'updating')
                    cv2.rectangle(image, (0, 0), (880, 26), (20, 20, 20), -1)
                    cv2.putText(image, text, (8, 18), cv2.FONT_HERSHEY_SIMPLEX, .5, (255, 255, 255), 1)
                    data = b'P6\n880 495\n255\n' + cv2.cvtColor(image, cv2.COLOR_BGR2RGB).tobytes()
                    sys.stdout.buffer.write(struct.pack('!I', len(data)) + data)
                    sys.stdout.buffer.flush()
                    # Cap display work, keeping CPU available for recognition/solver.
                    time.sleep(max(0, .05 - (time.monotonic()-now)))
            finally:
                analysis.close()
    except BrokenPipeError:
        pass
    finally:
        camera.close()
        lock.release()


def main():
    prepare_vision_runtime()
    if '--wait-for-start' in sys.argv:
        from ec_26_e.gui.preview_server import serve
        # The control thread can still be waiting when SIGTERM arrives. Raw
        # stdin avoids a BufferedReader lock during interpreter finalization.
        serve(preview_session, MODES, sys.stdin.buffer.raw, sys.stdout.buffer)
    else:
        stopped = threading.Event()
        signal.signal(signal.SIGTERM, lambda *_: stopped.set())
        label = sys.argv[1] if len(sys.argv) > 1 else next(iter(MODES))
        preview_session(label, lambda: not stopped.is_set())


def run():
    try:
        main()
        return 0
    except TaskBusyError:
        write_preview_error(sys.stdout.buffer,
            '设备正由其他任务或维护占用，请稍后重新打开预览', 'busy')
        return 2
    except Exception as error:
        import traceback
        traceback.print_exc()
        try:
            write_preview_error(sys.stdout.buffer, f'预览失败：{error}')
        except BrokenPipeError:
            pass
        return 1


if __name__ == '__main__':
    sys.exit(run())
