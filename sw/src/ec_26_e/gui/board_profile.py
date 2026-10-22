"""菜单、相机子进程共用的板级选择；旧板默认行为保持兼容。"""
from __future__ import annotations

import json
import os
from pathlib import Path


def load_menu_config() -> dict:
    path = Path(os.environ.get('EC_MENU_CONFIG', '/opt/ec26e/config/axu5ev/menu.json'))
    if path.exists():
        config = json.loads(path.read_text(encoding='utf-8'))
    elif 'EC_MENU_CONFIG' in os.environ:
        raise FileNotFoundError(f'指定的菜单配置不存在：{path}')
    else:
        config = {'board': 'axu5ev', 'plan_only': True, 'motion_enabled': False,
                  'physical_buttons': False, 'preprocess_backend': 'cpu'}
    validate_menu_config(config)
    return config


def validate_menu_config(config: dict) -> None:
    if not isinstance(config, dict):
        raise ValueError('菜单配置必须为 JSON 对象')
    if config.get('board', 'zynq7020') not in ('zynq7020', 'axu5ev'):
        raise ValueError('未知板型')
    if preview_backend(config) not in ('cpu', 'pl'):
        raise ValueError('preprocess_backend 必须为 cpu 或 pl')
    for name in ('plan_only', 'motion_enabled', 'physical_buttons', 'warm_tasks'):
        if name in config and type(config[name]) is not bool:
            raise ValueError(f'{name} 必须为布尔值')
    if config.get('plan_only') and config.get('motion_enabled'):
        raise ValueError('只求解模式不能同时启用运动')
    if config.get('board') == 'axu5ev':
        if config.get('motion_enabled'):
            if config.get('plan_only') is not False or config.get('wiring_and_calibration_verified') is not True:
                raise ValueError('AXU5EV 运动需要明确关闭仅规划并确认接线与标定')
        if config.get('physical_buttons'):
            raise ValueError('AXU5EV 实体按键尚未适配，请使用键鼠')


def preview_backend(config: dict) -> str:
    return config.get('preprocess_backend',
                      'cpu' if config.get('board') == 'axu5ev' else 'pl')


def physical_buttons_enabled(config: dict) -> bool:
    return config.get('physical_buttons', config.get('board', 'zynq7020') == 'zynq7020')


def profile_paths(config: dict) -> tuple[Path, Path]:
    return (Path(config.get('machine_env', '/opt/ec26e/config/machine.env')),
            Path(config.get('task_config_dir', '/opt/ec26e/config/tasks')))
