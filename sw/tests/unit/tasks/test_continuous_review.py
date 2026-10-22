"""连续窗口不能复用旧场景；同时满足时长、新帧数量及几何阈值。"""
from dataclasses import replace
import pytest
from ec_26_e.tasks.task1 import Task1Options
from ec_26_e.tasks.task1.stability import StableSceneReviewer
from .test_task1_stability import make_reviewed_frame, two_pieces


def reviewer():
    return StableSceneReviewer(Task1Options(continuous_review=True, minimum_stable_frames=6,
        allowed_jump_frames=0, acquisition_timeout_seconds=10))


def frame(index, seconds, offset=0):
    return make_reviewed_frame(index, int((seconds+1)*1e9), two_pieces(1, 2, offset=offset))


def test_requires_six_distinct_frames_and_two_seconds():
    gate = reviewer()
    for index in range(5):
        assert gate.add_frame(frame(index, index*.5)) is None
    assert gate.add_frame(frame(5, 2.5)).accepted
    gate = reviewer()
    for index in range(6):
        assert gate.add_frame(frame(index, index*.1)) is None
    assert gate.add_frame(frame(6, 2)).accepted


@pytest.mark.parametrize('failure', ['move', 'drop', 'workspace'])
def test_failure_restarts_time_and_does_not_accept_historical_frames(failure):
    gate = reviewer()
    for index in range(5):
        gate.add_frame(frame(index, index*.4))
    if failure == 'drop':
        gate.note_processing_failure(int(2.8e9))
    elif failure == 'move':
        gate.add_frame(frame(5, 1.8, offset=8))
    else:
        shifted = replace(frame(5, 1.8), workspace_corners_source_px=((160,80),(1240,80),(1240,640),(160,640)))
        gate.add_frame(shifted)
    for index in range(5):
        assert gate.add_frame(frame(10+index, 2+index*.4)) is None
    assert gate.add_frame(frame(15, 4)).accepted


def test_anchor_rejects_slow_accumulated_drift():
    gate = reviewer()
    for index in range(7):
        assert gate.add_frame(frame(index, index*.4, offset=index*.7)) is None


def test_duplicate_and_backwards_time_rejected():
    gate = reviewer()
    gate.add_frame(frame(1, 0))
    with pytest.raises(ValueError): gate.add_frame(frame(1, 1))
    with pytest.raises(ValueError): gate.add_frame(frame(2, 0))
