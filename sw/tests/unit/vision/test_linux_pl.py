"""Host-side transport contracts; these do not substitute for board DMA tests."""
import hashlib
import json
import mmap
from pathlib import Path
import struct
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from ec_26_e.vision import linux_pl


def test_uio_bulk_transfer_uses_aligned_words():
    words, writes = {i * 4: i for i in range(8)}, []
    def write(offset, value):
        writes.append((offset, value))
        words[offset] = value
    array = linux_pl.UioWordArray(SimpleNamespace(read=words.__getitem__, write=write), 8)
    array[1:4] = np.array([9, 10, 11], dtype=np.uint32)
    array[4:6] = 0
    assert writes == [(4, 9), (8, 10), (12, 11), (16, 0), (20, 0)]
    assert array[1:6].tolist() == [9, 10, 11, 0, 0]
    assert array[0:0].dtype == np.uint32
    assert array[-1] == 7
    with pytest.raises(IndexError):
        array[8]
    with pytest.raises(ValueError):
        array[1:4] = [1, 2]


def test_hardware_identity_requires_matching_pair(tmp_path, monkeypatch):
    bit = tmp_path / 'puzzle5ev.bit'
    hwh = bit.with_suffix('.hwh')
    bit.write_bytes(b'bit'); hwh.write_bytes(b'hwh')
    status = tmp_path / 'status.json'
    status.write_text(json.dumps({
        'board': 'axu5ev', 'loaded': True,
        'bit_sha256': hashlib.sha256(bit.read_bytes()).hexdigest(),
        'hwh_sha256': hashlib.sha256(hwh.read_bytes()).hexdigest(),
    }))
    monkeypatch.setattr(linux_pl, 'Path', lambda p: status if str(p) == '/run/ec26e/pl-status.json' else Path(p))
    linux_pl.verify_loaded_hardware(bit)
    hwh.write_bytes(b'stale')
    with pytest.raises(RuntimeError): linux_pl.verify_loaded_hardware(bit)
    with pytest.raises(ValueError): linux_pl.verify_loaded_hardware(None)


@pytest.fixture
def transport(monkeypatch):
    closed = []
    mappings = []
    original_mmap = mmap.mmap
    def allocate(fd, size, **kwargs):
        mapping = original_mmap(-1, size)
        mappings.append(mapping)
        return mapping
    monkeypatch.setattr(linux_pl, 'verify_loaded_hardware', lambda p: None)
    monkeypatch.setattr(linux_pl.os, 'O_SYNC', 0x101000, raising=False)
    monkeypatch.setattr(linux_pl.os, 'open', lambda *a: 123)
    monkeypatch.setattr(linux_pl.os, 'close', lambda fd: closed.append(fd))
    monkeypatch.setattr(linux_pl.mmap, 'mmap', allocate)
    return mappings, closed


def test_preprocess_dma_layout_and_copy(transport, monkeypatch):
    mappings, closed = transport
    calls = []
    def ioctl(fd, command, args):
        calls.append((fd, command, struct.unpack('<6I', args)))
        assert mappings[0][:8] == bytes([1, 2, 3, 255, 4, 5, 6, 255])
        mappings[1][:2] = bytes([0, 255])
    monkeypatch.setitem(sys.modules, 'fcntl', SimpleNamespace(ioctl=ioctl))
    device = linux_pl.LinuxPreprocessor('test.bit')
    result = device.process(np.array([[[1, 2, 3], [4, 5, 6]]], dtype=np.uint8), 7)
    assert calls == [(123, 0x40184501, (2, 1, 300, 45, 0, 7))]
    device.close(); device.close()
    assert result.tolist() == [[0, 255]]  # result survives closed coherent mapping
    assert closed == [123]
    assert all(m.closed for m in mappings)


def test_transport_fault_latches_until_reopen(transport, monkeypatch):
    calls = []
    def ioctl(*args):
        calls.append(args)
        raise TimeoutError('DMA timeout')
    monkeypatch.setitem(sys.modules, 'fcntl', SimpleNamespace(ioctl=ioctl))
    device = linux_pl.LinuxPreprocessor('test.bit')
    frame = np.zeros((2, 2, 3), dtype=np.uint8)
    with pytest.raises(TimeoutError): device.process(frame, 1)
    with pytest.raises(RuntimeError): device.process(frame, 2)
    assert len(calls) == 1
    device.close()


def test_fixed_auto_updates_ratio_and_brightness_together_per_frame(transport, monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, 'fcntl', SimpleNamespace(
        ioctl=lambda fd, command, args: calls.append(struct.unpack('<6I', args))))
    device = linux_pl.LinuxPreprocessor('test.bit', linux_pl.PreprocessConfig(blue_profile='fixed_auto'))
    try:
        device.process(np.full((120,120,3), (233,169,45), np.uint8), 1)
        device.process(np.full((120,120,3), (185,163,70), np.uint8), 2)
        with pytest.raises(ValueError, match='蓝底采样不足'):
            device.process(np.full((120,120,3), 230, np.uint8), 3)
        device.process(np.full((120,120,3), (233,169,45), np.uint8), 4)
        assert [(c[2],c[3],c[5]) for c in calls] == [(320,198,1),(275,45,2),(320,198,4)]
    finally:
        device.close()


def test_adaptive_threshold_sent_per_frame_without_poisoning_dma(transport, monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, 'fcntl', SimpleNamespace(
        ioctl=lambda fd, command, args: calls.append(struct.unpack('<6I', args))))
    params = linux_pl.PreprocessConfig(300, 45, 0, 218)
    device = linux_pl.LinuxPreprocessor('test.bit', params)
    try:
        with pytest.raises(ValueError, match='蓝底采样不足'):
            device.process(np.full((120,120,3), 230, np.uint8), 1)
        assert not calls
        device.process(np.full((120,120,3), (233,169,45), np.uint8), 2)
        device.process(np.full((120,120,3), (186,135,36), np.uint8), 3)
        assert [c[3] for c in calls] == [198,158]
        assert device.frame_count == 2
    finally:
        device.close()


def test_partial_allocation_releases_buffers(transport, monkeypatch):
    mappings, closed = transport
    original_array = linux_pl.np.ndarray
    calls = []
    def array(*args, **kwargs):
        calls.append(args)
        if len(calls) == 2: raise MemoryError('RX array')
        return original_array(*args, **kwargs)
    monkeypatch.setattr(linux_pl.np, 'ndarray', array)
    with pytest.raises(MemoryError): linux_pl.LinuxPreprocessor('test.bit')
    assert closed == [123]
    assert all(m.closed for m in mappings)


def test_invalid_input_never_submitted(transport, monkeypatch):
    def ioctl(*args): pytest.fail('invalid input reached DMA')
    monkeypatch.setitem(sys.modules, 'fcntl', SimpleNamespace(ioctl=ioctl))
    device = linux_pl.LinuxPreprocessor('test.bit')
    frame = np.zeros((1, 1, 3), dtype=np.uint8)
    for frame_id in (0, -1, 0xffffffff):
        with pytest.raises(ValueError): device.process(frame, frame_id)
    with pytest.raises(ValueError): device.process(np.zeros((721, 1, 3), dtype=np.uint8), 1)
    assert not device._failed
    device.close()
