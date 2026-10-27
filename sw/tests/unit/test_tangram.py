"""七巧板完整软件链路：合成白片蓝底、真实分割及无硬件执行验证。"""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import math

import cv2
import numpy as np
import pytest
from puzzle_solver import PATTERN_NAMES, PlanningError
from ec_26_e.cam import CameraFrame
from ec_26_e.config.tangram import load_tangram_config
from ec_26_e.gui.board_menu import BoardMenu
from ec_26_e.gui.menu_model import MODES,MenuModel
from ec_26_e.tasks.tangram import run_tangram, TangramStability
from ec_26_e.vision.resize.contracts import ResizeResult
from ec_26_e.vision.traditional import TraditionalVisionDetector
from ec_26_e.vision.factory import preprocess_config
from ec_26_e.vision.preprocess import CpuPreprocessor

ROOT=Path(__file__).resolve().parents[3]


@pytest.fixture
def config():
    return load_tangram_config(ROOT/'sw/configs/tasks/tangram.json',
        env_path=ROOT/'deploy/axu5ev/board.env.example')


def scene():
    a,b=50*math.sqrt(2),25*math.sqrt(2)
    pieces=(
        ((8,177),(8+a,177),(8,177+a)), ((88,177),(88+a,177),(88,177+a)),
        ((50,215),(50+b,215),(50+b,215+b)), ((164,181),(164+b,181),(164+b,181+b)),
        ((148,235),(198,235),(198,285)),
        ((10,250),(10+b,250),(10+b,250+b),(10,250+b)),
        ((48,260),(98,260),(123,285),(73,285)))
    image=np.full((621,876,3),(180,60,30),np.uint8)
    for p in pieces:
        points=np.array([(y/292*875,x/207*620) for x,y in p])
        cv2.fillPoly(image,[np.rint(points).astype(np.int32)],(240,240,240))
    return image


class Camera:
    def __init__(self,image):self.image,self.sequence,self.closed=image,0,False
    def start(self):pass
    def close(self):self.closed=True
    def read_latest(self,**kwargs):
        self.sequence+=1
        return CameraFrame(self.sequence,self.sequence,self.image.copy())


def resizer(image):
    h,w=image.shape[:2]
    rect=ResizeResult(image,(w,h),(w,h),((0.,0.),(w-1.,0.),(w-1.,h-1.),(0.,h-1.)),np.eye(3),0.,1.,0.)
    return SimpleNamespace(resize=lambda _:rect)


def test_white_seven_piece_detection_and_complete_plan_never_constructs_actuator(config):
    image=scene(); camera=Camera(image)
    factory=Mock(side_effect=AssertionError('must not construct motion'))
    plan=run_tangram(config,camera_factory=lambda _:camera,resizer_factory=lambda _:resizer(image),
                     actuator_factory=factory)
    assert len(plan.moves)==7 and camera.closed and camera.sequence==1
    factory.assert_not_called()


def test_unverified_motion_rejected_before_opening_camera(config):
    camera=Mock(side_effect=AssertionError('no camera'))
    with pytest.raises(PlanningError,match='标定'):
        run_tangram(config,execute_motion=True,camera_factory=camera)
    camera.assert_not_called()


def test_verified_motion_receives_validated_plan_after_camera_release(config):
    image=scene();camera=Camera(image)
    def actuator(_):
        assert camera.closed
        return SimpleNamespace(execute=lambda plan: executed.append(plan))
    executed=[]
    run_tangram(replace(config,motion_verified=True),execute_motion=True,
        camera_factory=lambda _:camera,resizer_factory=lambda _:resizer(image),actuator_factory=actuator)
    assert len(executed)==1 and len(executed[0].moves)==7


@pytest.mark.parametrize('change',['missing','extra','touching'])
def test_bad_image_returns_no_partial_detection(config,change):
    image=scene()
    if change=='missing':cv2.rectangle(image,(775,0),(875,620),(180,60,30),-1)
    elif change=='extra':cv2.rectangle(image,(300,80),(350,130),(240,240,240),-1)
    else:cv2.rectangle(image,(525,225),(540,275),(240,240,240),-1)
    params=config.detect1
    with TraditionalVisionDetector(params,expected_pieces=7,preprocessor=CpuPreprocessor(preprocess_config(params))) as detector:
        assert not detector.detect(image).pieces


