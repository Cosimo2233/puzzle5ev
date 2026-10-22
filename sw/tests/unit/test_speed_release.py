"""Deployment preserves calibrated values and can restore an interrupted install."""
from contextlib import nullcontext
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest


@pytest.fixture
def deployment(tmp_path, monkeypatch):
    script = Path(__file__).resolve().parents[3]/'deploy/axu5ev/install_speed_release.py'
    spec = importlib.util.spec_from_file_location('speed_release', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    config, site, release = tmp_path/'config', tmp_path/'site', tmp_path/'release'
    (config/'tasks').mkdir(parents=True)
    (tmp_path/'install').mkdir()
    release.mkdir()
    (tmp_path/'install/start_menu.sh').write_text('old launcher\n')
    (config/'menu.json').write_text(json.dumps(dict(board='axu5ev', motion_enabled=True,
        plan_only=False, calibration_profile_accepted=True, wiring_and_calibration_verified=True)))
    (config/'machine.env').write_text('MOTION_A4_ORIGIN_X_MM=18.5\nEC_MOTION_ENABLED=false\nTASK1_REVIEW_CYCLE_SECONDS=15\n')
    for mode in ('fixed', 'random'):
        (config/'tasks'/f'{mode}.json').write_text(json.dumps(dict(task={}, detect1={}, planning={},
            resize={'reference_corners_relative': [[.2, .1], [.8, .1], [.8, .9], [.2, .9]]})))
    for name in module.PACKAGES:
        (site/name).mkdir(parents=True)
        (site/name/'original').write_text(name)
    artifacts = {}
    for name in ('ec_26_e-0.1.0-py3-none-any.whl', 'puzzle_solver-0.1.0-py3-none-any.whl', 'start_menu.sh'):
        (release/name).write_text('new '+name)
        artifacts[name] = hashlib.sha256((release/name).read_bytes()).hexdigest()
    (release/'manifest.json').write_text(json.dumps(dict(artifacts=artifacts)))
    monkeypatch.setattr(module, 'BASE', tmp_path)
    monkeypatch.setattr(module, 'CONFIG', config)
    monkeypatch.setattr(module.sysconfig, 'get_path', lambda _: str(site))
    monkeypatch.setattr(module, 'TaskExecutionLock', nullcontext)
    monkeypatch.setattr(module, 'stop_service', Mock(return_value=123))
    monkeypatch.setattr(module, 'start_service', Mock())
    monkeypatch.setattr(module.subprocess, 'run', Mock())
    monkeypatch.setattr(sys, 'argv', ['install', '--release', str(release), '--apply'])
    return module, config, release, site


def test_install_preserves_calibration_and_motion_then_restores_exact_config(deployment, monkeypatch):
    module, config, release, site = deployment
    original = {p.relative_to(config): p.read_bytes() for p in config.rglob('*') if p.is_file()}
    module.main()
    env = (config/'machine.env').read_text()
    assert 'MOTION_A4_ORIGIN_X_MM=18.5\n' in env and 'EC_MOTION_ENABLED=false\n' in env
    menu = json.loads((config/'menu.json').read_text())
    assert menu['motion_enabled'] and menu['wiring_and_calibration_verified'] and menu['warm_tasks']
    for mode in ('fixed', 'random'):
        task = json.loads((config/'tasks'/f'{mode}.json').read_text())
        assert task['resize'] == json.loads(original[Path('tasks')/f'{mode}.json'])['resize']
    monkeypatch.setattr(sys, 'argv', ['install', '--release', str(release), '--apply', '--rollback'])
    module.main()
    assert all((config/name).read_bytes() == data for name, data in original.items())
    assert (module.BASE/'install/start_menu.sh').read_text() == 'old launcher\n'


def test_install_failure_restores_old_packages_and_configuration(deployment):
    module, config, release, site = deployment
    original = (config/'menu.json').read_bytes()
    module.subprocess.run.side_effect = RuntimeError('simulated install failure')
    with pytest.raises(RuntimeError, match='simulated install failure'):
        module.main()
    assert (config/'menu.json').read_bytes() == original
    assert all((site/name/'original').read_text() == name for name in module.PACKAGES)
    module.start_service.assert_called_once_with(123)
