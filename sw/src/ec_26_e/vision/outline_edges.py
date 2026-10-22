"""窄带色差梯度直线拟合；兼容 BSP 预览 Python 3.8 和正式检测。"""
import cv2
import numpy as np


def refine_polygon_edges(image, contour, band, paper=False, is_polygon=False):
    """只在候选边的窄带取样、拟合直线和求交；不做凸包或跨帧沿用。"""
    contour = contour.astype(np.float32)
    polygon = (contour if paper or is_polygon else cv2.approxPolyDP(
        contour, cv2.arcLength(contour, True) * 0.018, True)).reshape(-1, 2)
    if not 3 <= len(polygon) <= 12:
        return contour
    signed_area = np.sum(polygon[:, 0] * np.roll(polygon[:, 1], -1)
                         - polygon[:, 1] * np.roll(polygon[:, 0], -1))
    orientation = 1 if signed_area > 0 else -1
    offsets = np.arange(-band - 3, band + 4, dtype=np.float32)
    lines = []
    for start, end in zip(polygon, np.roll(polygon, -1, axis=0)):
        edge = end - start
        length = float(np.linalg.norm(edge))
        if length < 8:
            return contour
        direction = edge / length
        normal = np.array([-direction[1], direction[0]], np.float32) * orientation
        fractions = np.linspace(0.15, 0.85, min(160, max(16, int(length / 2))), dtype=np.float32)
        bases = start + fractions[:, None] * edge
        points = bases[:, None, :] + offsets[None, :, None] * normal
        samples = cv2.remap(image, points[..., 0], points[..., 1], cv2.INTER_LINEAR,
                            borderMode=cv2.BORDER_REPLICATE).astype(np.float32)
        # 蓝纸相对白纸/黑框的色差，避免把碎片投影的暗影当成纸片边缘。
        profile = np.minimum(samples[..., 0], samples[..., 1]) - samples[..., 2]
        profile = cv2.GaussianBlur(profile, (5, 1), 0)
        gradient = cv2.Sobel(profile, cv2.CV_32F, 1, 0, ksize=3) / 8
        if not paper:
            gradient = -gradient  # 碎片内侧由蓝纸过渡到白纸。
        indexes = np.argmax(gradient[:, 3:-3], axis=1) + 3
        rows = np.arange(len(indexes))
        peaks = gradient[rows, indexes]
        left, right = gradient[rows, indexes - 1], gradient[rows, indexes + 1]
        curvature = left - 2 * peaks + right
        delta = np.divide(0.5 * (left - right), curvature,
                          out=np.zeros_like(peaks), where=curvature < -1e-6)
        delta = np.clip(delta, -0.5, 0.5)
        valid = (peaks > max(3, float(np.median(peaks)) * 0.3))
        if np.count_nonzero(valid) < len(indexes) * 0.65:
            return contour
        selected = bases[valid] + (offsets[indexes[valid]] + delta[valid])[:, None] * normal
        vx, vy, x, y = cv2.fitLine(selected, cv2.DIST_HUBER, 0, 0.01, 0.01).reshape(4)
        fitted_normal = np.array([-vy, vx], np.float32)
        residual = np.abs((selected - (x, y)) @ fitted_normal)
        if float(np.quantile(residual, 0.9)) > 1.5:
            return contour
        lines.append((fitted_normal, -float(fitted_normal @ (x, y))))
    refined = []
    for index, (normal, offset) in enumerate(lines):
        previous, previous_offset = lines[index - 1]
        matrix = np.stack((previous, normal))
        if abs(float(np.linalg.det(matrix))) < 0.08:
            return contour
        point = np.linalg.solve(matrix, -np.array([previous_offset, offset]))
        if np.linalg.norm(point - polygon[index]) > 2 * band + 2:
            return contour
        refined.append(point)
    result = np.asarray(refined, np.float32).reshape(-1, 1, 2)
    ratio = cv2.contourArea(result) / max(cv2.contourArea(contour), 1)
    if not 0.85 <= ratio <= 1.15 or (paper and not cv2.isContourConvex(result)):
        return contour
    return result
