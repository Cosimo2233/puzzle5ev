"""Preloading never launches a task; one explicit dispatch transfers ownership."""
import io
import json
import sys
from unittest.mock import Mock

import pytest

from ec_26_e.gui import warm_task


def test_idle_worker_exits_on_eof_without_calling_an_application(tmp_path, monkeypatch):
    application = Mock()
    ready = tmp_path/'ready'
    monkeypatch.setattr(warm_task, 'prepare', lambda: {'ec_26_e': application})
    monkeypatch.setattr(sys, 'argv', ['warm_task', str(ready)])
    monkeypatch.setattr(sys, 'stdin', io.StringIO(''))
    warm_task.main()
    application.assert_not_called()
    assert not ready.exists()


def test_explicit_request_runs_once_with_current_environment(monkeypatch):
    application = Mock()
    monkeypatch.setenv('EC_MOTION_ENABLED', 'false')
    request = dict(module='ec_26_e', args=['--task', 'task1'], environment={'EC_MOTION_ENABLED': 'true'})
    warm_task.execute_request(request, {'ec_26_e': application})
    application.assert_called_once_with(args=['--task', 'task1'], prog_name='ec_26_e')
    assert warm_task.os.environ['EC_MOTION_ENABLED'] == 'true'


def test_slot_does_not_stop_a_dispatched_task(monkeypatch):
    process = Mock()
    process.poll.return_value = None
    monkeypatch.setattr(warm_task.subprocess, 'Popen', Mock(return_value=process))
    slot = warm_task.WarmTaskSlot()
    slot.prime()
    process.stdin.write.assert_not_called()
    result = slot.dispatch([sys.executable, '-u', '-m', 'ec_26_e.commands.app', 'plan-live'],
                           {'EC_MOTION_ENABLED': 'false'})
    payload = json.loads(process.stdin.write.call_args.args[0])
    assert payload['module'] == 'ec_26_e.commands.app'
    assert result is process
    slot.close()
    process.terminate.assert_not_called()


def test_unknown_command_rejected_before_creating_worker(monkeypatch):
    spawn = Mock()
    monkeypatch.setattr(warm_task.subprocess, 'Popen', spawn)
    with pytest.raises(ValueError):
        warm_task.WarmTaskSlot().dispatch([sys.executable, '-u', '-m', 'unknown'], {})
    spawn.assert_not_called()
