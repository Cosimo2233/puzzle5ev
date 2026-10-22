"""AXU5EV 原生 Linux PL 接口；仅打开启动阶段已验证的设备。"""
from __future__ import annotations

import hashlib
import json
import mmap
import os
from pathlib import Path
import struct
import threading
import time

import numpy as np
import cv2

from ec_26_e.vision._blue import validate_bgr_image
from ec_26_e.vision.preprocess import PreprocessConfig, resolve_blue_min


def verify_loaded_hardware(bitstream):
    if not bitstream or not Path(bitstream).name:
        raise ValueError('必须指定已加载的 bitstream 文件')
    path = Path(bitstream)
    status = json.loads(Path('/run/ec26e/pl-status.json').read_text(encoding='utf-8'))
    if status.get('board') != 'axu5ev' or status.get('loaded') is not True:
        raise RuntimeError('AXU5EV PL 尚未由启动加载器验收')
    for item, suffix in (('bit_sha256', '.bit'), ('hwh_sha256', '.hwh')):
        source = path.with_suffix(suffix)
        if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != status.get(item):
            raise RuntimeError('PL 文件与本次启动加载记录不匹配')


class UioWordArray:
    """AXI-Lite accepts aligned 32-bit accesses, not NumPy's SIMD memcpy."""
    def __init__(self, registers, count):
        self.registers, self.count = registers, count

    def _indices(self, key):
        if isinstance(key, slice):
            return range(*key.indices(self.count))
        index = int(key)
        if index < 0:
            index += self.count
        if not 0 <= index < self.count:
            raise IndexError(index)
        return (index,)

    def __getitem__(self, key):
        values = [self.registers.read(index * 4) for index in self._indices(key)]
        return np.asarray(values, dtype=np.uint32) if isinstance(key, slice) else values[0]

    def __setitem__(self, key, value):
        indices = self._indices(key)
        values = np.broadcast_to(np.asarray(value, dtype=np.uint32), (len(indices),))
        for index, word in zip(indices, values):
            self.registers.write(index * 4, int(word))


