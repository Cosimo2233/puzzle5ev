"""Hardware-loader transaction contracts; real-board acceptance is separate."""
import ctypes
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


@pytest.fixture
def loader(monkeypatch):
    if sys.platform == 'win32':
        monkeypatch.setitem(sys.modules, 'fcntl', SimpleNamespace(ioctl=None))
    source = Path(__file__).resolve().parents[3] / 'deploy/axu5ev/load_hardware.py'
    spec = importlib.util.spec_from_file_location('axu5ev_loader', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_register_read_uses_repeated_start(loader, monkeypatch):
    def ioctl(fd, request, transfer):
        assert (fd, request, transfer.count) == (7, 0x707, 2)
        tx, rx = transfer.messages[0], transfer.messages[1]
        assert (tx.addr, tx.flags, tx.length) == (0x39, 0, 1)
        assert (rx.addr, rx.flags, rx.length) == (0x39, 1, 1)
        assert ctypes.string_at(tx.buffer, 1) == b'\x98'
        ctypes.memmove(rx.buffer, b'\x03', 1)
        return 2
    monkeypatch.setattr(loader.fcntl, 'ioctl', ioctl)
    assert loader.read_i2c_register(7, 0x39, 0x98) == 3


def test_register_read_propagates_bus_failure(loader, monkeypatch):
    def failed(*args):
        raise OSError('I2C NACK')
    monkeypatch.setattr(loader.fcntl, 'ioctl', failed)
    with pytest.raises(OSError, match='NACK'):
        loader.read_i2c_register(7, 0x39, 0x98)
