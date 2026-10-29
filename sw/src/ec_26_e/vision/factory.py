"""构造正式任务统一使用的 YOLO 语义门控与几何精修检测器。"""

from __future__ import annotations

from ec_26_e.vision.contracts import (
    Detect1Config,
    StableVisionConfig,
    VisionConfig,
)
from ec_26_e.vision.detector import VisionDetector
from ec_26_e.vision.stabilizer import StableVisionDetector
from ec_26_e.vision.preprocess import PreprocessConfig


def preprocess_config(config: Detect1Config) -> PreprocessConfig:
    """预览和任务必须采用同一组颜色参数。"""
    return PreprocessConfig(config.blue_ratio_q8, config.blue_min,
                            config.brightness_min, config.blue_background_fraction_q8, config.blue_profile)


def create_legacy_yolo_detector(
    legacy_config: Detect1Config,
) -> StableVisionDetector:
    """保持旧工厂签名，并返回 9 帧窗口、6 帧确认的统一视觉检测器。

    ``legacy_config`` 只保留用于 Task1/Task2 现有配置和第三方工厂注入
    契约。正式检测参数集中固定在此处，生产路径不再依据旧 Detect1
    配置选择纯传统视觉检测器。
    """

    if not isinstance(legacy_config, Detect1Config):
        raise TypeError("legacy_config 必须是 Detect1Config")
    detector = VisionDetector(
        VisionConfig(
            score_threshold=0.15,
            nms_threshold=0.45,
            mask_threshold=0.50,
            dilation_mm=3.0,
            fallback_dilation_mm=4.0,
            short_edge_merge_mm=legacy_config.short_edge_merge_mm,
            minimum_physical_edge_mm=(
                legacy_config.minimum_physical_edge_mm
            ),
            minimum_corner_turn_deg=(
                legacy_config.minimum_corner_turn_deg
            ),
            minimum_corner_depth_mm=(
                legacy_config.minimum_corner_depth_mm
            ),
            minimum_polygon_iou=legacy_config.minimum_polygon_iou,
            min_vertices=3,
            max_vertices=5,
            max_pieces=4,
        )
    )
    return StableVisionDetector(
        detector,
        StableVisionConfig(window_size=9, required_frames=6),
    )


def create_production_detector(legacy_config: Detect1Config) -> StableVisionDetector:
    """7020 正式入口；YOLO 仅保留为离线基线，不作运行时回退。"""
    from ec_26_e.vision.traditional import TraditionalVisionDetector
    from ec_26_e.vision.preprocess import CpuPreprocessor, PreprocessConfig
    if not isinstance(legacy_config, Detect1Config):
        raise TypeError("legacy_config 必须是 Detect1Config")
    params = preprocess_config(legacy_config)
    backend = legacy_config.preprocess_backend
    expected = legacy_config.expected_pieces
    if expected is not None and expected not in {2,3,4,7}:
        raise ValueError("EC_EXPECTED_PIECES 必须为 2～4 或七巧板的 7")
    if expected == 7:
        # 七巧板预览与单张采图任务使用同一个白片分割算法。
        from ec_26_e.vision.tangram import TangramPreprocessor
        preprocessor = TangramPreprocessor()
    elif backend == "cpu":
        preprocessor = CpuPreprocessor(params)
    elif backend == "pl":
        import os
        access = os.environ.get('EC_PL_ACCESS', 'pynq')
        if access == 'linux':
            from ec_26_e.vision.linux_pl import LinuxPreprocessor
            preprocessor = LinuxPreprocessor(legacy_config.pl_bitstream, params)
        elif access == 'pynq':
            from ec_26_e.vision.pl import PynqPreprocessor
            preprocessor = PynqPreprocessor(legacy_config.pl_bitstream, params)
        else:
            raise ValueError('EC_PL_ACCESS 必须为 pynq 或 linux')
    else:
        raise ValueError("EC_PREPROCESS_BACKEND 只能为 cpu 或 pl")
    detector = TraditionalVisionDetector(legacy_config, expected_pieces=expected, preprocessor=preprocessor)
    return StableVisionDetector(detector, StableVisionConfig(window_size=6, required_frames=6))
