"""CPU/PL 共用定点蓝底分割；邻域零填充。"""
from dataclasses import dataclass, replace
import cv2
import numpy as np
from ec_26_e.vision._blue import validate_bgr_image

@dataclass(frozen=True)
class PreprocessConfig:
    blue_ratio_q8: int = 300
    blue_min: int = 45
    brightness_min: int = 0
    blue_background_fraction_q8: int = 0  # 0 保持静态阈值；其余为蓝底亮度比例。
    blue_profile: str = 'configured'

    def __post_init__(self):
        if self.blue_profile not in ('configured', 'fixed_auto', 'background_auto'):
            raise ValueError('blue_profile 必须为 configured/fixed_auto/background_auto')
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
        fraction = background.size / max(1, sample.shape[0] * sample.shape[1])
        raise ValueError(f"蓝底采样不足（识别为蓝底 {fraction:.0%}，至少需 40%；"
                         f"颜色阈值 {config.blue_ratio_q8}/256）；请检查模式颜色配置、底色和光照")
    return max(config.blue_min, int(float(np.median(background)) *
                                   config.blue_background_fraction_q8 / 256))


def resolve_blue_parameters(image, config):
    """从蓝底颜色分布选阈值，避免青/蓝两档切换吞掉切口花纹。

    低分位数保留较暗底板的支持，比例余量避免背景碎裂；中间色温
    同时启用亮度门槛，保护与蓝底同色但更暗的牌面。PS 稀疏估参，
    CPU/PL 使用同一整数阈值；不依赖片数、模板或求解结果。
    """
    if config.blue_profile == 'configured':
        return config.blue_ratio_q8, resolve_blue_min(image, config)
    sample = image[8:-8:12, 8:-8:12].astype(np.uint32)
    b, g, r = (sample[:, :, i] for i in range(3))
    needed = max(32, sample.shape[0] * sample.shape[1] * .4)
    broad = (b >= config.blue_min) & (b * 256 > np.maximum(g, r) * 275)
    if np.count_nonzero(broad) < needed:
        # Same evidence check and actionable error as configured thresholds.
        chosen = replace(config, blue_ratio_q8=275, blue_background_fraction_q8=1)
        return 275, resolve_blue_min(image, chosen)
    ratios = b[broad].astype(np.float64) * 256 / np.maximum(np.maximum(g, r)[broad], 1)
    ratio = int(np.clip(np.floor(np.quantile(ratios, .1) * .94), 275, 320))
    fraction = 1 if ratio == 275 else 218 if ratio == 320 else 200
    chosen = replace(config, blue_ratio_q8=ratio, blue_background_fraction_q8=fraction)
    return ratio, resolve_blue_min(image, chosen)

class CpuPreprocessor:
    """颜色分割 → 3×3 多数滤波 → 开运算 → 闭运算。"""
    def __init__(self, config=PreprocessConfig()):
        self.config = config

    def process(self, image, frame_id):
        validate_bgr_image(image)
        if not 1 <= image.shape[0] <= 720 or not 1 <= image.shape[1] <= 1280:
            raise ValueError("预处理支持最大 1280×720")
        self.last_blue_ratio_q8, self.last_blue_min = resolve_blue_parameters(image, self.config)
        b, g, r = (image[:, :, i].astype(np.uint32) for i in range(3))
        blue = (b >= self.last_blue_min) & (b * 256 > np.maximum(g, r) * self.last_blue_ratio_q8)
        foreground = (~blue) & (np.maximum(np.maximum(b, g), r) >= self.config.brightness_min)
        kernel = np.ones((3, 3), np.uint8)
        count = cv2.filter2D(foreground.astype(np.uint8), cv2.CV_16U, kernel, borderType=cv2.BORDER_CONSTANT)
        mask = (count >= 5).astype(np.uint8) * 255
        for operation in (cv2.erode, cv2.dilate, cv2.dilate, cv2.erode):
            mask = operation(mask, kernel, borderType=cv2.BORDER_CONSTANT, borderValue=0)
        return mask

    def close(self):
        pass
