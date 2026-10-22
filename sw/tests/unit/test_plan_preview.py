import json
from types import SimpleNamespace
from unittest.mock import Mock
import cv2
import numpy as np
import pytest

from ec_26_e.gui.plan_preview import PlanPreviewPublisher, sheet_to_camera, render_plan
from ec_26_e.tasks.task1.review import _landscape_point_to_portrait_mm
from ec_26_e.gui.production_preview import overlay_is_current, draw_latest


@pytest.mark.parametrize('rotation', ['clockwise', 'counterclockwise'])
def test_preview_projection_is_inverse_of_execution_coordinates(rotation):
    camera = np.array([[15, 25], [200, 10], [195, 120], [10, 115]], np.float32)
    target = np.array([[0,0],[761,0],[761,539],[0,539]], np.float32)
    h = cv2.getPerspectiveTransform(camera, target)
    uv = [(0.,0.), (1.,0.), (1.,1.), (0.,1.)]
    mm = [_landscape_point_to_portrait_mm(p, rotation) for p in uv]
    np.testing.assert_allclose(sheet_to_camera(mm, h, (762,540), rotation), camera, atol=1e-5)


def plan():
    return SimpleNamespace(target_width_mm=60.,target_height_mm=40.,confidence=.99,moves=[SimpleNamespace(
        order=1,piece_id='a',rotation_deg_clockwise=90.,pick_xy_a4_mm=(20.,20.),place_xy_a4_mm=(70.,70.),
        observed_vertices_a4_mm=((10.,10.),(30.,10.),(20.,30.)),
        target_vertices_a4_mm=((60.,60.),(80.,60.),(70.,80.)))])


def test_prediction_requires_this_tasks_snapshot_and_failure_invalidates(tmp_path):
    first = PlanPreviewPublisher(tmp_path, 'first', 'counterclockwise')
    with pytest.raises(RuntimeError, match='快照'):
        first.publish(plan())
    raw = np.zeros((120,200,3), np.uint8)
    first.capture(SimpleNamespace(image_bgr=raw,sequence_id=22),
                  SimpleNamespace(homography=np.eye(3),target_size=(200,120)))
    first.publish(plan())
    state = json.loads((tmp_path/'first.json').read_text())
    assert state['state']=='ready' and state['token']=='first'
    assert (tmp_path/'first.ppm').read_bytes().startswith(b'P6\n880 495')
    assert not raw.any()
    second = PlanPreviewPublisher(tmp_path, 'second', 'clockwise')
    assert json.loads((tmp_path/'second.json').read_text())['state']=='acquiring'
    second.fail('no solution')
    assert json.loads((tmp_path/'second.json').read_text())['state']=='failed'
    assert not (tmp_path/'second.ppm').exists()


def test_preview_hides_old_contours_after_motion_or_age():
    a = np.zeros((90,160),np.uint8)
    assert overlay_is_current(a,a,.2)
    assert not overlay_is_current(a,a,2.01)
    b=a.copy(); b[20:30,20:40]=100
    assert not overlay_is_current(a,b,.2)
    raw=np.zeros((120,200,3),np.uint8)
    image,report=draw_latest(raw,b,(a,1.,dict(candidate_count=4,valid=True,contours_source_px=[])),1.2)
    assert not report['valid'] and report['candidate_count']==0
    assert image.shape==(495,880,3)


def test_prediction_history_is_bounded_without_deleting_unrelated_files(tmp_path):
    unrelated = tmp_path/'config.json'
    unrelated.write_text('{"keep": true}')
    for index in range(10):
        PlanPreviewPublisher(tmp_path, f'task{index}', 'clockwise')
        (tmp_path/f'task{index}.ppm').write_bytes(b'frame')
    assert unrelated.exists()
    assert len(list(tmp_path.glob('task*.json'))) == 4
    assert len(list(tmp_path.glob('task*.ppm'))) == 4


def test_cancel_during_camera_release_cannot_dispatch_queued_start():
    from ec_26_e.gui.board_menu import BoardMenu
    menu=BoardMenu.__new__(BoardMenu)
    menu.transition=Mock()
    menu.pending_action=('confirm','mouse')
    menu._apply_action=Mock()
    menu.action('cancel','mouse')
    assert menu.pending_action==('cancel','mouse')
    menu._apply_action.assert_not_called()
