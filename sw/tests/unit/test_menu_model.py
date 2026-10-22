from unittest.mock import Mock
from unittest.mock import patch
import signal
import json
import pytest

from ec_26_e.gui.menu_model import MenuModel, MODES, PressEdges


def test_mode_cycle_and_confirm_motion_guard():
    start = Mock()
    model = MenuModel(start_task=start)
    seen = []
    for _ in MODES:
        seen.append(model.label)
        model.action('mode')
    assert seen == list(MODES)
    model.action('confirm')
    start.assert_not_called()
    assert '运动未启用' in model.message


def test_cancel_waits_for_task_cleanup_and_keeps_menu_available():
    process = Mock()
    process.poll.return_value = None
    model = MenuModel(motion_enabled=True, start_task=Mock(return_value=process))
    model.action('confirm')
    selected = model.label
    model.action('mode')
    assert model.label == selected
    model.action('cancel')
    model.action('cancel')
    process.send_signal.assert_called_once_with(signal.SIGINT)
    model.poll_task()
    assert model.busy
    process.poll.return_value = 130
    model.poll_task()
    assert not model.busy
    assert '已取消' in model.message
    model.action('preview')
    assert model.preview
    model.action('cancel')
    assert not model.preview


def test_buttons_ignore_boot_hold_bounce_and_long_hold():
    edges = PressEdges(['mode'])
    assert edges.update({'mode': True}, 0) == []
    assert edges.update({'mode': True}, .05) == []
    edges.update({'mode': False}, .1)
    edges.update({'mode': False}, .15)
    edges.update({'mode': True}, .2)
    edges.update({'mode': False}, .22)
    edges.update({'mode': True}, .23)
    assert edges.update({'mode': True}, .28) == ['mode']
    assert edges.update({'mode': True}, 10) == []


def test_simultaneous_confirm_never_starts_task_and_cancel_wins():
    edges = PressEdges(['confirm', 'mode', 'cancel'])
    def samples(*pressed):
        return {name: name in pressed for name in edges.state}
    edges.update(samples(), 0)
    edges.update(samples(), .05)
    edges.update(samples('confirm', 'mode'), .1)
    assert edges.update(samples('confirm', 'mode'), .15) == []
    edges.update(samples(), .2)
    edges.update(samples(), .25)
    edges.update(samples('confirm', 'cancel'), .3)
    assert edges.update(samples('confirm', 'cancel'), .35) == ['cancel']


@pytest.mark.parametrize('pl,verified,message', [
    ({'variant': 'preview', 'motion_enabled': False}, True, '预览硬件'),
    ({'variant': 'motion', 'motion_enabled': True}, False, '接线和标定'),
])
def test_formal_task_rejects_wrong_hardware_or_unverified_machine(pl, verified, message):
    from ec_26_e.gui.board_menu import BoardMenu
    menu = BoardMenu.__new__(BoardMenu)
    menu.config = {'wiring_and_calibration_verified': verified}
    with patch('pathlib.Path.read_text', return_value=json.dumps(pl)), \
         patch('ec_26_e.gui.board_menu.subprocess.Popen') as spawn:
        with pytest.raises(RuntimeError, match=message):
            menu.start_task('固定四片')
        spawn.assert_not_called()


def test_formal_random_task_dispatch_preserves_addressed_motor_project_entry():
    from ec_26_e.gui.board_menu import BoardMenu
    menu = BoardMenu.__new__(BoardMenu)
    menu.config = {'wiring_and_calibration_verified': True}
    with patch('pathlib.Path.read_text', return_value=json.dumps({'variant': 'motion', 'motion_enabled': True})), \
         patch('pathlib.Path.is_file', return_value=True), \
         patch('ec_26_e.gui.board_menu.importlib.util.find_spec', return_value=object()), \
         patch('ec_26_e.gui.board_menu.subprocess.Popen') as spawn:
        menu.start_task('随机拼图')
        command = spawn.call_args.args[0]
        assert command[command.index('--task')+1] == 'task2'
        assert command[command.index('--task-config')+1].endswith('random.json')
        assert spawn.call_args.kwargs['env']['EC_EXPECTED_PIECES'] == 'auto'
        assert spawn.call_args.kwargs['env']['EC_DRAW_ENABLED'] == 'false'


def test_imported_profile_can_start_with_loader_motion_authorization_false():
    from ec_26_e.gui.board_menu import BoardMenu
    menu = BoardMenu.__new__(BoardMenu)
    menu.config = {'calibration_profile_accepted': True,
                   'wiring_and_calibration_verified': False}
    with patch('pathlib.Path.read_text', return_value=json.dumps({'variant': 'motion', 'motion_enabled': False})), \
         patch('pathlib.Path.is_file', return_value=True), \
         patch('ec_26_e.gui.board_menu.importlib.util.find_spec', return_value=object()), \
         patch('ec_26_e.gui.board_menu.subprocess.Popen') as spawn:
        menu.start_task('固定四片')
        spawn.assert_called_once()


def test_missing_solver_blocks_process_before_any_motor_access():
    from ec_26_e.gui.board_menu import BoardMenu
    menu = BoardMenu.__new__(BoardMenu)
    menu.config = {'calibration_profile_accepted': True}
    with patch('pathlib.Path.read_text', return_value=json.dumps({'variant': 'motion'})), \
         patch('ec_26_e.gui.board_menu.importlib.util.find_spec',
               side_effect=lambda name: None if name == 'puzzle_solver' else object()), \
         patch('ec_26_e.gui.board_menu.subprocess.Popen') as spawn:
        with pytest.raises(RuntimeError, match='puzzle_solver'):
            menu.start_task('固定四片')
        spawn.assert_not_called()
