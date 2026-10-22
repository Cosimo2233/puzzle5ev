"""Validate the generated boot script, its integrity and factory escape path."""
import importlib.util
from pathlib import Path
import struct
import unittest
import zlib
from unittest.mock import Mock, patch

path = Path(__file__).resolve().parents[3]/'scripts/axu5ev/prepare_autostart.py'
spec = importlib.util.spec_from_file_location('prepare_autostart', path)
boot = importlib.util.module_from_spec(spec); spec.loader.exec_module(boot)


def original_script():
    text = b'echo factory\nfatload mmc 1:1 0x10000000 image.ub\nbootm 0x10000000\n'
    data = struct.pack('>II', len(text), 0)+text
    fields = [0x27051956, 0, 0, len(data), 0, 0, zlib.crc32(data), 5, 2, 6, 0, b'factory']
    fields[1] = zlib.crc32(boot.HEADER.pack(*fields))
    return boot.HEADER.pack(*fields)+data, text


class AutostartBootTests(unittest.TestCase):
    def test_preserves_original_fallback_and_gates_candidate(self):
        source, original = original_script()
        output = boot.prepare(source)
        text = boot.unpack_script(output)
        self.assertTrue(text.endswith(original))
        self.assertIn(b'if test -e mmc 1:1 /puzzle5ev-autostart.enable;', text)
        self.assertIn(b'x11=false', text)
        self.assertIn(b'setenv bootargs "${ec26e_saved_bootargs}"', text)
        self.assertNotIn(b'saveenv', text)
        with self.assertRaisesRegex(ValueError, 'Already'):
            boot.prepare(output)

    def test_rejects_corruption_and_truncation(self):
        source, _ = original_script()
        for offset in (4, 64, len(source)-1):
            bad = bytearray(source); bad[offset] ^= 1
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                boot.prepare(bytes(bad))
        with self.assertRaises(ValueError):
            boot.prepare(source[:30])

    def test_network_selection_uses_controller_identity(self):
        source = path.parents[2]/'deploy/axu5ev/autostart.py'
        spec = importlib.util.spec_from_file_location('ec26e_autostart', source)
        module = importlib.util.module_from_spec(spec)
        with patch.dict('sys.modules', {'fcntl': Mock()}):
            spec.loader.exec_module(module)

        class Interface:
            def __init__(self, name, device):
                self.name, self.device = name, device
            def __truediv__(self, child):
                assert child == 'device'
                result = Mock()
                result.resolve.return_value = Path('/sys/devices')/self.device
                return result

        root = Mock()
        for name in ('eth0', 'eth1'):
            root.iterdir.return_value = [Interface('lo', 'lo'), Interface(name, 'ff0e0000.ethernet')]
            self.assertEqual(module.ps_network_command(root), ['ifup', name])
        for interfaces in ([], [Interface('eth0', 'ff0e0000.ethernet'), Interface('eth1', 'ff0e0000.ethernet')]):
            root.iterdir.return_value = interfaces
            with self.assertRaises(RuntimeError):
                module.ps_network_command(root)
