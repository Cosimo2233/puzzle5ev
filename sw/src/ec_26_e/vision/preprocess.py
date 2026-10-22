"""CPU/PL 共用定点蓝底分割；邻域零填充。"""
from dataclasses import dataclass
import cv2
import numpy as np
from ec_26_e.vision._blue import validate_bgr_image

@dataclass(frozen=True)
class PreprocessConfig:
    blue_ratio_q8: int = 300
    blue_min: int = 45
    brightness_min: int = 0
    blue_background_fraction_q8: int = 0  # 0 保持静态阈值；其余为蓝底亮度比例。

    def __post_init__(self):
        for name, limit in (("blue_ratio_q8", 1023), ("blue_min", 255), ("brightness_min", 255),
                            ("blue_background_fraction_q8", 255)):
            if type(getattr(self, name)) is not int or not 0 <= getattr(self, name) <= limit:
                raise ValueError(f"{name} 必须为 0..{limit} 的整数")


def resolve_blue_min(image, config):
    """稀疏采样蓝底，设置本帧 CPU/PL 共用门槛；不生成 CPU 全幅掩膜。

    蓝灰印刷与蓝底都可能满足通道比例。用蓝底亮度中位数排除较暗的
    印刷，同时随曝光调整。启用时要求至少 40% 采样点支持蓝底，不能
    在工作区不可信时沿用上一帧参数。饱和或与蓝底同色的印刷仍有歧义。
    """
    if not config.blue_background_fraction_q8:
        return config.blue_min
    sample = image[8:-8:12, 8:-8:12].astype(np.uint32)
    b, g, r = (sample[:, :, i] for i in range(3))
    background = b[(b >= config.blue_min) &
                   (b * 256 > np.maximum(g, r) * config.blue_ratio_q8)]
    if background.size < max(32, sample.shape[0] * sample.shape[1] * 0.4):
        raise ValueError("蓝底采样不足，无法校准分割亮度；请检查遮挡、底色和光照")
    return max(config.blue_min, int(float(np.median(background)) *
                                   config.blue_background_fraction_q8 / 256))

class CpuPreprocessor:
    """颜色分割 → 3×3 多数滤波 → 开运算 → 闭运算。"""
    def __init__(self, config=PreprocessConfig()):
        self.config = config

    def process(self, image, frame_id):
        validate_bgr_image(image)
        if not 1 <= image.shape[0] <= 720 or not 1 <= image.shape[1] <= 1280:
            raise ValueError("预处理支持最大 1280×720")
        self.last_blue_min = resolve_blue_min(image, self.config)
        b, g, r = (image[:, :, i].astype(np.uint32) for i in range(3))
        blue = (b >= self.last_blue_min) & (b * 256 > np.maximum(g, r) * self.config.blue_ratio_q8)
        foreground = (~blue) & (np.maximum(np.maximum(b, g), r) >= self.config.brightness_min)
        kernel = np.ones((3, 3), np.uint8)
        count = cv2.filter2D(foreground.astype(np.uint8), cv2.CV_16U, kernel, borderType=cv2.BORDER_CONSTANT)
        mask = (count >= 5).astype(np.uint8) * 255
        for operation in (cv2.erode, cv2.dilate, cv2.dilate, cv2.erode):
            mask = operation(mask, kernel, borderType=cv2.BORDER_CONSTANT, borderValue=0)
        return mask

    def close(self):
        pass
