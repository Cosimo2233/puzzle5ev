"""验证 Task2 随机拼图配置、任务选择和独立环境覆盖。

该文件只读写临时 JSON，不访问相机、串口、GPIO 或输出目录。可通过
``python -m unittest tests.unit.test_task2_config`` 独立运行。
"""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ec_26_e.config import (
    DEFAULT_TASK2_CONFIG_PATH,
    Task2Config,
    load_runtime_config,
    load_task2_config,
)


TEST_ENV = {"MOTION_MAGNET_GPIO_LINE": "12"}


class Task2ConfigTest(unittest.TestCase):
    """验证 Task2 的随机模式和白纸检测模式不会泄漏到 Task1 环境项。"""

    def test_fixed_preprocessing_overrides_global_card_parameters(self):
        from ec_26_e.config import load_task1_config
        env = dict(TEST_ENV, EC_BLUE_RATIO_Q8='320', EC_BLUE_BACKGROUND_FRACTION_Q8='218',
                   TASK1_BLUE_RATIO_Q8='275', TASK1_BLUE_BACKGROUND_FRACTION_Q8='0', TASK1_BLUE_PROFILE='fixed_auto')
        fixed = load_task1_config(environ=env, env_path=None)
        random = load_task2_config(environ=env, env_path=None)
        self.assertEqual((fixed.detect1.blue_ratio_q8, fixed.detect1.blue_background_fraction_q8), (275, 0))
        self.assertEqual((random.detect1.blue_ratio_q8, random.detect1.blue_background_fraction_q8), (320, 218))
        self.assertEqual(fixed.detect1.blue_profile, 'fixed_auto')
        self.assertEqual(random.detect1.blue_profile, 'configured')

    def test_card_preprocessing_is_scoped_to_random_mode(self):
        from ec_26_e.config import load_task1_config
        env = dict(TEST_ENV, EC_BLUE_RATIO_Q8='275', TASK2_BLUE_RATIO_Q8='300',
                   TASK2_BLUE_BACKGROUND_FRACTION_Q8='218')
        fixed = load_task1_config(environ=env, env_path=None)
        random = load_task2_config(environ=env, env_path=None)
        self.assertEqual((fixed.detect1.blue_ratio_q8, fixed.detect1.blue_background_fraction_q8), (275, 0))
        self.assertEqual((random.detect1.blue_ratio_q8, random.detect1.blue_background_fraction_q8), (300, 218))

    def test_official_configuration_is_random_white_paper_task(self) -> None:
        """正式配置应固定随机规划和白纸 Detect1 模式。"""

        config = load_task2_config(environ=TEST_ENV, env_path=None)

        self.assertIsInstance(config, Task2Config)
        self.assertEqual(config.options.planning_options.mode, "random")
        self.assertEqual(config.detect1.piece_mode, 1)
        self.assertEqual(config.detect1.short_edge_merge_mm, 20.0)
        self.assertEqual(config.detect1.minimum_physical_edge_mm, 20.0)
        self.assertEqual(config.detect1.minimum_corner_turn_deg, 12.0)
        self.assertEqual(config.detect1.minimum_corner_depth_mm, 4.0)
        self.assertEqual(config.detect1.minimum_polygon_iou, 0.85)
        self.assertEqual(config.options.minimum_stable_frames, 5)

    def test_task2_specific_environment_overrides_json_mode(self) -> None:
        """Task2 专属环境变量应覆盖 JSON，Task1 旧变量不会泄漏。"""

        config = load_task2_config(
            environ={
                **TEST_ENV,
                "DETECT1_PIECE_MODE": "2",
            },
            env_path=None,
        )

        self.assertEqual(config.detect1.piece_mode, 1)
        config = load_task2_config(
            environ={
                **TEST_ENV,
                "TASK2_DETECT1_PIECE_MODE": "2",
            },
            env_path=None,
        )

        self.assertEqual(config.detect1.piece_mode, 2)

    def test_json_configures_edge_thresholds_independently(self) -> None:
        """Task2 应从自己的 JSON 读取两项边长阈值。"""

        raw = json.loads(DEFAULT_TASK2_CONFIG_PATH.read_text(encoding="utf-8"))
        raw["detect1"]["short_edge_merge_mm"] = 10.0
        raw["detect1"]["minimum_physical_edge_mm"] = 5.0
        with TemporaryDirectory() as directory:
            path = Path(directory) / "task2.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            config = load_task2_config(
                path,
                environ=TEST_ENV,
                env_path=None,
            )

        self.assertEqual(config.detect1.short_edge_merge_mm, 10.0)
        self.assertEqual(config.detect1.minimum_physical_edge_mm, 5.0)

    def test_fixed_mode_and_unknown_field_are_rejected(self) -> None:
        """Task2 必须拒绝固定模板和不受支持的 JSON 字段。"""

        raw = json.loads(DEFAULT_TASK2_CONFIG_PATH.read_text(encoding="utf-8"))
        with TemporaryDirectory() as directory:
            path = Path(directory) / "task2.json"
            raw["planning"]["mode"] = "fixed"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "random"):
                load_task2_config(path, environ=TEST_ENV, env_path=None)

            raw["planning"]["mode"] = "random"
            raw["detect1"]["unexpected"] = 1
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "未知字段"):
                load_task2_config(path, environ=TEST_ENV, env_path=None)

    def test_runtime_selects_default_task2_json(self) -> None:
        """只设置 EC_TASK=task2 时应推导 Task2 的标准 JSON 路径。"""

        runtime = load_runtime_config(
            env_path=None,
            environ={"EC_TASK": "task2"},
        )

        self.assertEqual(runtime.task_config_path, DEFAULT_TASK2_CONFIG_PATH)


if __name__ == "__main__":
    unittest.main()