def test_cancellation_releases_camera_and_never_constructs_motor(config):
    image=scene();camera=Camera(image)
    camera.read_latest=Mock(side_effect=KeyboardInterrupt)
    actuator=Mock()
    with pytest.raises(KeyboardInterrupt):
        run_tangram(config,camera_factory=lambda _:camera,resizer_factory=lambda _:resizer(image),actuator_factory=actuator)
    assert camera.closed
    actuator.assert_not_called()


def test_menu_dispatches_unverified_tangram_as_plan_only_even_with_old_motion_permission(config,tmp_path):
    menu=BoardMenu.__new__(BoardMenu)
    menu.config=dict(board='axu5ev',plan_only=False,motion_enabled=True,wiring_and_calibration_verified=True,
        machine_env=str(tmp_path/'machine.env'),task_config_dir=str(tmp_path))
    menu.model=MenuModel(motion_enabled=True)
    menu.tangram_pattern='cat'
    menu._spawn_task=Mock()
    with patch('ec_26_e.config.tangram.load_tangram_config',return_value=config):
        menu.start_task('七巧板')
    command,environment=menu._spawn_task.call_args.args
    assert '--plan-only' in command and '--execute' not in command
    assert environment['EC_MOTION_ENABLED']=='false' and environment['EC_TANGRAM_PATTERN']=='cat'
    assert menu.model.task_plan_only
    assert len(PATTERN_NAMES)==6 and MODES['七巧板']==('tangram',7)


def test_config_rejects_unknown_or_nonboolean_authorization(tmp_path,config):
    import json
    path=tmp_path/'tangram.json'
    path.write_text(json.dumps(dict(schema_version=1,unknown=True)))
    with pytest.raises(ValueError):load_tangram_config(path)
    with pytest.raises(ValueError):replace(config,motion_verified='true')


def test_verified_menu_dispatches_manual_motion_using_existing_bridge(config,tmp_path):
    menu=BoardMenu.__new__(BoardMenu)
    menu.config=dict(board='axu5ev',plan_only=False,motion_enabled=True,wiring_and_calibration_verified=True,
        machine_env=str(tmp_path/'machine.env'),task_config_dir=str(tmp_path))
    menu.model=MenuModel(motion_enabled=True)
    menu.tangram_pattern='rabbit'
    menu._spawn_task=Mock()
    with patch('ec_26_e.config.tangram.load_tangram_config',return_value=replace(config,motion_verified=True)), \
         patch('ec_26_e.gui.board_menu.Path.exists',return_value=True):
        menu.start_task('七巧板')
    command,environment=menu._spawn_task.call_args.args
    assert '--execute' in command and '--plan-only' not in command
    assert environment['EC_MOTION_ENABLED']=='true' and environment['EC_TANGRAM_PATTERN']=='rabbit'
    assert not menu.model.task_plan_only


def test_tangram_ignores_inherited_pl_backend_without_requiring_bitstream():
    config=load_tangram_config(ROOT/'sw/configs/tasks/tangram.json',
        env_path=ROOT/'deploy/axu5ev/board.env.example',environ={'EC_PREPROCESS_BACKEND':'pl'})
    assert config.detect1.preprocess_backend=='cpu'


def test_multi_frame_review_is_opt_in(config):
    image=scene();camera=Camera(image)
    run_tangram(replace(config,stable_frames=6),camera_factory=lambda _:camera,
                resizer_factory=lambda _:resizer(image))
    assert camera.sequence==6


def test_invalid_single_frame_does_not_retry_capture_or_open_motor(config):
    image=np.full((621,876,3),(180,60,30),np.uint8);camera=Camera(image)
    actuator=Mock()
    with pytest.raises(PlanningError,match='单帧'):
        run_tangram(config,camera_factory=lambda _:camera,resizer_factory=lambda _:resizer(image),actuator_factory=actuator)
    assert camera.sequence==1 and camera.closed
    actuator.assert_not_called()
