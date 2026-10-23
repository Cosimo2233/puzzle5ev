"""实拍蓝灰花纹，以及曝光变化/真实凹角的颜色分割回归。"""
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np
import pytest

from ec_26_e.vision.contracts import Detect1Config
from ec_26_e.vision.factory import create_production_detector
from ec_26_e.vision.preprocess import CpuPreprocessor, PreprocessConfig, resolve_blue_min, resolve_blue_parameters


CARD = Path(__file__).parents[2] / 'fixtures/vision/poker-j-blue-print.png'
CONFIG = Detect1Config(expected_pieces=None, min_vertices=3, max_vertices=5, max_pieces=4,
                       polygon_epsilon_ratio=.018, short_edge_merge_mm=8,
                       minimum_physical_edge_mm=8, blue_ratio_q8=320,
                       blue_background_fraction_q8=218)


@pytest.mark.parametrize('gain', [.6, .8, .9, .95, 1., 1.05, 1.1])
def test_real_print_crossing_cut_edges_stays_four_pieces(gain):
    image = cv2.imread(str(CARD))
    adjusted = np.clip(image.astype(float) * gain, 0, 255).astype(np.uint8)
    with create_production_detector(CONFIG) as owner:
        reference = owner.detector.detect(image)
        observed = owner.detector.detect(adjusted)
        assert [len(p.corners_px) for p in observed.pieces] == [3, 5, 4, 4]
        for before, after in zip(reference.pieces, observed.pieces):
            delta = (np.array(before.corners_px) - np.array(after.corners_px)) * [292/761, 207/539]
            # 沿用任务的 2 mm 观测误差模型；饱和曝光下仍须落在该范围。
            assert np.linalg.norm(delta, axis=1).max() < 2.0
        for _ in range(6):
            stable = owner.detect(adjusted)
        assert len(stable.pieces) == 4


def test_original_scene_reproduces_static_threshold_failure():
    with create_production_detector(replace(CONFIG, blue_ratio_q8=275,
                                             blue_background_fraction_q8=0)) as owner:
        assert not owner.detector.detect(cv2.imread(str(CARD))).pieces


def test_fixed_cyan_scene_uses_its_own_color_profile():
    image = cv2.imread(str(CARD.with_name('fixed-cyan-background.png')))
    fixed = replace(CONFIG, expected_pieces=4, max_vertices=6,
                    blue_ratio_q8=275, blue_background_fraction_q8=0)
    with create_production_detector(CONFIG) as owner:
        with pytest.raises(ValueError, match='蓝底采样不足'):
            owner.detector.detect(image)
        assert '0%' in owner.detector.last_failure
    for gain in (.7, .85, 1., 1.1):
        adjusted = np.clip(image.astype(float)*gain, 0, 255).astype(np.uint8)
        with create_production_detector(fixed) as owner:
            result = owner.detector.detect(adjusted)
            assert sorted(len(p.corners_px) for p in result.pieces) == [3, 4, 4, 4]


@pytest.mark.parametrize('name,ratio', [('fixed-cyan-background.png',275),('fixed-blue-cast.png',320)])
@pytest.mark.parametrize('gain', [.7, .85, 1., 1.1])
def test_fixed_auto_profile_handles_both_recorded_color_states(name, ratio, gain):
    raw = cv2.imread(str(CARD.with_name(name)))
    image = np.clip(raw.astype(float)*gain, 0, 255).astype(np.uint8)
    config = replace(CONFIG, expected_pieces=4, max_vertices=6, blue_profile='fixed_auto')
    with create_production_detector(config) as owner:
        result = owner.detector.detect(image)
        assert sorted(len(p.corners_px) for p in result.pieces) == [3,4,4,4], owner.detector.last_failure
        assert owner.detector.preprocessor.last_blue_ratio_q8 == ratio


def test_auto_profile_rejects_missing_or_insufficient_blue_without_stale_reuse():
    params = PreprocessConfig(blue_profile='fixed_auto')
    image = np.full((120,120,3), (185,163,70), np.uint8)
    assert resolve_blue_parameters(image, params) == (275,45)
    image[:] = 230
    with pytest.raises(ValueError, match='蓝底采样不足'):
        resolve_blue_parameters(image, params)
    image[:30] = (185,163,70)  # 少量蓝色物体不能代表整个底板。
    with pytest.raises(ValueError, match='蓝底采样不足'):
        resolve_blue_parameters(image, params)


def test_print_rejection_does_not_fill_concavity_or_bridge_gap():
    image = np.full((400, 600, 3), (233, 169, 45), np.uint8)
    # 明确的凹口以及分离的第二片；印刷一直延伸到第一片外边。
    points = np.array([[40,40], [250,40], [250,260], [145,170], [40,260]])
    cv2.fillPoly(image, [points], (245,245,245))
    cv2.rectangle(image, (280,40), (450,260), (245,245,245), -1)
    params = PreprocessConfig(300,45,0,218)
    expected = CpuPreprocessor(params).process(image, 1)
    cv2.rectangle(image, (40,80), (120,130), (166,85,55), -1)
    cv2.rectangle(image, (280,90), (330,140), (152,72,39), -1)
    observed = CpuPreprocessor(params).process(image, 2)
    np.testing.assert_array_equal(expected, observed)
    assert observed[245,145] == 0  # 真实凹口
    assert observed[100,265] == 0  # 片间间隙


def test_no_blue_background_fails_instead_of_using_stale_threshold():
    params = PreprocessConfig(300,45,0,218)
    valid = np.full((120,120,3), (233,169,45), np.uint8)
    assert resolve_blue_min(valid, params) == 198
    with pytest.raises(ValueError, match='蓝底采样不足'):
        resolve_blue_min(np.full_like(valid, 230), params)
    assert resolve_blue_min(valid, PreprocessConfig()) == 45


@pytest.mark.parametrize('value', [-1, 256, True, 1.5])
def test_invalid_fraction_rejected(value):
    with pytest.raises(ValueError):
        PreprocessConfig(blue_background_fraction_q8=value)
