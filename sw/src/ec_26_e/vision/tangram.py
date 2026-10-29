"""白色七巧板的单帧自适应分割；不借助目标模板补全缺失轮廓。"""
from __future__ import annotations

import cv2
import numpy as np

from ec_26_e.vision._blue import normalized_blue_score_u8, validate_bgr_image


class TangramPreprocessor:
    """归一化蓝色度分离白片和蓝底，弱边缘必须连接到可靠的白片像素。

    亮度乘性变化基本不改变色度，因此蓝底投影阴影不会仅因变暗成为
    碎片。每张图独立估参，不保存上一帧阈值，不通过模板或凑片数分割。
    """

    def process(self, image, frame_id):
        validate_bgr_image(image)
        height, width = image.shape[:2]
        if not 1 <= height <= 720 or not 1 <= width <= 1280:
            raise ValueError('七巧板预处理支持最大 1280×720')
        # 中值滤波消除孤立色噪声，同时避免高斯模糊跨越窄片间隙。
        filtered = cv2.medianBlur(image, 3)
        score = normalized_blue_score_u8(filtered)
        brightness = filtered.max(axis=2)
        sample = score[4:-4:4, 4:-4:4]
        colors = filtered[4:-4:4, 4:-4:4].astype(np.float32)
        blue = ((sample >= 145) & (colors[..., 0] > colors[..., 2] * 1.05)
                & (colors.max(axis=2) >= 15))
        if np.count_nonzero(blue) < max(32, sample.size * .4):
            raise ValueError('七巧板蓝底支持不足（至少需 40%），请检查光照和工作区')
        # 冷色温会让白片也带蓝；先选择占多数的背景色簇，不能把所有
        # 偏蓝像素的低分位（可能属于白片）当成底板。
        center = float(np.median(sample[blue]))
        spread = float(np.median(np.abs(sample[blue].astype(float) - center)))
        blue &= sample >= center - max(24., 3 * spread)
        if np.count_nonzero(blue) < max(32, sample.size * .4):
            raise ValueError('七巧板背景颜色不一致，无法可靠分离白片')
        otsu, _ = cv2.threshold(score, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        brightness_floor = max(12., float(np.median(colors.max(axis=2)[blue])) * .12)
        light_enough = brightness >= brightness_floor
        white_samples = score[(score <= otsu) & light_enough]
        if white_samples.size < score.size * .01:
            raise ValueError('未找到可靠白片像素')
        white = float(np.median(white_samples))
        if center - white < 24.:
            raise ValueError('白片与蓝底色差不足，请改善光照')
        # 利用周围已确认蓝底估计局部背景，补偿渐变光照和浅色反光。
        # 只作色度归一化，不模糊输出轮廓，也不读取下一帧。
        background = ((score >= center-max(24.,3*spread)) & light_enough).astype(np.float32)
        weight = cv2.GaussianBlur(background, (0,0), 24.)
        local = cv2.GaussianBlur(score.astype(np.float32)*background, (0,0), 24.)
        local = np.divide(local, weight, out=np.full_like(local, center), where=weight > .01)
        contrast = (score.astype(np.float32)-white) / np.maximum(local-white, 24.)
        self.last_thresholds = (white, center)
        seeds = (contrast <= .35) & light_enough
        possible = ((contrast <= .55) & light_enough).astype(np.uint8)
        count, labels, stats, _ = cv2.connectedComponentsWithStats(possible, 8)
        support = np.bincount(labels[seeds], minlength=count)
        # 没有白片核心的弱色差阴影不能单独成为物体；不按片数选前七名。
        keep = (support >= 4) & (support >= stats[:, cv2.CC_STAT_AREA] * .15)
        keep[0] = False
        mask = keep[labels].astype(np.uint8) * 255
        # 只修复一个像素的孔洞；不作大核闭运算或接触碎片的强制拆分。
        return cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))

    def close(self):
        pass
