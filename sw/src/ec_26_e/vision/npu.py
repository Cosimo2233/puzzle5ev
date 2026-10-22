"""用 ctypes 安全调用 A733 VIPLite YOLO11n-seg 原生共享库。"""

from __future__ import annotations

import ctypes
from pathlib import Path
import threading

import numpy as np

from ec_26_e.vision.contracts import NativeTiming, SegInstance, VisionConfig


class _Detection(ctypes.Structure):
    """镜像 C ABI 的检测框结构。"""

    _fields_ = [
        ("x1", ctypes.c_float),
        ("y1", ctypes.c_float),
        ("x2", ctypes.c_float),
        ("y2", ctypes.c_float),
        ("score", ctypes.c_float),
        ("class_id", ctypes.c_int32),
    ]


class _Timing(ctypes.Structure):
    """镜像 C ABI 的纳秒计时结构。"""

    _fields_ = [
        ("preprocess_ns", ctypes.c_uint64),
        ("input_flush_ns", ctypes.c_uint64),
        ("network_ns", ctypes.c_uint64),
        ("output_map_ns", ctypes.c_uint64),
        ("detection_decode_ns", ctypes.c_uint64),
        ("nms_ns", ctypes.c_uint64),
        ("mask_decode_ns", ctypes.c_uint64),
        ("output_unmap_ns", ctypes.c_uint64),
        ("total_ns", ctypes.c_uint64),
    ]


def _absolute_from_project(path: Path) -> Path:
    """将相对路径解析到仓库根目录。"""

    if path.is_absolute():
        return path
    return Path(__file__).resolve().parents[3] / path


class NpuYolo11SegInferencer:
    """管理单线程归属的 VIPLite 模型生命周期与输出缓冲区。"""

    def __init__(self, config: VisionConfig | None = None) -> None:
        """加载共享库并创建模型上下文。"""

        self.config = config or VisionConfig()
        self._owner_thread = threading.get_ident()
        library_path = _absolute_from_project(self.config.library_path)
        model_path = _absolute_from_project(self.config.model_path)
        if not library_path.is_file():
            raise FileNotFoundError(f"YOLO 原生库不存在：{library_path}")
        if not model_path.is_file():
            raise FileNotFoundError(f"YOLO 模型不存在：{model_path}")
        self._library = ctypes.CDLL(str(library_path))
        self._configure_abi()
        self._context = self._library.yolo_seg_create(
            str(model_path).encode(),
            self.config.score_threshold,
            self.config.nms_threshold,
            self.config.mask_threshold,
        )
        if not self._context:
            raise RuntimeError(self._error_message(None))
        self.last_timing = NativeTiming()

    def _configure_abi(self) -> None:
        """逐项声明 ctypes 参数，阻止指针和整数被隐式截断。"""

        byte_pointer = ctypes.POINTER(ctypes.c_uint8)
        detection_pointer = ctypes.POINTER(_Detection)
        self._library.yolo_seg_create.argtypes = [
            ctypes.c_char_p, ctypes.c_float, ctypes.c_float, ctypes.c_float
        ]
        self._library.yolo_seg_create.restype = ctypes.c_void_p
        self._library.yolo_seg_infer_bgr.argtypes = [
            ctypes.c_void_p, byte_pointer, ctypes.c_size_t,
            ctypes.c_int, ctypes.c_int, ctypes.c_size_t,
            detection_pointer, ctypes.c_int, byte_pointer, ctypes.c_size_t,
        ]
        self._library.yolo_seg_infer_bgr.restype = ctypes.c_int
        self._library.yolo_seg_last_timing.argtypes = [ctypes.c_void_p, ctypes.POINTER(_Timing)]
        self._library.yolo_seg_last_timing.restype = ctypes.c_int
        self._library.yolo_seg_input_size.argtypes = [ctypes.c_void_p]
        self._library.yolo_seg_input_size.restype = ctypes.c_int
        self._library.yolo_seg_driver_version.argtypes = [ctypes.c_void_p]
        self._library.yolo_seg_driver_version.restype = ctypes.c_uint32
        self._library.yolo_seg_last_error.argtypes = [ctypes.c_void_p]
        self._library.yolo_seg_last_error.restype = ctypes.c_char_p
        self._library.yolo_seg_destroy.argtypes = [ctypes.c_void_p]
        self._library.yolo_seg_destroy.restype = None

    def _require_owner(self) -> None:
        """保证推理和释放发生在创建 VIPLite 的同一线程。"""

        if threading.get_ident() != self._owner_thread:
            raise RuntimeError("NPU 推理器必须在创建它的线程使用和关闭")
        if not self._context:
            raise RuntimeError("NPU 推理器已经关闭")

    def _error_message(self, context: ctypes.c_void_p | None) -> str:
        """读取并解码原生层最近一次错误。"""

        raw = self._library.yolo_seg_last_error(context)
        return raw.decode("utf-8", errors="replace") if raw else "未知原生错误"

    @property
    def input_size(self) -> int:
        """返回模型的正方形输入边长。"""

        self._require_owner()
        return int(self._library.yolo_seg_input_size(self._context))

    @property
    def driver_version(self) -> int:
        """返回 VIPLite 驱动的原始版本整数。"""

        self._require_owner()
        return int(self._library.yolo_seg_driver_version(self._context))

    def infer(self, image_bgr: np.ndarray) -> tuple[SegInstance, ...]:
        """对一张连续 BGR 图推理，输出 A4 图坐标中的框与独立 mask。"""

        self._require_owner()
        if not isinstance(image_bgr, np.ndarray) or image_bgr.dtype != np.uint8:
            raise TypeError("image_bgr 必须是 uint8 numpy 数组")
        if image_bgr.ndim != 3 or image_bgr.shape[2] != 3:
            raise ValueError("image_bgr 必须是 H×W×3 BGR 图像")
        image = np.ascontiguousarray(image_bgr)
        height, width = image.shape[:2]
        detections = (_Detection * self.config.max_pieces)()
        masks = np.zeros((self.config.max_pieces, height, width), dtype=np.uint8)
        count = self._library.yolo_seg_infer_bgr(
            self._context,
            image.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
            image.nbytes,
            width,
            height,
            image.strides[0],
            detections,
            self.config.max_pieces,
            masks.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
            masks.nbytes,
        )
        if count < 0:
            raise RuntimeError(self._error_message(self._context))
        timing = _Timing()
        if self._library.yolo_seg_last_timing(self._context, ctypes.byref(timing)) != 0:
            raise RuntimeError(self._error_message(self._context))
        values = [getattr(timing, name) / 1_000_000.0 for name, _ in timing._fields_]
        self.last_timing = NativeTiming(*values)
        return tuple(
            SegInstance(
                box_px=(item.x1, item.y1, item.x2, item.y2),
                score=float(item.score),
                class_id=int(item.class_id),
                mask=masks[index].copy(),
            )
            for index, item in enumerate(detections[:count])
        )

    def close(self) -> None:
        """在归属线程确定性释放 VIPLite 资源。"""

        if not self._context:
            return
        self._require_owner()
        self._library.yolo_seg_destroy(self._context)
        self._context = None

    def __enter__(self) -> NpuYolo11SegInferencer:
        """进入确定性资源上下文。"""

        return self

    def __exit__(self, *_: object) -> None:
        """退出上下文时关闭原生资源。"""

        self.close()
