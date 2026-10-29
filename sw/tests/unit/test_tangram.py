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
from ec_26_e.vision.tangram import TangramPreprocessor
from scipy.optimize import linear_sum_assignment

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
    assert {'square','cat','rabbit','fish','boat','house','dog','swan','bird',
            'penguin','seal','chair'} <= PATTERN_NAMES.keys()
    assert MODES['七巧板']==('tangram',7)


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


def perturb(image, change):
    """固定种子的单帧退化，不向识别器提供变换参数或模板。"""
    values = image.astype(np.float32)
    h, w = image.shape[:2]
    if change == 'dim':
        values *= .3
    elif change == 'shadow':
        yy, xx = np.mgrid[:h, :w]
        shadow = 1 - .68 * np.exp(-((xx-w*.72)/(w*.2))**2 - ((yy-h*.5)/(h*.9))**2)
        values *= shadow[..., None]
    elif change == 'warm':
        values *= (.75, .95, 1.13)
    elif change == 'cool':
        values *= (1.10, .97, .85)
    elif change == 'desaturated':
        values = values * .60 + values.mean(axis=2, keepdims=True) * .40
    elif change == 'noise':
        values += np.random.default_rng(292207).normal(0, 5, values.shape)
    elif change == 'glare':
        yy, xx = np.mgrid[:h, :w]
        values += 45 * np.exp(-((xx-w*.75)/(w*.3))**2 - ((yy-h*.3)/(h*.5))**2)[..., None]
    elif change == 'blur_jpeg':
        blurred = cv2.GaussianBlur(image, (5, 5), .8)
        ok, encoded = cv2.imencode('.jpg', blurred, [cv2.IMWRITE_JPEG_QUALITY, 55])
        assert ok
        return cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    elif change != 'clean':
        raise AssertionError(change)
    return np.clip(values, 0, 255).astype(np.uint8)


def detect_tangram(image, config):
    with TraditionalVisionDetector(config.detect1, expected_pieces=7,
                                   preprocessor=TangramPreprocessor()) as detector:
        result = detector.detect(image)
        assert len(result.pieces) == 7, detector.last_failure
        return result


@pytest.mark.parametrize('source', ['synthetic', 'real'])
@pytest.mark.parametrize('change', ['clean','dim','shadow','warm','cool','desaturated','noise','glare','blur_jpeg'])
def test_single_frame_robustness_preserves_seven_shapes_and_measured_edges(config, source, change):
    from puzzle_solver import classify_tangram_piece
    from ec_26_e.tasks.task1.review import review_visual_frame
    original = (scene() if source == 'synthetic' else
                cv2.imread(str(ROOT/'sw/tests/fixtures/tangram/white7-20261027.png')))
    assert original is not None
    image = perturb(original, change)
    clean = detect_tangram(original, config)
    result = detect_tangram(image, config)
    scale = np.array((292/(image.shape[1]-1), 207/(image.shape[0]-1)))
    costs = np.array([[np.linalg.norm((np.array(a.center_px)-b.center_px)*scale)
                       for b in result.pieces] for a in clean.pieces])
    rows, cols = linear_sum_assignment(costs)
    assert costs[rows, cols].max() < .8
    for a, b in zip(rows, cols):
        reference = np.array(clean.pieces[a].corners_px) * scale
        observed = np.array(result.pieces[b].corners_px) * scale
        assert len(reference) == len(observed)
        assert min(np.linalg.norm(reference-np.roll(observed,k,axis=0),axis=1).max()
                   for k in range(len(observed))) < 1.
    rect = resizer(image).resize(image)
    reviewed = review_visual_frame(CameraFrame(1,1,image), rect, result,
                                   config.base.options.landscape_rotation)
    kinds = [classify_tangram_piece(p.vertices_mm, 100, 3) for p in reviewed.pieces]
    assert sorted(kinds) == sorted(('large','large','small','small','medium','square','parallel'))


@pytest.mark.parametrize('change', ['missing','extra','touching','occluded','no_blue','empty'])
def test_robust_segmentation_never_turns_invalid_scene_into_motion(config, change):
    image = scene()
    if change == 'missing':
        cv2.rectangle(image, (775,0), (875,620), (180,60,30), -1)
    elif change == 'extra':
        # 目标区灰色异物也必须报告，不能只保留最大的七个候选。
        cv2.rectangle(image, (300,80), (350,130), (100,100,100), -1)
    elif change == 'touching':
        cv2.rectangle(image, (525,225), (540,275), (240,240,240), -1)
    elif change == 'occluded':
        cv2.rectangle(image, (530,35), (610,95), (180,60,30), -1)
    elif change == 'no_blue':
        image[:] = (160,160,160)
    elif change == 'empty':
        image[:] = (180,60,30)
    camera, actuator = Camera(image), Mock()
    with pytest.raises(PlanningError):
        run_tangram(replace(config,motion_verified=True),execute_motion=True,
            camera_factory=lambda _:camera,resizer_factory=lambda _:resizer(image),actuator_factory=actuator)
    assert camera.closed and camera.sequence == 1
    actuator.assert_not_called()


@pytest.mark.parametrize('background', [(180,170,60),(160,40,20)])
def test_white_pieces_on_cyan_and_deep_blue_background(config, background):
    image = scene()
    image[np.all(image == (180,60,30), axis=2)] = background
    result = detect_tangram(image, config)
    assert len(result.pieces) == 7


def test_production_preview_uses_same_adaptive_segmentation_as_task(config):
    from ec_26_e.vision.factory import create_production_detector
    with create_production_detector(config.detect1) as owner:
        assert isinstance(owner.detector.preprocessor,TangramPreprocessor)
        result = owner.detector.detect(perturb(scene(),'shadow'))
        assert len(result.pieces) == 7


@pytest.mark.parametrize('pattern', PATTERN_NAMES)
def test_saved_photo_recognition_plans_every_pattern_inside_machine_limits(config, pattern):
    image = cv2.imread(str(ROOT/'sw/tests/fixtures/tangram/white7-20261027.png'))
    camera, actuator = Camera(image), Mock()
    selected = replace(config, planning=replace(config.planning, pattern_id=pattern))
    plan = run_tangram(selected, camera_factory=lambda _:camera,
        resizer_factory=lambda _:resizer(image), actuator_factory=actuator)
    assert len(plan.moves) == 7 and plan.mode == 'tangram'
    assert camera.sequence == 1 and camera.closed
    actuator.assert_not_called()
