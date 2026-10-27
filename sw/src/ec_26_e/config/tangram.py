"""七巧板独立现场配置；复用公共机器配置，不继承旧任务运动授权。"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
import json
import os
from pathlib import Path

from puzzle_solver import TangramPlanningOptions
from ec_26_e.config.env import load_env_file
from ec_26_e.config.task2 import Task2Config, load_task2_config


@dataclass(frozen=True)
class TangramConfig:
    base: Task2Config
    planning: TangramPlanningOptions
    motion_verified: bool = False
    acquisition_timeout_seconds: float = 30.
    stable_frames: int = 1
    stability_tolerance_mm: float = .8

    @property
    def camera(self): return self.base.camera
    @property
    def resize(self): return self.base.resize
    @property
    def detect1(self):
        return replace(self.base.detect1, expected_pieces=7, preprocess_backend='cpu',
            piece_mode=1, min_vertices=3, max_vertices=4, max_pieces=7,
            short_edge_merge_mm=12.,minimum_physical_edge_mm=12.,
            minimum_corner_depth_mm=3., min_piece_area_ratio=.004,
            polygon_epsilon_ratio=.018)
    @property
    def actuation(self): return self.base.actuation

    def __post_init__(self):
        import math
        if not isinstance(self.base, Task2Config) or not isinstance(self.planning,TangramPlanningOptions):
            raise TypeError('七巧板需要 Task2Config 机器参数及 TangramPlanningOptions')
        if type(self.motion_verified) is not bool:
            raise ValueError('motion_verified 必须为布尔值')
        if type(self.stable_frames) is not int or self.stable_frames < 1:
            raise ValueError('stable_frames 必须为正整数；1 表示单张采图')
        for value in (self.acquisition_timeout_seconds,self.stability_tolerance_mm):
            if isinstance(value,bool) or not math.isfinite(value) or value <= 0:
                raise ValueError('采集超时和稳定阈值必须为正有限数')


def load_tangram_config(path: Path, *, env_path: Path | None = None,
                        environ: Mapping[str,str] | None = None) -> TangramConfig:
    from ec_26_e.config.task1 import _reject_duplicate_keys, _reject_nonfinite_constant
    path = Path(path)
    raw = json.loads(path.read_text(encoding='utf-8-sig'),object_pairs_hook=_reject_duplicate_keys,
                     parse_constant=_reject_nonfinite_constant)
    allowed = {'schema_version','base_profile','planning','motion_verified','acquisition_timeout_seconds',
               'stable_frames','stability_tolerance_mm','actuation_overrides'}
    if not isinstance(raw,dict) or set(raw)-allowed or raw.get('schema_version') != 1:
        raise ValueError('七巧板配置格式错误或包含未知字段')
    values = dict(os.environ if environ is None else environ)
    if env_path is not None:
        load_env_file(env_path,environ=values,override=True)
    # 旧菜单可能有 auto/4 等模式变量；七巧板检测独立固定为七片。
    values['EC_EXPECTED_PIECES'] = 'auto'
    values['EC_PREPROCESS_BACKEND'] = 'cpu'
    base = load_task2_config(path.parent/raw.get('base_profile','random.json'),env_path=None,environ=values)
    overrides = raw.get('actuation_overrides',{})
    allowed_overrides = {'z_boot_degrees','z_up_degrees','z_down_degrees','z_speed_level',
                         'a4_origin_x_mm','a4_origin_y_mm','magnet_attach_wait_seconds','magnet_release_wait_seconds'}
    if not isinstance(overrides,dict) or set(overrides)-allowed_overrides:
        raise ValueError('七巧板运动覆盖参数不受支持')
    base = replace(base,actuation=replace(base.actuation,**overrides))
    planning = dict(raw.get('planning',{}))
    for key in ('source_bounds_mm','target_bounds_mm','start_xy_a4_mm'):
        if key in planning: planning[key] = tuple(planning[key])
    if 'EC_TANGRAM_PATTERN' in values:
        planning['pattern_id'] = values['EC_TANGRAM_PATTERN']
    return TangramConfig(base,TangramPlanningOptions(**planning),
        **{k:raw[k] for k in ('motion_verified','acquisition_timeout_seconds','stable_frames',
                             'stability_tolerance_mm') if k in raw})
