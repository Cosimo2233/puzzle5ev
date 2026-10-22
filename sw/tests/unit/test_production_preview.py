from types import SimpleNamespace
from unittest.mock import Mock
import numpy as np

from ec_26_e.gui.production_preview import annotate


def test_failed_count_keeps_all_five_candidates_visible():
    raw = np.zeros((120, 200, 3), np.uint8)
    rect = SimpleNamespace(image_bgr=raw, homography=np.eye(3),
                           source_corners_px=((2, 2), (197, 2), (197, 117), (2, 117)))
    contours = [np.array([[[10 + i*35, 40]], [[20+i*35, 40]],
                          [[20+i*35, 60]], [[10+i*35, 60]]], np.int32) for i in range(5)]
    detector = SimpleNamespace(detect=Mock(return_value=SimpleNamespace(pieces=())),
                               last_preview_contours=contours,
                               last_failure='预期 4 片，检测到 5 个连通域')
    canvas, report = annotate(raw, Mock(resize=Mock(return_value=rect)), detector)
    assert report['candidate_count'] == 5
    assert report['piece_count'] == 0 and not report['valid']
    assert report['reason'] == detector.last_failure
    for contour in contours:
        x, y = contour[0, 0]
        assert canvas[y, x].any()
    assert not raw.any()


def test_workspace_failure_does_not_show_stale_candidates():
    raw = np.zeros((120, 200, 3), np.uint8)
    detector = Mock()
    canvas, report = annotate(raw, Mock(resize=Mock(side_effect=ValueError('工作区无效'))), detector)
    assert report['candidate_count'] == 0 and not report['paper_detected']
    assert report['reason'] == '工作区无效'
    detector.detect.assert_not_called()
