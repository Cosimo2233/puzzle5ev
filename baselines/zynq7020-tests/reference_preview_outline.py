"""验证蓝底预览只标注工作区内的外边界，保留凹形且拒绝残缺底纸。"""
import importlib.util
from pathlib import Path

import cv2
import numpy as np


source = Path(__file__).resolve().parents[3] / 'deploy/zynq7020/preview_outline.py'
spec = importlib.util.spec_from_file_location('preview_outline', source)
preview = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preview)


def scene():
    image = np.full((450, 800, 3), 15, np.uint8)
    paper = np.array([(120, 50), (680, 65), (660, 400), (135, 390)], np.int32)
    cv2.fillConvexPoly(image, paper, (190, 130, 30))
    return image


def test_perspective_paper_and_concave_piece_keep_real_boundaries():
    image = scene()
    shape = np.array([(220, 150), (310, 150), (310, 180),
                      (255, 180), (255, 240), (220, 240)], np.int32)
    cv2.fillPoly(image, [shape], (230, 230, 230))
    cv2.rectangle(image, (450, 170), (530, 240), (220, 220, 220), -1)
    # 内部黑色印刷与底纸外物体均不应增加轮廓。
    cv2.circle(image, (480, 200), 8, (0, 0, 0), -1)
    cv2.rectangle(image, (15, 160), (85, 240), (255, 255, 255), -1)
    paper, pieces = preview.detect_outlines(image)
    assert paper is not None and len(pieces) == 2
    concave = min(pieces, key=lambda c: cv2.boundingRect(c)[0])
    assert cv2.contourArea(concave) / cv2.contourArea(cv2.convexHull(concave)) < 0.85
    for corner in [(120, 50), (680, 65), (660, 400), (135, 390)]:
        assert np.linalg.norm(paper.reshape(-1, 2) - corner, axis=1).min() < 5


def test_red_overlay_and_empty_blue_paper():
    image = scene()
    before = image.copy()
    result = preview.draw_outlines(image)
    assert result == {'paper_detected': True, 'piece_count': 0}
    red = (image[:, :, 2] > 200) & (image[:, :, 0] < 40) & (image[:, :, 1] < 40)
    assert red.sum() > 1000
    assert np.array_equal(image[200, 400], before[200, 400])


def test_no_paper_does_not_outline_unrelated_white_objects():
    image = np.full((450, 800, 3), 15, np.uint8)
    cv2.rectangle(image, (200, 100), (600, 380), (255, 255, 255), -1)
    assert preview.detect_outlines(image) == (None, [])
    assert preview.draw_outlines(image)['paper_detected'] is False


def test_clipped_blue_paper_is_rejected():
    image = np.full((450, 800, 3), 15, np.uint8)
    cv2.rectangle(image, (0, 40), (650, 400), (190, 130, 30), -1)
    assert preview.detect_outlines(image) == (None, [])


def test_piece_occluding_paper_edge_rejects_incomplete_workspace():
    image = scene()
    cv2.rectangle(image, (127, 190), (210, 240), (230, 230, 230), -1)
    assert preview.detect_outlines(image) == (None, [])


def test_missing_paper_does_not_retain_previous_frame_outlines():
    assert preview.detect_outlines(scene())[0] is not None
    assert preview.detect_outlines(np.zeros((450, 800, 3), np.uint8)) == (None, [])


def test_dark_cyan_reflections_do_not_join_paper_to_frame_edge():
    image = scene()
    cv2.rectangle(image, (120, 390), (680, 449), (40, 30, 5), -1)
    # 模拟黑边框的暗青反光与纸张紧挨；纸张底边仍应在画面内。
    paper, pieces = preview.detect_outlines(image)
    assert paper is not None and not pieces
    assert paper[:, :, 1].max() < 420


def test_dimmer_paper_uses_scene_brightness_instead_of_fixed_cutoff():
    image = (scene().astype(np.float32) * 0.5).astype(np.uint8)
    assert preview.detect_outlines(image)[0] is not None
