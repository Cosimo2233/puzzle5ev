"""PL 批量间隙粗筛；最终几何与执行许可始终由 PS 检查。"""
from pathlib import Path
import math
import threading
import time
import numpy as np

ABI = 0x43440101
INPUT_BASE, OUTPUT_BASE = 0x1000, 0x2000
SAMPLES = 401


def pack_scan(vertices, centroids):
    """仅接受四个严格凸多边形；其余几何显式留在 CPU。"""
    if len(vertices) != 4:
        return None
    centers = np.asarray(centroids, dtype=float)
    if centers.shape != (4, 2) or not np.isfinite(centers).all():
        raise ValueError('间隙粗筛质心必须为有限 4×2 坐标')
    if np.abs(centers).max() > 512:
        return None
    packed = np.zeros(76, dtype=np.float32)
    packed[4:12] = centers.ravel()
    for index, raw in enumerate(vertices):
        points = np.asarray(raw, dtype=float)
        if points.ndim != 2 or points.shape[1] != 2 or not np.isfinite(points).all():
            raise ValueError('间隙粗筛顶点必须为有限 N×2 坐标')
        if not 3 <= len(points) <= 8 or np.abs(points).max() > 512:
            return None
        area = np.sum(points[:,0]*np.roll(points[:,1],-1)-points[:,1]*np.roll(points[:,0],-1))
        if area < 0:
            points = points[::-1]
        edges = np.roll(points,-1,axis=0)-points
        cross = edges[:,0]*np.roll(edges[:,1],-1)-edges[:,1]*np.roll(edges[:,0],-1)
        if np.any(cross <= 1e-5) or np.any(np.sum(edges**2,axis=1) < 1.01):
            return None
        # Local turns alone also accept some self-intersecting star polygons.
        relative = points[None, :, :] - points[:, None, :]
        halfplanes = edges[:, None, 0]*relative[:, :, 1] - edges[:, None, 1]*relative[:, :, 0]
        if np.any(halfplanes < -1e-5):
            return None
        packed[index] = len(points)
        packed[12+16*index:12+16*index+points.size] = points.ravel()
    return packed


class PynqClearanceScanBackend:
    """独占扫描核；错误后停用，不下载 Overlay，也不复位其他外设。"""
    def __init__(self, bitstream, timeout_seconds=2.0):
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError('扫描超时必须为有限正数')
        path = Path(bitstream)
        if not path.is_file() or not path.with_suffix('.hwh').is_file():
            raise RuntimeError('间隙 PL 需要匹配的 bit/HWH')
        import fcntl
        self._lockfile = open('/tmp/ec26e-clearance-scan.lock','a+b')
        try:
            fcntl.flock(self._lockfile.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            from ec_26_e.vision.linux_pl import open_pl_overlay
            self.overlay = open_pl_overlay(str(path))
            if 'puzzle_clearance_scan_0' not in self.overlay.ip_dict:
                raise RuntimeError('当前 HWH 缺少 puzzle_clearance_scan_0')
            self.ip = self.overlay.puzzle_clearance_scan_0
            self.timeout_seconds = timeout_seconds
            self._lock = threading.Lock()
            self._failed = self._closed = False
            self._request_id = self.scan_count = self.unsupported_count = 0
            self.transfer_seconds = self.kernel_wait_seconds = 0.0
            if not self.ip.read(0)&4:
                raise RuntimeError('间隙扫描核正忙，不能接管在途请求')
            self.ip.mmio.array[INPUT_BASE//4:INPUT_BASE//4+76] = 0
            self._transaction(expected_status=1)
        except Exception:
            from ec_26_e.vision.linux_pl import close_pl_overlay
            close_pl_overlay(getattr(self, 'overlay', None))
            self._lockfile.close()
            raise

    def _transaction(self, expected_status=0):
        started = time.perf_counter()
        self._request_id = self._request_id % 0xffffffff + 1
        self.ip.read(0)
        self.ip.write(0x10,self._request_id)
        self.ip.write(0,1)
        deadline = time.monotonic()+self.timeout_seconds
        while not self.ip.read(0)&2:
            if time.monotonic()>=deadline:
                raise TimeoutError('间隙 PL 扫描超时，实例已停用')
            time.sleep(.0001)
        if self.ip.read(0x28)!=ABI or self.ip.read(0x20)!=self._request_id:
            raise RuntimeError('间隙 PL 协议或完成序号不匹配')
        if self.ip.read(0x30)!=expected_status:
            raise RuntimeError('间隙 PL 返回错误状态')
        self.kernel_wait_seconds += time.perf_counter() - started

    def minimum_distances(self, vertices, centroids):
        packed = pack_scan(vertices,centroids)
        with self._lock:
            if self._failed or self._closed:
                raise RuntimeError('间隙 PL 已失步或关闭，禁止复用')
            if packed is None:
                self.unsupported_count += 1
                return None
            try:
                started = time.perf_counter()
                self.ip.mmio.array[INPUT_BASE//4:INPUT_BASE//4+76] = packed.view(np.uint32)
                self.transfer_seconds += time.perf_counter() - started
                self._transaction()
                started = time.perf_counter()
                squared = self.ip.mmio.array[OUTPUT_BASE//4:OUTPUT_BASE//4+SAMPLES].copy().view(np.float32)
                self.transfer_seconds += time.perf_counter() - started
                if not np.isfinite(squared).all() or np.any(squared<0) or np.any(squared>1e8):
                    raise RuntimeError('间隙 PL 返回无效距离')
                self.scan_count += 1
                return np.sqrt(squared.astype(float))
            except Exception:
                self._failed = True
                raise

    def close(self):
        with self._lock:
            self._closed = True
            from ec_26_e.vision.linux_pl import close_pl_overlay
            close_pl_overlay(getattr(self, 'overlay', None))
            self._lockfile.close()
