from pathlib import Path
from ec_26_e.config import load_task1_config, load_task2_config

def test_profile_passes_frozen_env_into_detector():
    config=load_task2_config(Path('configs/zynq7020/random.json'),env_path=None,environ={
        'MOTION_MAGNET_GPIO_LINE':'0','EC_EXPECTED_PIECES':'2','EC_PREPROCESS_BACKEND':'pl',
        'EC_PL_BITSTREAM':'/opt/test.bit','EC_BLUE_RATIO_Q8':'320','EC_BRIGHTNESS_MIN':'0',
        'CAMERA_WIDTH':'1280','CAMERA_HEIGHT':'720'})
    assert config.detect1.expected_pieces==2
    assert config.detect1.preprocess_backend=='pl'
    assert config.detect1.pl_bitstream=='/opt/test.bit'
    assert config.detect1.blue_ratio_q8==320
    assert config.options.planning_options.mode=='random'

def test_board_template_loads_without_touching_devices():
    config=load_task1_config(Path('configs/zynq7020/fixed.json'),env_path=Path('../deploy/zynq7020/board.env.example'),environ={})
    assert config.detect1.preprocess_backend=='pl'
    assert config.detect1.expected_pieces==4
    assert config.options.acquisition_timeout_seconds==120
    assert config.actuation.magnet.line_offset==0
