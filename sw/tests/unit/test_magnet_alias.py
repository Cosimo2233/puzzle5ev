"""GPIO 设备别名在两种 gpiod 1.x 接口下都解析为实际字符设备。"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from ec_26_e.actuation import GpiodMagnetOutput, MagnetConfig


@pytest.mark.parametrize('official', [True, False])
def test_gpio_alias_resolved_and_requested_low(official):
    line = Mock()
    chip = Mock()
    chip.get_line.return_value = line

    def open_chip(path):
        if path != str(Path('/dev/gpiochip1')):
            raise OSError('binding requires a gpiochip character device')
        return chip

    if official:
        binding = SimpleNamespace(Chip=open_chip, LINE_REQ_DIR_OUT=3)
    else:
        class Request:
            DIRECTION_OUTPUT = 3
        binding = SimpleNamespace(chip=open_chip, line_request=Request)

    output = GpiodMagnetOutput(MagnetConfig(chip_path=Path('/dev/ec26e-magnet'), line_offset=0))
    with patch.dict('sys.modules', gpiod=binding), \
            patch.object(Path, 'resolve', return_value=Path('/dev/gpiochip1')):
        output.open()
        output.set_enabled(False)
        output.close()

    chip.get_line.assert_called_once_with(0)
    kwargs = line.request.call_args.kwargs
    assert kwargs['default_vals' if official else 'default_val'] == ([0] if official else 0)
    assert all(call.args == (0,) for call in line.set_value.call_args_list)
    line.release.assert_called_once()
