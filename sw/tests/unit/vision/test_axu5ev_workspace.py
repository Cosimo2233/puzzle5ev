"""现场回归：错误参考框曾令右边界跳动约 90 px 并切断拼片。"""
from dataclasses import replace
import json
from pathlib import Path
import unittest

import cv2
import numpy as np
from ec_26_e.vision import Resize, ResizeConfig
from ec_26_e.vision.contracts import Detect1Config
from ec_26_e.vision.factory import create_production_detector

ROOT = Path(__file__).resolve().parents[4]
FIXTURES = Path(__file__).resolve().parents[2] / 'fixtures/vision'


class Axu5evWorkspaceTests(unittest.TestCase):
    def test_live_frames_use_verified_edges_and_preserve_four_pieces(self):
        profile = json.loads((ROOT/'deploy/axu5ev/tasks/fixed.json').read_text())
        resizer = Resize(ResizeConfig(**profile['resize']))
        detector_config = Detect1Config(**profile['detect1'], blue_ratio_q8=275)
        corners = []
        with create_production_detector(detector_config) as owner:
            for index in ('000', '019'):
                raw = cv2.imread(str(FIXTURES/f'axu5ev-workspace-{index}.png'))
                rect = resizer.resize(raw)
                self.assertGreater(rect.workspace_threshold, 0)
                corners.append(rect.source_corners_px)
                result = owner.detector.detect(rect.image_bgr)
                self.assertEqual(len(result.pieces), 4, owner.detector.last_failure)
                self.assertEqual(sorted(len(p.corners_px) for p in result.pieces), [3, 4, 4, 4])
        self.assertLess(np.linalg.norm(np.asarray(corners[0])-corners[1], axis=1).max(), 2)

    def test_stale_reference_is_reported_instead_of_silently_clipping(self):
        image = cv2.imread(str(FIXTURES/'axu5ev-workspace-019.png'))
        with self.assertRaisesRegex(ValueError, '工作区定位失败'):
            Resize(replace(ResizeConfig(), search_band_ratio=.04)).resize(image)