class UioRegisters:
    def __init__(self, name):
        matches = [p for p in Path('/sys/class/uio').glob('uio*')
                   if (p / 'name').read_text().strip() == name]
        if len(matches) != 1:
            raise RuntimeError(f'无法唯一定位 UIO：{name}')
        path = matches[0]
        size = int((path / 'maps/map0/size').read_text(), 0)
        if size != 65536 or int((path / 'maps/map0/offset').read_text(), 0):
            raise RuntimeError('UIO 寄存器布局与硬件 ABI 不一致')
        fd = os.open('/dev/' + path.name, os.O_RDWR | os.O_SYNC)
        try:
            self._mapping = mmap.mmap(fd, size)
        finally:
            os.close(fd)
        self.array = UioWordArray(self, size // 4)
        self.mmio = self

    def read(self, offset):
        return struct.unpack_from('<I', self._mapping, offset)[0]

    def write(self, offset, value):
        struct.pack_into('<I', self._mapping, offset, value)

    def close(self):
        self.array = None
        self._mapping.close()


class LinuxOverlay:
    def __init__(self, bitstream):
        verify_loaded_hardware(bitstream)
        self.ip_dict = {name: {} for name in ('puzzle_rigid_fit_0', 'puzzle_clearance_scan_0')}
        if any(p.read_text().strip() == 'puzzle_candidate_scan_0'
               for p in Path('/sys/class/uio').glob('uio*/name')):
            self.ip_dict['puzzle_candidate_scan_0'] = {}
        self._devices = {}

    def __getattr__(self, name):
        if name not in self.ip_dict:
            raise AttributeError(name)
        if name not in self._devices:
            self._devices[name] = UioRegisters(name)
        return self._devices[name]

    def close(self):
        for device in self._devices.values():
            device.close()
        self._devices.clear()


def close_pl_overlay(overlay):
    if isinstance(overlay, LinuxOverlay):
        overlay.close()


def open_pl_overlay(bitstream):
    access = os.environ.get('EC_PL_ACCESS', 'pynq')
    if access == 'linux':
        return LinuxOverlay(bitstream)
    if access != 'pynq':
        raise ValueError('EC_PL_ACCESS 必须为 pynq 或 linux')
    from pynq import Overlay
    return Overlay(str(bitstream), download=False)


class LinuxPreprocessor:
    """coherent DMA 映射；超时、序号及 DMA 状态由内核驱动检查。"""
    PIXELS = 1280 * 720
    SUBMIT = 0x40184501  # _IOW('E', 1, six uint32 fields), Linux AArch64 ABI.

    def __init__(self, bitstream, config=PreprocessConfig()):
        verify_loaded_hardware(bitstream)
        self.config = config
        self._lock = threading.Lock()
        self._closed = self._failed = False
        self.frame_count = 0
        self.pack_seconds = self.dma_seconds = self.copy_seconds = 0.0
        self.calibration_seconds = 0.0
        self._tx_map = self._rx_map = None
        self._fd = os.open('/dev/ec26e-vision', os.O_RDWR | os.O_SYNC)
        try:
            self._tx_map = mmap.mmap(self._fd, self.PIXELS * 4, offset=0)
            self._rx_map = mmap.mmap(self._fd, self.PIXELS, offset=mmap.PAGESIZE)
            self._tx = np.ndarray((self.PIXELS, 4), dtype=np.uint8, buffer=self._tx_map)
            self._rx = np.ndarray((self.PIXELS,), dtype=np.uint8, buffer=self._rx_map)
            self._tx[:] = 0
        except BaseException:
            self._tx = self._rx = None
            if self._rx_map is not None: self._rx_map.close()
            if self._tx_map is not None: self._tx_map.close()
            os.close(self._fd)
            raise

    def process(self, image, frame_id):
        output = np.empty(image.shape[:2], dtype=np.uint8)
        return self.process_into(image, frame_id, output)

    def process_into(self, image, frame_id, output):
        """写入调用方专用缓冲区，避免每帧分配；不暴露正在复用的 DMA 内存。"""
        import fcntl
        validate_bgr_image(image)
        h, w = image.shape[:2]
        if output.shape != (h, w) or output.dtype != np.uint8 or not output.flags.c_contiguous:
            raise ValueError('输出缓冲区必须为同尺寸连续 uint8 数组')
        if not 1 <= w <= 1280 or not 1 <= h <= 720 or not 1 <= frame_id < 0xffffffff:
            raise ValueError('非法帧尺寸或序号')
        with self._lock:
            if self._closed or self._failed:
                raise RuntimeError('PL 后端已关闭或失步')
            started = time.perf_counter()
            self.last_blue_min = resolve_blue_min(image, self.config)
            self.calibration_seconds += time.perf_counter() - started
            try:
                started = time.perf_counter()
                # NEON converts directly into the coherent DMA buffer. The PL
                # consumes data[23:0] only; the unused high byte is 255 (BGRA).
                # Avoid NumPy's byte-wise strided stores to uncached memory.
                cv2.cvtColor(image, cv2.COLOR_BGR2BGRA,
                             dst=self._tx[:h*w].reshape(h, w, 4))
                self.pack_seconds += time.perf_counter() - started
                args = struct.pack('<6I', w, h, self.config.blue_ratio_q8,
                                   self.last_blue_min, self.config.brightness_min, frame_id)
                started = time.perf_counter()
                fcntl.ioctl(self._fd, self.SUBMIT, args)
                self.dma_seconds += time.perf_counter() - started
                started = time.perf_counter()
                np.copyto(output, self._rx[:h*w].reshape(h, w))
                self.copy_seconds += time.perf_counter() - started
                self.frame_count += 1
                return output
            except BaseException:
                self._failed = True
                raise

    def close(self):
        with self._lock:
            if self._closed: return
            self._closed = True
            self._tx = self._rx = None
            self._tx_map.close()
            self._rx_map.close()
            os.close(self._fd)
