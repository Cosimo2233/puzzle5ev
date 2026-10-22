"""2～4 片凹/凸多边形的 PL 批量候选距离；不签发执行许可。"""
from itertools import combinations
from pathlib import Path
import math
import threading
import time

import numpy as np
from shapely.geometry import Polygon

ABI = 0x434e0101
INPUT_BASE, OUTPUT_BASE = 0x1000, 0x2000
INPUT_WORDS, BATCH = 580, 64
PAIRS = tuple(combinations(range(4), 2))


def pack_geometry(vertices, deltas):
    if not 2 <= len(vertices) <= 4:
        return None
    deltas = np.asarray(deltas, dtype=float)
    if deltas.ndim != 3 or deltas.shape[1:] != (len(vertices), 2) or not np.isfinite(deltas).all():
        raise ValueError('候选平移必须为有限的 B×N×2 数组')
    if deltas.size and np.abs(deltas).max() > 128:
        return None
    packed = np.zeros(INPUT_WORDS, dtype=np.float32)
    for index, raw in enumerate(vertices):
        points = np.asarray(raw, dtype=float)
        if points.ndim != 2 or points.shape[1] != 2 or not np.isfinite(points).all():
            raise ValueError('候选顶点必须为有限的 N×2 数组')
        if not 3 <= len(points) <= 8 or np.abs(points).max() > 512:
            return None
        poly = Polygon(points)
        if not poly.is_valid or poly.area <= 1e-6:
            raise ValueError('候选几何必须为简单非退化多边形')
        if np.min(np.linalg.norm(np.roll(points, -1, axis=0)-points, axis=1)) < .01:
            return None
        packed[index] = len(points)
        packed[4+16*index:4+16*index+points.size] = points.ravel()
    return packed


class PynqCandidateScanBackend:
    def __init__(self, bitstream, timeout_seconds=2.0):
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError('候选扫描超时必须为有限正数')
        path = Path(bitstream)
        if not path.is_file() or not path.with_suffix('.hwh').is_file():
            raise RuntimeError('候选 PL 需要匹配的 bit/HWH')
        import fcntl
        from ec_26_e.vision.linux_pl import open_pl_overlay, close_pl_overlay
        self._lockfile = open('/tmp/ec26e-candidate-scan.lock', 'a+b')
        try:
            fcntl.flock(self._lockfile.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.overlay = open_pl_overlay(str(path))
            if 'puzzle_candidate_scan_0' not in self.overlay.ip_dict:
                raise RuntimeError('当前硬件缺少凹多边形候选扫描核，请加载匹配的新硬件包')
            self.ip = self.overlay.puzzle_candidate_scan_0
            self.timeout_seconds = timeout_seconds
            self._lock = threading.Lock()
            self._failed = self._closed = False
            self._request_id = self.request_count = self.batch_count = self.unsupported_count = 0
            self.pack_seconds = self.transfer_seconds = self.kernel_wait_seconds = 0.0
            if not self.ip.read(0) & 4:
                raise RuntimeError('候选扫描核正忙，不能接管在途请求')
            self.ip.mmio.array[INPUT_BASE//4:INPUT_BASE//4+INPUT_WORDS] = 0
            self._transaction(1, expected_status=1)
        except BaseException:
            close_pl_overlay(getattr(self, 'overlay', None))
            self._lockfile.close()
            raise

    def _transaction(self, count, expected_status=0):
        started = time.perf_counter()
        if not self.ip.read(0) & 4:
            raise RuntimeError('候选 PL 核未处于空闲状态')
        self._request_id = self._request_id % 0xffffffff + 1
        self.ip.write(0x10, self._request_id)
        self.ip.write(0x18, count)
        self.ip.write(0, 1)
        deadline = time.monotonic() + self.timeout_seconds
        while not self.ip.read(0) & 2:
            if time.monotonic() >= deadline:
                raise TimeoutError('候选 PL 扫描超时，实例已停用')
            time.sleep(.0001)
        if self.ip.read(0x28) != ABI or self.ip.read(0x20) != self._request_id:
            raise RuntimeError('候选 PL 协议或完成序号不匹配')
        if self.ip.read(0x30) != expected_status:
            raise RuntimeError('候选 PL 返回错误状态')
        self.kernel_wait_seconds += time.perf_counter()-started

    def pair_distances(self, vertices, deltas):
        started = time.perf_counter()
        packed = pack_geometry(vertices, deltas)
        deltas = np.asarray(deltas, dtype=float)
        with self._lock:
            if self._failed or self._closed:
                raise RuntimeError('候选 PL 已失步或关闭，禁止复用')
            self.pack_seconds += time.perf_counter()-started
            if packed is None:
                self.unsupported_count += 1
                return None
            active = [i for i, (_, b) in enumerate(PAIRS) if b < len(vertices)]
            output = np.empty((len(deltas), len(active)), dtype=float)
            try:
                for start in range(0, len(deltas), BATCH):
                    count = min(BATCH, len(deltas)-start)
                    shifts = np.zeros((count, 4, 2), dtype=np.float32)
                    shifts[:, :len(vertices)] = deltas[start:start+count]
                    packed[68:68+8*count] = shifts.ravel()
                    started = time.perf_counter()
                    self.ip.mmio.array[INPUT_BASE//4:INPUT_BASE//4+68+8*count] = packed[:68+8*count].view(np.uint32)
                    self.transfer_seconds += time.perf_counter()-started
                    self._transaction(count)
                    started = time.perf_counter()
                    squared = self.ip.mmio.array[OUTPUT_BASE//4:OUTPUT_BASE//4+6*count].copy().view(np.float32).reshape(count, 6)
                    self.transfer_seconds += time.perf_counter()-started
                    inactive = [i for i in range(6) if i not in active]
                    values = squared[:, active]
                    if (not np.isfinite(values).all() or np.any(values < 0) or np.any(values > 1e8)
                            or (inactive and np.any(squared[:, inactive] != -1))):
                        raise RuntimeError('候选 PL 返回无效距离或填充结果')
                    output[start:start+count] = np.sqrt(values.astype(float))
                    self.request_count += count
                    self.batch_count += 1
                return output
            except BaseException:
                self._failed = True
                raise

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            from ec_26_e.vision.linux_pl import close_pl_overlay
            close_pl_overlay(getattr(self, 'overlay', None))
            self._lockfile.close()
