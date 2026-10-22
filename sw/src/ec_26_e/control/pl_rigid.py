"""AXI-Lite 批量配准驱动；仅使用已加载 PL，不下载或复位全系统。"""
from __future__ import annotations

from pathlib import Path
import math
import threading
import time
import numpy as np
from puzzle_solver.acceleration import (
    CpuRigidFitBackend, RigidFitRequest, RigidFitResult, validate_request,
)

ABI = 0x52470101
MAX_BATCH, MAX_POINTS = 64, 8
INPUT_STRIDE, OUTPUT_STRIDE = 41, 6
INPUT_BASE, OUTPUT_BASE = 0x4000, 0x8000


class PynqRigidFitBackend:
    """一个进程独占设备。错误/超时后实例永久失效，禁止返回旧结果。"""

    def __init__(self, bitstream: str, timeout_seconds: float = 2.0):
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("配准超时必须为有限正数")
        path = Path(bitstream)
        if not path.is_file() or not path.with_suffix('.hwh').is_file():
            raise RuntimeError("配准 PL 需要同一构建的 bit/HWH 文件")
        # OS lock covers all instances/processes; no conflict with vision DMA.
        import fcntl
        self._lockfile = open('/tmp/ec26e-rigid-fit.lock', 'a+b')
        try:
            fcntl.flock(self._lockfile.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            from ec_26_e.vision.linux_pl import open_pl_overlay
            self.overlay = open_pl_overlay(str(path))
            if 'puzzle_rigid_fit_0' not in self.overlay.ip_dict:
                raise RuntimeError("当前 HWH 缺少 puzzle_rigid_fit_0；请先部署匹配硬件")
            self.ip = self.overlay.puzzle_rigid_fit_0
            self.timeout_seconds = timeout_seconds
            self._lock = threading.Lock()
            self._failed = self._closed = False
            self._request_id = 0
            self.numerical_cpu_fallbacks = 0
            self.batch_count = 0
            self.request_count = 0
            self.pack_seconds = self.transfer_seconds = self.kernel_wait_seconds = 0.0
            if not self.ip.read(0) & 4:
                raise RuntimeError("配准 IP 正忙或未加载，不能接管在途请求")
            self._transaction(0)  # Empty request probes ABI and request identity.
        except Exception:
            from ec_26_e.vision.linux_pl import close_pl_overlay
            close_pl_overlay(getattr(self, 'overlay', None))
            self._lockfile.close()
            raise

    def _transaction(self, count: int) -> np.ndarray:
        started = time.perf_counter()
        self._request_id = self._request_id % 0xffffffff + 1
        self.ip.read(0)  # Clear a prior ap_done before starting a new request.
        self.ip.write(0x10, self._request_id)
        self.ip.write(0x18, count)
        self.ip.write(0, 1)
        deadline = time.monotonic() + self.timeout_seconds
        while not self.ip.read(0) & 2:
            if time.monotonic() >= deadline:
                raise TimeoutError("配准 PL 批次超时；实例已停用，需检查硬件")
            time.sleep(0.0001)
        if self.ip.read(0x28) != ABI:
            raise RuntimeError("配准 PL 协议版本不匹配")
        if self.ip.read(0x20) != self._request_id:
            raise RuntimeError("配准 PL 完成序号不匹配")
        if self.ip.read(0x30) != 0:
            raise RuntimeError("配准 PL 拒绝批次")
        self.batch_count += 1
        self.request_count += count
        self.kernel_wait_seconds += time.perf_counter() - started
        started = time.perf_counter()
        words = np.asarray(self.ip.mmio.array[OUTPUT_BASE//4:OUTPUT_BASE//4+count*OUTPUT_STRIDE], dtype=np.uint32).copy()
        self.transfer_seconds += time.perf_counter() - started
        return words.view(np.float32).reshape(count, OUTPUT_STRIDE)

    def fit_batch(self, requests):
        requests = list(requests)
        # Validate the whole logical batch before making hardware changes.
        for request in requests:
            validate_request(request)
            if len(request.source) > MAX_POINTS or max(np.abs(request.source).max(), np.abs(request.target).max()) > 4096:
                raise ValueError("配准 PL 支持 2～8 点、坐标绝对值不超过 4096 mm")
            if request.weights is not None:
                # Match the existing CPU estimator's clip-before-normalize rule.
                weights = np.clip(np.asarray(request.weights, float), 1e-6, None)
                if weights.min() / weights.max() < 1e-6:
                    raise ValueError("配准 PL 权重动态范围超过 1e6")
        with self._lock:
            if self._failed or self._closed:
                raise RuntimeError("配准 PL 已失步或关闭，禁止复用")
            results = []
            try:
                for start in range(0, len(requests), MAX_BATCH):
                    started = time.perf_counter()
                    chunk = requests[start:start+MAX_BATCH]
                    packed = np.zeros((len(chunk), INPUT_STRIDE), dtype=np.float32)
                    for i, request in enumerate(chunk):
                        n = len(request.source)
                        packed[i, 0] = n
                        points = packed[i, 1:].reshape(MAX_POINTS, 5)
                        points[:n, :2], points[:n, 2:4] = request.source, request.target
                        weights = (np.ones(n) if request.weights is None
                                   else np.clip(np.asarray(request.weights, float), 1e-6, None))
                        points[:n, 4] = weights / weights.max()
                    self.pack_seconds += time.perf_counter() - started
                    started = time.perf_counter()
                    # The resident ABI only reads count + n points for each job.
                    # Avoid sending the unused padding of every two-endpoint fit.
                    for index, request in enumerate(chunk):
                        words = 1 + 5 * len(request.source)
                        offset = INPUT_BASE // 4 + index * INPUT_STRIDE
                        self.ip.mmio.array[offset:offset+words] = packed[index, :words].view(np.uint32)
                    self.transfer_seconds += time.perf_counter() - started
                    raw = self._transaction(len(chunk))
                    for request, row in zip(chunk, raw):
                        if not np.isfinite(row).all():
                            raise RuntimeError("配准 PL 返回非有限数值")
                        if row[0] == 2:
                            # Defined numerical fallback, never a transport-failure fallback.
                            results.extend(CpuRigidFitBackend().fit_batch([request]))
                            self.numerical_cpu_fallbacks += 1
                        elif row[0] != 0 or row[4] < 0 or row[5] < 0 or not -180 <= row[1] < 180:
                            raise RuntimeError("配准 PL 返回无效状态或结果")
                        else:
                            results.append(RigidFitResult(float(row[1]), row[2:4].astype(float), float(row[4])))
                return results
            except Exception:
                self._failed = True
                raise

    def close(self):
        with self._lock:
            self._closed = True
            from ec_26_e.vision.linux_pl import close_pl_overlay
            close_pl_overlay(getattr(self, 'overlay', None))
            self._lockfile.close()
