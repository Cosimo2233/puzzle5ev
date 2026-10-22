"""验证正式检测的亚像素交点细化，包含尖角、凹角和无边缘时回退。"""
import cv2
import numpy as np
import pytest
from ec_26_e.vision.outline_edges import refine_polygon_edges


@pytest.mark.parametrize('corners', [
    [(105.3, 105.6), (507.4, 65.8), (238.7, 322.3)],
    [(80.3, 70.6), (280.4, 70.6), (280.4, 140.8),
     (160.7, 140.8), (160.7, 300.3), (80.3, 300.3)],
])
def test_refinement_recovers_known_fractional_corners_without_losing_concavity(corners):
    corners = np.array(corners, np.float32)
    image = np.full((1656, 2336, 3), (180, 90, 30), np.uint8)
    cv2.fillPoly(image, [np.rint(corners * 4).astype(np.int32)], (235, 235, 235))
    image = cv2.resize(image, (584, 414), interpolation=cv2.INTER_AREA)
    seed = (corners + (1.8, -1.5)).astype(np.float32).reshape(-1, 1, 2)
    refined = refine_polygon_edges(image, seed, band=4, is_polygon=True).reshape(-1, 2)
    assert refined.shape == corners.shape
    assert np.linalg.norm(refined - corners, axis=1).max() < 1.0


def test_missing_color_transition_keeps_the_validated_seed():
    image = np.full((414, 584, 3), (180, 90, 30), np.uint8)
    seed = np.array([[(80, 70)], [(280, 70)], [(160, 300)]], np.float32)
    assert np.array_equal(refine_polygon_edges(image, seed, band=3, is_polygon=True), seed)
