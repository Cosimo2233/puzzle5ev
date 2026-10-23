"""PYNQ Linux DMA 后端；一个在途帧，超时后禁止复用失步流水线。"""
import threading
import time
import numpy as np
from ec_26_e.vision._blue import validate_bgr_image
from ec_26_e.vision.preprocess import PreprocessConfig, resolve_blue_parameters

class PynqPreprocessor:
    def __init__(self, bitstream, config=PreprocessConfig(), *, timeout_seconds=5.0):
        if not np.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("DMA 超时必须为正数")
        from pynq import Overlay, allocate
        self.config, self.timeout_seconds = config, timeout_seconds
        # 正式任务不重新配置正在供 Linux UART/GPIO 驱动使用的 PL。
        # bitstream 与同名 .hwh 必须对应启动时已加载的设计。
        self.overlay = Overlay(bitstream, download=False)
        self.dma = self.overlay.axi_dma_0
        self.ip = self.overlay.puzzle_preprocess_0
        self.allocate = allocate
        self._lock = threading.Lock()
        self._failed = False
        self._closed = False
        self._buffers = None
        self._buffer_shape = None

    def _release_buffers(self):
        if self._buffers is not None:
            tx, rx = self._buffers
            self._buffers = None
            self._buffer_shape = None
            tx.freebuffer()
            rx.freebuffer()

    def _get_buffers(self, h, w):
        # 仅在上一帧已完成时换尺寸；固定相机重复使用同一组 CMA 缓冲。
        if self._buffer_shape != (h, w):
            self._release_buffers()
            tx = self.allocate(shape=(h*w,), dtype=np.uint32)
            try:
                rx = self.allocate(shape=(h*w,), dtype=np.uint8)
            except Exception:
                tx.freebuffer()
                raise
            tx[:] = 0
            self._buffers = (tx, rx)
            self._buffer_shape = (h, w)
        return self._buffers

    def process(self, image, frame_id):
        validate_bgr_image(image)
        h, w = image.shape[:2]
        if not 1 <= w <= 1280 or not 1 <= h <= 720 or not 1 <= frame_id < 0xffffffff:
            raise ValueError("非法帧尺寸或序号")
        with self._lock:
            if self._failed or self._closed:
                raise RuntimeError("PL 后端已关闭或失步，需要重新加载 overlay")
            self.last_blue_ratio_q8, self.last_blue_min = resolve_blue_parameters(image, self.config)
            tx, rx = self._get_buffers(h, w)
            try:
                # AXIS 字节顺序是 B/G/R/0。直接写字节，避免三个 uint32
                # 通道、移位和按位或产生的全帧临时数组。asarray 去掉
                # PynqBuffer 子类，使临时视图不会拥有释放 CMA 的职责。
                np.asarray(tx).view(np.uint8).reshape(h, w, 4)[..., :3] = image
                for offset, value in ((0x10,w),(0x18,h),(0x20,self.last_blue_ratio_q8),
                    (0x28,self.last_blue_min),(0x30,self.config.brightness_min),(0x38,frame_id)):
                    self.ip.write(offset,value)
                self.dma.recvchannel.transfer(rx)
                self.ip.write(0x00,1)
                self.dma.sendchannel.transfer(tx)
                deadline = time.monotonic()+self.timeout_seconds
                done = False
                while True:
                    done = done or bool(self.ip.read(0x00) & 2)
                    # Read status directly: PYNQ wait() has no deadline.
                    status = (self.dma.mmio.read(0x04),self.dma.mmio.read(0x34))
                    if any(s & 0x70 for s in status):
                        raise RuntimeError(f"AXI DMA 错误: {status}")
                    if done and all(s & 2 for s in status):
                        break
                    if time.monotonic() >= deadline:
                        raise TimeoutError("PL/DMA 未在期限内完成整帧")
                    time.sleep(0.001)
                if self.ip.read(0x40) != frame_id:
                    raise RuntimeError("PL 帧编号不匹配")
                rx.invalidate()
                return np.array(rx).reshape(h,w)
            except Exception:
                self._failed = True
                # Reset both channels before buffers can be released.
                try:
                    self.dma.mmio.write(0x00,4)
                    self.dma.mmio.write(0x30,4)
                    deadline = time.monotonic()+1
                    while self.dma.mmio.read(0x00)&4 or self.dma.mmio.read(0x30)&4:
                        if time.monotonic()>deadline:
                            raise TimeoutError("DMA reset timeout")
                        time.sleep(0.001)
                except Exception as reset_error:
                    # Also retain buffers when MMIO access itself fails.
                    self._quarantined = (tx,rx)
                    raise RuntimeError("DMA 无法复位；缓冲区保留，请重启板卡") from reset_error
                self._release_buffers()
                raise

    def close(self):
        with self._lock:
            self._closed = True
            if not hasattr(self, "_quarantined"):
                self._release_buffers()
