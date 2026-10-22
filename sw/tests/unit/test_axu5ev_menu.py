"""新板菜单：默认只求解，明确授权后通过 M0 执行；不自动运行。"""
import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from ec_26_e.gui.board_menu import BoardMenu
from ec_26_e.gui.board_profile import (
    load_menu_config, physical_buttons_enabled, preview_backend, validate_menu_config,
)
from ec_26_e.gui.menu_model import MenuModel


def test_axu5ev_defaults_never_export_old_gpio_or_require_pl():
    assert not physical_buttons_enabled({'board': 'axu5ev'})
    assert preview_backend({'board': 'axu5ev'}) == 'cpu'
    assert physical_buttons_enabled({})
    assert preview_backend({}) == 'pl'


@pytest.mark.parametrize('extra', [
    {'motion_enabled': True}, {'physical_buttons': True}, {'plan_only': 'true'},
    {'preprocess_backend': 'unknown'},
])
def test_invalid_new_board_config_fails_before_hardware_access(extra):
    with pytest.raises(ValueError):
        validate_menu_config(dict(board='axu5ev', **extra))


def test_preview_reads_same_profile_as_menu(tmp_path, monkeypatch):
    path = tmp_path / 'menu.json'
    config = {'board': 'axu5ev', 'machine_env': '/config/new.env', 'plan_only': True}
    path.write_text(json.dumps(config), encoding='utf-8')
    monkeypatch.setenv('EC_MENU_CONFIG', str(path))
    assert load_menu_config() == config


def test_missing_explicit_profile_does_not_fall_back_to_old_board(tmp_path, monkeypatch):
    monkeypatch.setenv('EC_MENU_CONFIG', str(tmp_path / 'absent.json'))
    with pytest.raises(FileNotFoundError): load_menu_config()


def test_plan_only_dispatch_cannot_launch_motor_entry(tmp_path):
    (tmp_path / 'machine.env').touch()
    (tmp_path / 'random.json').write_text('{}')
    menu = BoardMenu.__new__(BoardMenu)
    menu.config = dict(board='axu5ev', plan_only=True, motion_enabled=False,
                       machine_env=str(tmp_path / 'machine.env'), task_config_dir=str(tmp_path))
    with patch('ec_26_e.gui.board_menu.subprocess.Popen') as spawn, \
         patch('ec_26_e.gui.board_menu.importlib.util.find_spec', side_effect=AssertionError('PL dependency')):
        menu.start_task('随机拼图')
    args = spawn.call_args.args[0]
    assert 'ec_26_e.commands.app' in args and 'plan-live' in args
    assert args[args.index('--backend') + 1] == 'cpu'
    assert args[args.index('--mode') + 1] == 'random'
    assert spawn.call_args.kwargs['env']['EC_MOTION_ENABLED'] == 'false'
    assert '--task' not in args


def test_plan_only_model_can_solve_without_motion_authorization():
    process = Mock()
    process.poll.return_value = 0
    model = MenuModel(plan_only=True, start_task=Mock(return_value=process))
    model.action('confirm')
    assert model.busy and not model.motion_enabled
    model.poll_task()
    assert '未执行运动' in model.message


def test_motion_enabled_model_waits_for_user_confirm():
    spawn = Mock()
    model = MenuModel(motion_enabled=True, start_task=spawn)
    model.poll_task()
    model.action('mode')
    model.action('preview')
    spawn.assert_not_called()


@pytest.mark.parametrize('bridge', ['/dev/ec26e-m0-bridge', None])
def test_authorized_axu5ev_dispatch_only_uses_m0(tmp_path, bridge):
    menu = BoardMenu.__new__(BoardMenu)
    menu.config = dict(board='axu5ev', plan_only=False, motion_enabled=True,
        wiring_and_calibration_verified=True, machine_env=str(tmp_path/'machine.env'),
        task_config_dir=str(tmp_path))
    profile = SimpleNamespace(actuation=SimpleNamespace(bridge_port=bridge))
    with patch('ec_26_e.gui.controller.load_profile', return_value=profile), \
         patch.object(Path, 'exists', return_value=True), \
         patch('ec_26_e.gui.board_menu.subprocess.Popen') as spawn:
        if bridge is None:
            with pytest.raises(RuntimeError, match='M0'):
                menu.start_task('固定四片')
            spawn.assert_not_called()
        else:
            menu.start_task('固定四片')
            args = spawn.call_args.args[0]
            assert 'ec_26_e' in args and 'task1' in args and 'plan-live' not in args
            assert spawn.call_args.kwargs['env']['EC_MOTION_ENABLED'] == 'true'


def test_axu5ev_motion_rejects_unconfirmed_calibration():
    with pytest.raises(ValueError, match='接线与标定'):
        validate_menu_config(dict(board='axu5ev', motion_enabled=True, plan_only=False,
                                  wiring_and_calibration_verified=False))


@pytest.mark.parametrize('fail', [False, True])
def test_live_plan_writes_truth_and_never_constructs_actuator(tmp_path, fail):
    from ec_26_e.gui import live_plan
    output = tmp_path / 'result.json'
    output.write_text('{"success": true, "plan": "stale"}')
    config = SimpleNamespace(camera=object(), resize=object(), detect1=object(), options=object())
    plan = Mock(to_dict=Mock(return_value={'moves': []}))
    with patch.object(live_plan, 'TaskExecutionLock', return_value=nullcontext()), \
         patch.object(live_plan, 'load_profile', return_value=config), \
         patch.object(live_plan, 'create_production_detector', return_value=nullcontext(Mock())), \
         patch.object(live_plan, 'FFmpegCamera'), patch.object(live_plan, 'Resize'), \
         patch.object(live_plan, 'run_task1', return_value=plan,
                      side_effect=RuntimeError('相机断开') if fail else None), \
         patch('ec_26_e.tasks.task1.application.MovePlanActuator', side_effect=AssertionError('motor access')):
        if fail:
            with pytest.raises(RuntimeError, match='相机断开'):
                live_plan.run_live_plan(Path('a'), Path('b'), 'fixed', 'cpu', output)
        else:
            live_plan.run_live_plan(Path('a'), Path('b'), 'fixed', 'cpu', output)
    result = json.loads(output.read_text(encoding='utf-8'))
    assert result['success'] is not fail
    assert result['motion_executed'] is False
    assert result['temporal_stability_validated'] is not fail
    if fail:
        assert 'plan' not in result and result['state'] == 'failed'
