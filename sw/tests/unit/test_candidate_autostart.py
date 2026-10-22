"""硬件版本只在显式选择后切换，旧部署默认仍可启动。"""
import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock, patch
import pytest


def load():
    source = Path(__file__).resolve().parents[3]/'deploy/axu5ev/autostart.py'
    spec = importlib.util.spec_from_file_location('candidate_autostart', source)
    module = importlib.util.module_from_spec(spec)
    with patch.dict('sys.modules', {'fcntl': Mock()}):
        spec.loader.exec_module(module)
    return module


def test_legacy_and_explicit_selection(tmp_path):
    module = load()
    config = tmp_path/'hardware.json'
    assert module.hardware_selection(config, tmp_path) == (module.BUNDLE, 'cpu')
    bundle = tmp_path/'bundle'
    bundle.mkdir()
    (bundle/'manifest.json').write_text('{}')
    config.write_text(json.dumps(dict(bundle=str(bundle), candidate_backend='pl')))
    assert module.hardware_selection(config, tmp_path) == (bundle.resolve(), 'pl')
    config.write_text(json.dumps(dict(bundle=str(bundle), candidate_backend='typo')))
    with pytest.raises(ValueError): module.hardware_selection(config, tmp_path)
    config.write_text(json.dumps(dict(bundle=str(tmp_path.parent))))
    with pytest.raises(ValueError): module.hardware_selection(config, tmp_path)
