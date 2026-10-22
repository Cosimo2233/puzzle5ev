import unittest
import json
from pathlib import Path
from unittest.mock import patch
import cv2
import numpy as np
from ec_26_e.vision.traditional import TraditionalVisionDetector, fit_polygon
from ec_26_e.vision.factory import create_production_detector
from ec_26_e.vision.contracts import Detect1Config
from ec_26_e.vision.geometry import polygon_turn_angles_deg, polygon_iou, polygon_edge_lengths

def scene():
    image = np.full((414,584,3), (180,60,30), np.uint8)
    cv2.fillPoly(image,[np.array([[40,40],[180,40],[180,95],[110,95],[110,165],[40,165]])],(240,240,240))
    cv2.rectangle(image,(280,40),(410,165),(230,230,230),-1)
    cv2.rectangle(image,(40,240),(180,365),(220,220,220),-1)
    cv2.fillPoly(image,[np.array([[300,240],[445,240],[360,365]])],(230,230,230))
    return image

class TraditionalTest(unittest.TestCase):
    def setUp(self):
        self.config=Detect1Config(short_edge_merge_mm=8,minimum_physical_edge_mm=8)

    def test_concave_and_printed_interior(self):
        image=scene()
        cv2.circle(image,(70,70),12,(0,0,0),-1)
        detector=TraditionalVisionDetector(self.config)
        result=detector.detect(image)
        self.assertEqual(len(result.pieces),4,detector.last_failure)
        self.assertTrue(any(np.any(polygon_turn_angles_deg(np.array(p.corners_px))<0) for p in result.pieces))

    def test_live_clipped_tip_recovers_four_supported_edges(self):
        path = Path(__file__).parents[2] / 'fixtures/vision/fixed4-clipped-tip.json'
        contour = np.asarray(json.loads(path.read_text()), np.int32).reshape(-1, 1, 2)
        scale = np.array([292 / 763, 207 / 539])
        epsilon = cv2.arcLength(contour, True) * self.config.polygon_epsilon_ratio
        original, _ = fit_polygon(contour, epsilon)
        self.assertEqual(len(original), 5)
        self.assertLess(polygon_edge_lengths(original * scale).min(), 2)
        for ordered in (contour, contour[::-1].copy()):
            polygon, residual = fit_polygon(ordered, epsilon, mm_scale=scale, config=self.config)
            self.assertEqual(len(polygon), 4)
            self.assertGreater(polygon_edge_lengths(polygon * scale).min(), 8)
            self.assertGreater(polygon_iou(contour, polygon), self.config.minimum_polygon_iou)
            self.assertLess(residual * max(scale), 1)

    def test_short_concave_notch_is_not_merged(self):
        mask = np.zeros((220, 220), np.uint8)
        points = np.array([[30,30],[180,30],[180,180],[106,180],[100,174],[94,180],[30,180]])
        cv2.fillPoly(mask, [points], 255)
        contour = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)[0][0]
        original, _ = fit_polygon(contour, 1)
        merged, _ = fit_polygon(contour, 1, mm_scale=np.array([.5,.5]), config=self.config)
        np.testing.assert_allclose(merged, original)
        self.assertTrue(np.any(polygon_turn_angles_deg(merged) < 0))

    def test_count_and_boundary_fail_whole_frame(self):
        for image in (np.full((414,584,3),(180,60,30),np.uint8), scene()):
            cv2.rectangle(image,(0,0),(100,100),(240,240,240),-1)
            self.assertFalse(TraditionalVisionDetector(self.config).detect(image).pieces)

    def test_black_print_reaching_edge_does_not_cut_paper(self):
        image=scene()
        cv2.rectangle(image,(280,65),(320,115),(0,0,0),-1)
        detector=TraditionalVisionDetector(self.config)
        result=detector.detect(image)
        self.assertEqual(len(result.pieces),4,detector.last_failure)

    def test_touching_and_extra_piece(self):
        image=scene()
        cv2.rectangle(image,(150,65),(320,80),(240,240,240),-1)
        self.assertFalse(TraditionalVisionDetector(self.config).detect(image).pieces)
        image=scene()
        cv2.rectangle(image,(460,180),(550,260),(240,240,240),-1)
        self.assertFalse(TraditionalVisionDetector(self.config).detect(image).pieces)

    def test_factory_no_npu_and_reset_on_invalid_frame(self):
        with patch('ec_26_e.vision.npu.NpuYolo11SegInferencer.__init__',side_effect=AssertionError('NPU accessed')):
            with create_production_detector(self.config) as detector:
                for _ in range(5): self.assertFalse(detector.detect(scene()).pieces)
                self.assertEqual(len(detector.detect(scene()).pieces),4)
                self.assertFalse(detector.detect(np.full_like(scene(),(180,60,30))).pieces)
                self.assertFalse(detector.detect(scene()).pieces)

    def test_two_and_three_expected_counts(self):
        for count in (2,3):
            image=scene()
            image[200:,:,:]=(180,60,30)
            if count==3: cv2.rectangle(image,(40,240),(180,365),(240,240,240),-1)
            detector=TraditionalVisionDetector(self.config,expected_pieces=count)
            self.assertEqual(len(detector.detect(image).pieces),count,detector.last_failure)

    def test_auto_count_requires_new_stable_frames_after_count_changes(self):
        from dataclasses import replace
        with create_production_detector(replace(self.config, expected_pieces=None)) as detector:
            for count in (4, 2, 3):
                image = scene()
                if count < 4:
                    image[200:, :, :] = (180, 60, 30)
                if count == 3:
                    cv2.rectangle(image, (40, 240), (180, 365), (240, 240, 240), -1)
                for _ in range(5):
                    self.assertFalse(detector.detect(image).pieces)
                self.assertEqual(len(detector.detect(image).pieces), count)

    def test_auto_count_rejects_one_five_boundary_and_touching(self):
        detector = TraditionalVisionDetector(self.config, expected_pieces=None)
        one = scene()
        one[200:, :, :] = (180, 60, 30)
        one[:200, 250:, :] = (180, 60, 30)
        five = scene()
        cv2.rectangle(five, (460, 180), (550, 260), (240, 240, 240), -1)
        boundary = scene()
        cv2.rectangle(boundary, (0, 0), (180, 95), (240, 240, 240), -1)
        touching = scene()
        cv2.rectangle(touching, (150, 65), (320, 80), (240, 240, 240), -1)
        for image in (one, five, boundary, touching):
            with self.subTest():
                self.assertFalse(detector.detect(image).pieces)
