from ec_26_e.config.task1 import _actuation_from_env, _camera_from_env


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
