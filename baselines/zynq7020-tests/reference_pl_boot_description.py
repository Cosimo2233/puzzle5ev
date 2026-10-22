"""核验启动前的硬件身份检查会拒绝错误类型、方向和吸合复位值。"""
import importlib.util
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest


source = Path(__file__).resolve().parents[3] / 'deploy/zynq7020/load_pl.py'
spec = importlib.util.spec_from_file_location('load_pl', source)
loader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(loader)


def hardware(tmp_path, *, ports=True, reset='0x00000000', baud='115200', wrong_direction=False):
    root = ET.Element('EDKSYSTEM')
    external = ET.SubElement(root, 'EXTERNALPORTS')
    if ports:
        for name, direction in loader.MOTION_PORTS.items():
            ET.SubElement(external, 'PORT', NAME=name, DIR='I' if wrong_direction else direction)
    modules = ET.SubElement(root, 'MODULES')
    values = {'magnet_gpio': {'C_ALL_OUTPUTS': '1', 'C_DOUT_DEFAULT': reset},
              'motion_uart': {'C_BAUDRATE': baud, 'C_DATA_BITS': '8', 'C_USE_PARITY': '0'},
              'rotation_uart': {'C_BAUDRATE': baud, 'C_DATA_BITS': '8', 'C_USE_PARITY': '0'}}
    for name, parameters in values.items():
        params = ET.SubElement(ET.SubElement(modules, 'MODULE', INSTANCE=name), 'PARAMETERS')
        for key, value in parameters.items():
            ET.SubElement(params, 'PARAMETER', NAME=key, VALUE=value)
    path = tmp_path / 'system.hwh'
    ET.ElementTree(root).write(path)
    return path


def test_accepts_motion_with_default_magnet_off(tmp_path):
    loader.validate_description(hardware(tmp_path), 'motion')


def test_accepts_preview_without_exported_motion_pins(tmp_path):
    loader.validate_description(hardware(tmp_path, ports=False), 'preview')


@pytest.mark.parametrize('variant,options', [
    ('preview', {}), ('motion', {'ports': False}), ('motion', {'wrong_direction': True}),
    ('motion', {'reset': '0x00000001'}), ('motion', {'baud': '9600'}), ('unknown', {}),
])
def test_rejects_unsafe_or_mismatched_description(tmp_path, variant, options):
    with pytest.raises(RuntimeError):
        loader.validate_description(hardware(tmp_path, **options), variant)


def test_gpio_identity_uses_platform_parent_and_physical_address(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('load_motor_io', source.with_name('load_motor_io.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    bus = tmp_path / 'bus'
    bus.mkdir()
    targets = {}
    for index, address in enumerate((0xE000A000, 0x41200000)):
        platform = tmp_path / str(address)
        (platform / 'of_node').mkdir(parents=True)
        (platform / 'of_node/reg').write_bytes(address.to_bytes(4, 'big') + (0x10000).to_bytes(4, 'big'))
        (platform / ('gpiochip' + str(index))).mkdir()
        link = bus / ('gpiochip' + str(index))
        link.mkdir()
        targets[link] = platform / ('gpiochip' + str(index))
    # 在 Windows 上模拟 Linux sysfs 链接解析，不要求主机创建特权符号链接。
    monkeypatch.setattr(Path, 'resolve', lambda path: targets.get(path, path))
    assert module.magnet_chips(bus) == [bus / 'gpiochip1']
