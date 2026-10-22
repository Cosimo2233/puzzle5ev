"""验证下次启动选择与回滚；不加载任何真实 PL。"""
from contextlib import nullcontext
import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def selection(tmp_path, monkeypatch):
    source = Path(__file__).resolve().parents[3]/'deploy/axu5ev/select_candidate_hardware.py'
    spec = importlib.util.spec_from_file_location('candidate_selection', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'BASE', tmp_path)
    monkeypatch.setattr(module, 'TaskExecutionLock', nullcontext)
    menu = tmp_path/'menu-status.json'
    menu.write_text('{}')
    monkeypatch.setattr(module, 'Path', lambda value: menu if value == '/run/ec26e/menu-status.json' else Path(value))
    release = tmp_path/'releases/candidate'
    hardware = release/'hardware'
    hardware.mkdir(parents=True)
    (hardware/'load_hardware.py').write_text('def verify_bundle(path): pass\ndef check_base(): pass\n')
    (hardware/'puzzle5ev.hwh').write_text('puzzle_candidate_scan_0')
    (hardware/'puzzle5ev.bit').write_bytes(b'test-bit')
    (release/'autostart.py').write_bytes(b'new-supervisor')
    supervisor = tmp_path/'install/autostart.py'
    supervisor.parent.mkdir()
    supervisor.write_bytes(b'original-supervisor')
    config = tmp_path/'config/axu5ev/hardware.json'
    config.parent.mkdir(parents=True)
    return module, release, config, supervisor, menu


@pytest.mark.parametrize('existing', [False, True])
def test_selection_does_not_change_running_hardware_and_restores_prior_files(selection, existing):
    module, release, config, supervisor, _ = selection
    if existing:
        config.write_bytes(b'{"bundle":"old"}\n')
    module.main(release=release, apply=False, rollback=False)
    assert supervisor.read_bytes() == b'original-supervisor'
    module.main(release=release, apply=True, rollback=False)
    assert json.loads(config.read_text())['candidate_backend'] == 'pl'
    assert supervisor.read_bytes() == b'new-supervisor'
    module.main(release=release, apply=True, rollback=True)
    assert supervisor.read_bytes() == b'original-supervisor'
    assert config.exists() is existing
    if existing:
        assert config.read_bytes() == b'{"bundle":"old"}\n'


def test_busy_and_later_manual_changes_are_preserved(selection):
    module, release, config, supervisor, menu = selection
    menu.write_text('{"busy":true}')
    with pytest.raises(RuntimeError): module.main(release=release, apply=True, rollback=False)
    assert not (release/'hardware-selection-before').exists()
    menu.write_text('{}')
    module.main(release=release, apply=True, rollback=False)
    supervisor.write_bytes(b'manual-update')
    with pytest.raises(RuntimeError): module.main(release=release, apply=True, rollback=True)
    assert supervisor.read_bytes() == b'manual-update'
    config.write_bytes(b'{"bundle":"manual"}')
    with pytest.raises(RuntimeError): module.main(release=release, apply=True, rollback=True)
    assert config.read_bytes() == b'{"bundle":"manual"}'
