"""验证模式、共享 DMA、预览关闭和运动启动的互斥。"""
from pathlib import Path
import threading
import time
from unittest.mock import Mock, patch

import numpy as np
import pytest

from ec_26_e.gui.controller import Controller, MODES, CameraLease, SharedPreprocessor, TaskPreviewObserver, load_profile

ENV = Path('../deploy/axu5ev/board.env.example')
CONFIGS = Path('../deploy/axu5ev/tasks')


def test_all_modes_override_piece_count_and_use_correct_task():
    assert tuple(MODES) == ('固定四片', '随机拼图', '七巧板')
    for label,(mode,count) in MODES.items():
        config = load_profile(ENV,CONFIGS,label,'cpu')
        if mode == 'tangram':
            assert config.planning.pattern_id == 'square'
        else:
            assert config.options.planning_options.mode == mode
        assert config.detect1.expected_pieces == count
        assert config.detect1.preprocess_backend == 'cpu'


def test_auto_count_environment_is_random_only():
    from ec_26_e.config import load_task1_config, load_task2_config
    environment = {'EC_EXPECTED_PIECES': 'auto'}
    config = load_task2_config(CONFIGS / 'random.json', env_path=ENV, environ=environment)
    assert config.detect1.expected_pieces is None
    with pytest.raises(ValueError, match='fixed 模式不能自动'):
        load_task1_config(CONFIGS / 'fixed.json', env_path=ENV, environ=environment)


def test_preview_and_task_cannot_overlap_dma_or_reuse_ids():
    seen = []
    active = 0
    def process(image,frame_id):
        nonlocal active
        active += 1
        assert active == 1
        seen.append(frame_id)
        time.sleep(0.002)
        active -= 1
        return image
    backend = Mock(process=process)
    shared = SharedPreprocessor(backend)
    threads = [threading.Thread(target=lambda: [shared.process(None,1) for _ in range(5)]) for _ in range(2)]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    assert seen == list(range(1,11))
    shared.close()
    backend.close.assert_not_called()


def test_camera_lease_does_not_stop_live_preview():
    camera = Mock()
    lease = CameraLease(camera)
    lease.start()
    lease.read_latest(20,0.3)
    lease.close()
    camera.read_latest.assert_called_once_with(after_sequence=20,timeout=0.3)
    camera.start.assert_not_called()
    camera.close.assert_not_called()


def test_demo_preview_has_contours_and_never_starts_motor():
    controller = Controller(ENV,CONFIGS,'cpu',Path('../baselines/upstream/fixed4.png'),motion_enabled=True)
    with patch('ec_26_e.gui.controller.FFmpegCamera') as camera:
        controller.start()
        try:
            deadline = time.monotonic()+5
            while controller.snapshot()[0] == 0 and time.monotonic()<deadline:
                time.sleep(0.01)
            version,image,_,detail,ready,_ = controller.snapshot()
            assert ready and version > 0
            assert '4 片' in detail
            assert np.any((image[:,:,0] == 70) & (image[:,:,1] == 240) & (image[:,:,2] == 90))
            with pytest.raises(RuntimeError): controller.run()
            assert controller.request_close()
        finally:
            controller.close()
        camera.assert_not_called()
    assert not controller.worker.is_alive()


def test_busy_task_blocks_mode_change_close_and_duplicate_start():
    controller = Controller(ENV,CONFIGS,'cpu',motion_enabled=True)
    controller.ready = controller.busy = True
    with pytest.raises(RuntimeError): controller.select('随机拼图')
    with pytest.raises(RuntimeError): controller.run()
    assert not controller.request_close()
    assert not controller.stop.is_set()


def test_preview_only_blocks_motor_start():
    controller = Controller(ENV,CONFIGS,'cpu')
    controller.ready = True
    with pytest.raises(RuntimeError): controller.run()


@pytest.mark.parametrize('label',['固定四片','随机拼图'])
def test_selected_task_uses_live_camera_and_shared_pl_without_reloading(label):
    controller = Controller(ENV,CONFIGS,'cpu',motion_enabled=True)
    controller.ready = True
    controller.camera = Mock()
    backend = Mock()
    controller.shared = SharedPreprocessor(backend)
    config = load_profile(ENV,CONFIGS,label,'cpu')
    observed = []
    def execute(params,context,**factories):
        lease = factories['camera_factory'](params.camera)
        lease.close()
        detector = factories['detector_factory'](params.detect1)
        assert detector.detector.preprocessor.shared is controller.shared
        assert detector.detector.expected_pieces == params.detect1.expected_pieces
        detector.close()
        plan = factories['planner']((),params.options.planning_options)
        actuator = factories['actuator_factory'](params)
        actuator.execute(plan)
        observed.append(params.options.planning_options.mode)
    with patch('ec_26_e.tasks.task1.application.execute_task1',side_effect=execute) as fixed, \
         patch('ec_26_e.tasks.task2.application.execute_task2',side_effect=execute) as random, \
         patch('ec_26_e.gui.controller.plan_sheet_moves',return_value='validated-plan'), \
         patch('ec_26_e.tasks.task1.actuation.MovePlanActuator') as actuator:
        controller._run_task(config)
        assert '任务完成' in controller.status
        assert observed == [config.options.planning_options.mode]
        assert fixed.call_count + random.call_count == 1
        actuator.return_value.execute.assert_called_once_with('validated-plan')
    backend.close.assert_not_called()
    controller.camera.close.assert_not_called()


def test_preview_failure_before_actuation_blocks_motor_constructor():
    controller = Controller(ENV,CONFIGS,'cpu',motion_enabled=True)
    controller.ready = True
    config = load_profile(ENV,CONFIGS,'固定四片','cpu')
    def execute(params,context,**factories):
        controller.ready = False
        factories['actuator_factory'](params)
    with patch('ec_26_e.tasks.task1.application.execute_task1',side_effect=execute), \
         patch('ec_26_e.tasks.task1.actuation.MovePlanActuator') as actuator:
        controller._run_task(config)
    actuator.assert_not_called()
    assert '禁止执行' in controller.status


def test_task_observer_publishes_frames_without_duplicate_preview_and_resumes():
    from types import SimpleNamespace
    controller = Controller(ENV,CONFIGS,'cpu',Path('../baselines/upstream/fixed4.png'))
    observer = TaskPreviewObserver(controller)
    with patch.object(controller, '_render_preview', wraps=controller._render_preview) as render:
        controller.start()
        try:
            deadline = time.monotonic() + 5
            while controller.version == 0 and time.monotonic() < deadline:
                time.sleep(0.01)
            assert controller.ready
            observer.start()
            count = render.call_count
            canvas = np.zeros((540,762,3), np.uint8)
            frame = SimpleNamespace(image_bgr=canvas, sequence_id=42)
            with patch('ec_26_e.gui.controller.draw_live_detection', return_value=canvas) as draw:
                observer.show_detection(frame, Mock(), SimpleNamespace(pieces=(1,2,3,4)))
                assert controller.snapshot()[1] is canvas
                draw.assert_called_once()
            time.sleep(0.15)
            assert render.call_count == count
            observer.close()
            deadline = time.monotonic() + 5
            while render.call_count == count and time.monotonic() < deadline:
                time.sleep(0.01)
            assert render.call_count > count
        finally:
            observer.close()
            controller.close()
