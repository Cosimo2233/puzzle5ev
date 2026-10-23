from ec_26_e.config.task1 import _actuation_from_env, _camera_from_env


def test_speed_configuration_cannot_overwrite_vision_and_fixed_patch_is_scoped(monkeypatch):
    import importlib.util
    from pathlib import Path
    scripts = Path(__file__).resolve().parents[3]/'deploy/axu5ev'
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location('capture_settings', scripts/'configure_capture_release.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    original = 'EC_BLUE_RATIO_Q8=320\nEC_BLUE_BACKGROUND_FRACTION_Q8=218\nTASK2_BLUE_RATIO_Q8=310\nFOC_RPM=20\n'
    values = lambda raw: dict(line.split('=', 1) for line in raw.decode().splitlines())
    speed = values(module.patch_environment(original))
    assert (speed['EC_BLUE_RATIO_Q8'], speed['EC_BLUE_BACKGROUND_FRACTION_Q8']) == ('320', '218')
    fixed = values(module.patch_environment(original, module.FIXED_VISION))
    assert fixed == dict(values(original.encode()), TASK1_BLUE_RATIO_Q8='275', TASK1_BLUE_BACKGROUND_FRACTION_Q8='0', TASK1_BLUE_PROFILE='fixed_auto')


def test_board_speed_and_focus_settings_reach_runtime_objects():
    camera = _camera_from_env({'CAMERA_FOCUS_ABSOLUTE': '68'})
    assert camera.focus_absolute == 68
    assert _camera_from_env({}).focus_absolute is None
    config = _actuation_from_env(dict(MOTION_BACKEND='m0',
        MOTION_SERIAL_DEVICE='/dev/ec26e-m0-ch1', MOTION_XY_SPEED_LEVEL='80',
        MOTION_XY_SPEED_MM_PER_SECOND='100', FOC_RPM='20'))
    assert config.xy_speed_level == 80
    assert config.xy_speed_mm_per_second == 100
    assert config.rotation.rpm == 20
    assert config.z_speed_level == 64
    assert config.xy_fixed_wait_seconds == 1.0
