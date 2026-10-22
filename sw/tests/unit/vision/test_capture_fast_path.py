from unittest.mock import patch

import cv2
import numpy as np
import pytest

from ec_26_e.vision.resize.resizer import _WorkspaceEdgeImages, _WorkspaceImages
from ec_26_e.vision.traditional import TraditionalVisionDetector, fit_polygon
from ec_26_e.vision.contracts import Detect1Config
from ec_26_e.vision.preprocess import CpuPreprocessor, PreprocessConfig


@pytest.mark.parametrize('seed', [
    [[40, 30], [300, 35], [310, 210], [35, 215]],
    [[0, 0], [319, 0], [319, 239], [0, 239]],
])
def test_edge_tiles_preserve_full_image_blur_lab_and_chroma(seed):
    raw = np.random.default_rng(42).integers(0, 256, (240, 320, 3), dtype=np.uint8)
    full = _WorkspaceImages(raw)
    tiles = _WorkspaceEdgeImages(raw, np.asarray(seed, np.float32), 6)
    covered = np.isfinite(tiles.luminance())
    np.testing.assert_array_equal(tiles.luminance()[covered], full.luminance()[covered])
    np.testing.assert_array_equal(tiles.chroma()[covered], full.chroma()[covered])
    tiles.require_appearance_coverage(np.asarray(seed))
    with pytest.raises(ValueError):
        tiles.require_appearance_coverage(np.asarray(seed) + [60, 0])


def test_random_rectangles_do_not_refit_when_recovery_adds_no_vertices():
    raw = np.full((414, 584, 3), (180, 60, 30), np.uint8)
    for x, y in ((40, 40), (300, 40), (40, 240), (300, 240)):
        cv2.rectangle(raw, (x, y), (x+100, y+100), (230, 230, 230), -1)
    detector = TraditionalVisionDetector(Detect1Config(), expected_pieces=None)
    with patch('ec_26_e.vision.traditional.fit_polygon', wraps=fit_polygon) as fit:
        assert len(detector.detect(raw).pieces) == 4
        assert fit.call_count == 4


def test_blue_tinted_white_pieces_survive_current_board_colour_profile():
    raw = np.full((414, 584, 3), (225, 150, 65), np.uint8)
    for x, y in ((40, 40), (300, 40), (40, 240), (300, 240)):
        cv2.rectangle(raw, (x, y), (x+100, y+100), (250, 220, 205), -1)
    detector = TraditionalVisionDetector(Detect1Config(), expected_pieces=4,
        preprocessor=CpuPreprocessor(PreprocessConfig(320, 45, 0, 218)))
    assert len(detector.detect(raw).pieces) == 4
