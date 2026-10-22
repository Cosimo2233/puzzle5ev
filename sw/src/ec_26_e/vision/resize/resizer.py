"""在参考位置附近精确检测黑底上的蓝色 A4 并执行透视校正。"""

from __future__ import annotations

from dataclasses import dataclass
from math import cos, radians

import cv2
import numpy as np

from ec_26_e.vision._blue import validate_bgr_image
from ec_26_e.vision.resize.contracts import (
    ASPECT_RATIO_TOLERANCE,
    ResizeConfig,
    ResizeResult,
    aspect_error,
)


_EDGE_SAMPLE_MARGIN_RATIO = 0.06
# 候选边缘两侧最多需要 8 个采样点，额外保留一个端点。
_EDGE_PROFILE_PADDING_PX = 9
_MIN_EDGE_SUPPORT_RATIO = 0.45
_MIN_EDGE_SPAN_RATIO = 0.52
_MAX_EDGE_ANGLE_DEGREES = 20.0
_MAX_COARSE_RESIDUAL_PX = 2.2
_MAX_FINE_RESIDUAL_PX = 2.0
_FINE_SEARCH_BAND_RATIO = 0.017
_MIN_EDGE_LUMINANCE_ADVANTAGE = 18.0
_MIN_EDGE_LUMINANCE_SAMPLE_ADVANTAGE = 10.0
_MIN_EDGE_LUMINANCE_SUPPORT_RATIO = 0.70
_MIN_RELIABLE_PAPER_LUMINANCE = 25.0
_MAX_RELIABLE_PAPER_LUMINANCE = 248.0
_MIN_RELIABLE_PAPER_SAMPLE_RATIO = 0.35
_MIN_PAPER_CHROMA = 24.0
_MIN_PAPER_CHROMA_RATIO = 0.55
_FALLBACK_LINE_ANGLE_DEGREES = 18.0
_FALLBACK_CANNY_LOW = 24
_FALLBACK_CANNY_HIGH = 72


@dataclass(frozen=True, slots=True)
class _EdgeFit:
    """保存一条有向黑蓝边界的稳健拟合结果。"""

    point: np.ndarray
    direction: np.ndarray
    residual: float
    support_ratio: float
    gradient_threshold: float


def _luminance_image(image_bgr: np.ndarray, *, blurred=None) -> np.ndarray:
    """生成用于亚像素边缘定位的平滑 Lab 明度图。"""

    if blurred is None:
        blurred = cv2.GaussianBlur(image_bgr, (5, 5), 0)
    return cv2.cvtColor(
        blurred,
        cv2.COLOR_BGR2LAB,
    )[..., 0].astype(np.float32)


def _paper_chroma_image(image_bgr: np.ndarray, *, blurred=None) -> np.ndarray:
    """生成青蓝卡纸相对红通道的非归一化绝对色差。"""

    if blurred is None:
        blurred = cv2.GaussianBlur(image_bgr, (5, 5), 0)
    # min(B-R, G-R) == min(B,G)-R. Keep the channels in uint8 until
    # the one output allocation; the integer differences are exact in float32.
    # This avoids a full three-channel float image, split copies and temporaries.
    chroma = np.minimum(blurred[..., 0], blurred[..., 1]).astype(np.float32)
    np.subtract(chroma, blurred[..., 2], out=chroma)
    return chroma


class _WorkspaceImages:
    """只在一帧定位期间复用中间图，失败回退也使用当前帧。"""

    def __init__(self, image_bgr):
        self.image_bgr = image_bgr
        self._blurred = self._luminance = self._chroma = None

    def blurred(self):
        if self._blurred is None:
            self._blurred = cv2.GaussianBlur(self.image_bgr, (5, 5), 0)
        return self._blurred

    def luminance(self):
        if self._luminance is None:
            self._luminance = _luminance_image(self.image_bgr, blurred=self.blurred())
        return self._luminance

    def chroma(self):
        if self._chroma is None:
            self._chroma = _paper_chroma_image(self.image_bgr, blurred=self.blurred())
        return self._chroma


class _WorkspaceEdgeImages:
    """逐边计算原分辨率 Lab/色差；保留卷积边界和完整定位检查。"""

    def __init__(self, image, seed, band):
        height, width = image.shape[:2]
        # Include the search band, appearance samples and bilinear neighbours.
        padding = int(np.ceil(2 * band + max(10, .018 * min(width, height)) + 3))
        self.bounds = []
        self._luminance = np.full((height, width), np.nan, np.float32)
        self._chroma = np.full((height, width), np.nan, np.float32)
        for start, end in zip(seed, np.roll(seed, -1, axis=0)):
            low = np.floor(np.minimum(start, end)).astype(int) - padding
            high = np.ceil(np.maximum(start, end)).astype(int) + padding + 1
            x0, y0 = np.maximum(low, 0)
            x1, y1 = np.minimum(high, (width, height))
            self.bounds.append((x0, y0, x1, y1))
            # A 5x5 blur needs two extra source pixels. Crop only afterwards;
            # image-border handling is identical to the full-image operation.
            sx, sy = max(0, x0 - 2), max(0, y0 - 2)
            ex, ey = min(width, x1 + 2), min(height, y1 + 2)
            blurred = cv2.GaussianBlur(image[sy:ey, sx:ex], (5, 5), 0)
            blurred = blurred[y0-sy:y1-sy, x0-sx:x1-sx]
            self._luminance[y0:y1, x0:x1] = _luminance_image(blurred, blurred=blurred)
            self._chroma[y0:y1, x0:x1] = _paper_chroma_image(blurred, blurred=blurred)

    def luminance(self):
        return self._luminance

    def chroma(self):
        return self._chroma

    def require_appearance_coverage(self, corners):
        """候选偏离窄带时回到完整搜索，不读取未计算的像素。"""
        padding = max(10.0, .018 * min(self._luminance.shape)) + 2
        height, width = self._luminance.shape
        for index, (start, end) in enumerate(zip(corners, np.roll(corners, -1, axis=0))):
            low = np.maximum(np.minimum(start, end) - padding, 0)
            high = np.minimum(np.maximum(start, end) + padding, (width-1, height-1))
            x0, y0, x1, y1 = self.bounds[index]
            if not (low[0] >= x0 and low[1] >= y0 and high[0] < x1 and high[1] < y1):
                raise ValueError('新工作区边缘超出当前计算区域，重新完整定位')


def _reference_corners(
    config: ResizeConfig,
    image_size: tuple[int, int],
) -> np.ndarray:
    """把配置中的相对参考四角转换为当前图像像素坐标。"""

    width, height = image_size
    scale = np.asarray(
        [max(width - 1, 1), max(height - 1, 1)],
        dtype=np.float32,
    )
    return (
        np.asarray(
            config.reference_corners_relative,
            dtype=np.float32,
        )
        * scale
    )


def _signed_polygon_area(points: np.ndarray) -> float:
    """计算图像坐标系中的有向多边形面积。"""

    x, y = points[:, 0], points[:, 1]
    return 0.5 * float(
        np.sum(x * np.roll(y, -1) - y * np.roll(x, -1))
    )


def _line_intersection(first: _EdgeFit, second: _EdgeFit) -> np.ndarray:
    """计算两条相邻拟合直线的交点。"""

    matrix = np.column_stack((first.direction, -second.direction))
    determinant = float(np.linalg.det(matrix))
    if abs(determinant) < 0.08:
        raise ValueError("A4 相邻边近似平行，无法稳定求交")
    parameters = np.linalg.solve(
        matrix,
        second.point - first.point,
    )
    return first.point + parameters[0] * first.direction


def _profile_side_means(
    profiles: np.ndarray,
    indices: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """计算候选边缘内外两侧各六个采样点的均值。"""

    row_indices = np.arange(len(indices))[:, np.newaxis]
    steps = np.arange(3, 9)[np.newaxis, :]
    inside_indices = np.clip(
        indices[:, np.newaxis] + steps,
        0,
        profiles.shape[1] - 1,
    )
    outside_indices = np.clip(
        indices[:, np.newaxis] - steps,
        0,
        profiles.shape[1] - 1,
    )
    return (
        np.mean(profiles[row_indices, inside_indices], axis=1),
        np.mean(profiles[row_indices, outside_indices], axis=1),
    )


def _fit_transition_edge(
    luminance: np.ndarray,
    start: np.ndarray,
    end: np.ndarray,
    search_band_px: float,
    max_residual_px: float,
) -> _EdgeFit:
    """沿参考边法线寻找由外侧黑底指向内侧蓝纸的明度跃迁。"""

    edge = end - start
    length = float(np.linalg.norm(edge))
    if length < 20.0:
        raise ValueError("A4 参考边过短")
    expected_direction = edge / length
    inward_normal = np.asarray(
        [-expected_direction[1], expected_direction[0]],
        dtype=np.float32,
    )
    sample_count = max(80, int(round(length * 0.75)))
    positions = np.linspace(
        _EDGE_SAMPLE_MARGIN_RATIO * length,
        (1.0 - _EDGE_SAMPLE_MARGIN_RATIO) * length,
        sample_count,
        dtype=np.float32,
    )
    offsets = np.arange(
        -search_band_px - _EDGE_PROFILE_PADDING_PX,
        search_band_px + _EDGE_PROFILE_PADDING_PX + 1.0,
        1.0,
        dtype=np.float32,
    )
    if len(offsets) < 21:
        raise ValueError("A4 边缘搜索带过窄")

    bases = (
        start[np.newaxis, :]
        + positions[:, np.newaxis] * expected_direction[np.newaxis, :]
    )
    sample_points = (
        bases[:, np.newaxis, :]
        + offsets[np.newaxis, :, np.newaxis]
        * inward_normal[np.newaxis, np.newaxis, :]
    ).astype(np.float32)
    profiles = cv2.remap(
        luminance,
        sample_points[..., 0],
        sample_points[..., 1],
        cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REPLICATE,
    )
    profiles = cv2.GaussianBlur(profiles, (7, 1), 0)
    gradients = (
        cv2.Sobel(profiles, cv2.CV_32F, 1, 0, ksize=3) / 8.0
    )
    candidate_indices = (
        # 填充仅用于两侧取样，候选边缘仍严格限制在原搜索带内。
        np.argmax(gradients[:, _EDGE_PROFILE_PADDING_PX:-_EDGE_PROFILE_PADDING_PX], axis=1)
        + _EDGE_PROFILE_PADDING_PX
    )
    row_indices = np.arange(sample_count)
    strengths = gradients[row_indices, candidate_indices]
    inside_mean, outside_mean = _profile_side_means(
        profiles,
        candidate_indices,
    )
    contrast = inside_mean - outside_mean
    adaptive_threshold = max(
        3.0,
        0.20 * float(np.median(np.maximum(strengths, 0.0))),
    )
    valid = (
        (strengths >= adaptive_threshold)
        & (contrast >= max(6.0, 1.2 * adaptive_threshold))
        & (candidate_indices > 5)
        & (candidate_indices < len(offsets) - 6)
    )
    support_ratio = float(np.count_nonzero(valid) / sample_count)
    if support_ratio < _MIN_EDGE_SUPPORT_RATIO:
        raise ValueError(
            "A4 边缘有效支持不足："
            f"{support_ratio:.3f} < {_MIN_EDGE_SUPPORT_RATIO:.3f}"
        )

    left = gradients[row_indices, candidate_indices - 1]
    right = gradients[row_indices, candidate_indices + 1]
    curvature = left - 2.0 * strengths + right
    subpixel_offsets = np.divide(
        0.5 * (left - right), curvature,
        out=np.zeros_like(strengths), where=curvature < -1e-6,
    )
    subpixel_offsets = np.clip(subpixel_offsets, -0.5, 0.5)
    peak_offsets = np.clip(
        offsets[candidate_indices] + subpixel_offsets,
        offsets[_EDGE_PROFILE_PADDING_PX], offsets[-_EDGE_PROFILE_PADDING_PX - 1],
    )
    selected = (
        bases[valid]
        + peak_offsets[valid, np.newaxis] * inward_normal
    ).astype(np.float32)
    for _ in range(3):
        vx, vy, x0, y0 = cv2.fitLine(
            selected,
            cv2.DIST_HUBER,
            0,
            0.01,
            0.01,
        ).reshape(4)
        direction = np.asarray([vx, vy], dtype=np.float64)
        direction /= np.linalg.norm(direction) + 1e-12
        if float(direction @ expected_direction) < 0.0:
            direction = -direction
        point = np.asarray([x0, y0], dtype=np.float64)
        residuals = np.abs(
            (selected[:, 0] - point[0]) * direction[1]
            - (selected[:, 1] - point[1]) * direction[0]
        )
        median = float(np.median(residuals))
        mad = float(np.median(np.abs(residuals - median)))
        limit = max(1.25, median + 3.0 * 1.4826 * mad)
        inliers = selected[residuals <= limit]
        if len(inliers) < _MIN_EDGE_SUPPORT_RATIO * sample_count:
            raise ValueError("A4 边缘稳健拟合后的有效点不足")
        if len(inliers) == len(selected):
            break
        selected = inliers

    vx, vy, x0, y0 = cv2.fitLine(
        selected,
        cv2.DIST_HUBER,
        0,
        0.01,
        0.01,
    ).reshape(4)
    direction = np.asarray([vx, vy], dtype=np.float64)
    direction /= np.linalg.norm(direction) + 1e-12
    if float(direction @ expected_direction) < 0.0:
        direction = -direction
    point = np.asarray([x0, y0], dtype=np.float64)
    projection = (selected - point) @ direction
    if float(np.ptp(projection)) < _MIN_EDGE_SPAN_RATIO * length:
        raise ValueError("A4 边缘可见跨度不足")
    residuals = np.abs(
        (selected[:, 0] - point[0]) * direction[1]
        - (selected[:, 1] - point[1]) * direction[0]
    )
    residual = float(np.mean(residuals))
    if residual > max_residual_px:
        raise ValueError(
            f"A4 边缘拟合残差过大：{residual:.3f} "
            f"> {max_residual_px:.3f}"
        )
    return _EdgeFit(
        point=point,
        direction=direction,
        residual=residual,
        support_ratio=float(len(selected) / sample_count),
        gradient_threshold=adaptive_threshold,
    )


def _fit_four_edges(
    luminance: np.ndarray,
    seed_corners: np.ndarray,
    search_band_px: float,
    max_residual_px: float,
) -> tuple[np.ndarray, tuple[_EdgeFit, ...]]:
    """按 TL、TR、BR、BL 种子依次拟合四条有向边并恢复四角。"""

    edges = tuple(
        _fit_transition_edge(
            luminance,
            seed_corners[index],
            seed_corners[(index + 1) % 4],
            search_band_px,
            max_residual_px,
        )
        for index in range(4)
    )
    corners = np.asarray(
        [
            _line_intersection(edges[index - 1], edges[index])
            for index in range(4)
        ],
        dtype=np.float32,
    )
    return corners, edges


def _validate_geometry(
    corners: np.ndarray,
    reference: np.ndarray,
    image_size: tuple[int, int],
    min_workspace_ratio: float,
    search_band_ratio: float,
) -> None:
    """校验最终四角完整可见、接近参考姿态且形成足够大的凸四边形。"""

    width, height = image_size
    if not np.all(np.isfinite(corners)):
        raise ValueError("A4 四角包含非有限坐标")
    if (
        np.any(corners[:, 0] < 2.0)
        or np.any(corners[:, 0] > width - 3.0)
        or np.any(corners[:, 1] < 2.0)
        or np.any(corners[:, 1] > height - 3.0)
    ):
        raise ValueError("A4 边界不完整或接触图像边缘")
    integer_corners = np.rint(corners).astype(np.int32).reshape(-1, 1, 2)
    if not cv2.isContourConvex(integer_corners):
        raise ValueError("A4 四角未形成凸四边形")
    if _signed_polygon_area(corners) <= 0.0:
        raise ValueError("A4 四角环绕方向错误")

    area_ratio = abs(cv2.contourArea(corners)) / float(width * height)
    if area_ratio < min_workspace_ratio:
        raise ValueError(
            f"A4 工作区占比过小：{area_ratio:.3f} "
            f"< {min_workspace_ratio:.3f}"
        )
    side_lengths = np.linalg.norm(
        np.roll(corners, -1, axis=0) - corners,
        axis=1,
    )
    if float(np.min(side_lengths)) < 0.15 * min(width, height):
        raise ValueError("A4 四边形包含过短边")

    maximum_normal_offset = (
        1.15 * search_band_ratio * min(width, height)
    )
    minimum_direction_dot = cos(radians(_MAX_EDGE_ANGLE_DEGREES))
    for index in range(4):
        reference_edge = (
            reference[(index + 1) % 4] - reference[index]
        )
        candidate_edge = (
            corners[(index + 1) % 4] - corners[index]
        )
        reference_length = float(np.linalg.norm(reference_edge))
        candidate_length = float(np.linalg.norm(candidate_edge))
        reference_direction = reference_edge / reference_length
        candidate_direction = candidate_edge / candidate_length
        if float(reference_direction @ candidate_direction) < (
            minimum_direction_dot
        ):
            raise ValueError("A4 边方向偏离参考姿态过大")
        normal = np.asarray(
            [-reference_direction[1], reference_direction[0]]
        )
        midpoint_offset = abs(
            float(
                (
                    (corners[index] + corners[(index + 1) % 4]) / 2.0
                    - (
                        reference[index]
                        + reference[(index + 1) % 4]
                    )
                    / 2.0
                )
                @ normal
            )
        )
        if midpoint_offset > maximum_normal_offset:
            raise ValueError("A4 边位置超出参考搜索范围")


def _validate_workspace_appearance(
    image_bgr: np.ndarray,
    corners: np.ndarray,
    *,
    luminance=None,
    paper_chroma=None,
) -> float:
    """用外暗内亮边界和整体青蓝纸色确认候选工作区。"""

    if luminance is None:
        luminance = _luminance_image(image_bgr)
    if paper_chroma is None:
        paper_chroma = _paper_chroma_image(image_bgr)
    short_side = min(image_bgr.shape[:2])
    near_offset = max(5.0, 0.008 * short_side)
    far_offset = max(10.0, 0.018 * short_side)
    offsets = np.linspace(
        near_offset,
        far_offset,
        6,
        dtype=np.float32,
    )
    luminance_advantages: list[float] = []
    inside_luminance_samples: list[np.ndarray] = []
    inside_chroma_samples: list[np.ndarray] = []
    for index in range(4):
        start = corners[index]
        end = corners[(index + 1) % 4]
        edge = end - start
        length = float(np.linalg.norm(edge))
        direction = edge / length
        inward = np.asarray(
            [-direction[1], direction[0]],
            dtype=np.float32,
        )
        positions = np.linspace(
            0.12 * length,
            0.88 * length,
            max(80, int(round(0.25 * length))),
            dtype=np.float32,
        )
        bases = (
            start[np.newaxis, :]
            + positions[:, np.newaxis] * direction[np.newaxis, :]
        )
        inside_points = (
            bases[:, np.newaxis, :]
            + offsets[np.newaxis, :, np.newaxis]
            * inward[np.newaxis, np.newaxis, :]
        ).astype(np.float32)
        outside_points = (
            bases[:, np.newaxis, :]
            - offsets[np.newaxis, :, np.newaxis]
            * inward[np.newaxis, np.newaxis, :]
        ).astype(np.float32)
        inside_luminance = cv2.remap(
            luminance,
            inside_points[..., 0],
            inside_points[..., 1],
            cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_REPLICATE,
        )
        outside_luminance = cv2.remap(
            luminance,
            outside_points[..., 0],
            outside_points[..., 1],
            cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_REPLICATE,
        )
        luminance_difference = inside_luminance - outside_luminance
        luminance_advantage = float(np.median(luminance_difference))
        luminance_support_ratio = float(
            np.mean(
                luminance_difference
                >= _MIN_EDGE_LUMINANCE_SAMPLE_ADVANTAGE
            )
        )
        if luminance_advantage < _MIN_EDGE_LUMINANCE_ADVANTAGE:
            raise ValueError("A4 边界外暗内亮差不足")
        if (
            luminance_support_ratio
            < _MIN_EDGE_LUMINANCE_SUPPORT_RATIO
        ):
            raise ValueError("A4 边界外暗内亮支持不足")

        inside_chroma = cv2.remap(
            paper_chroma,
            inside_points[..., 0],
            inside_points[..., 1],
            cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_REPLICATE,
        )
        luminance_advantages.append(luminance_advantage)
        inside_luminance_samples.append(inside_luminance.reshape(-1))
        inside_chroma_samples.append(inside_chroma.reshape(-1))

    combined_luminance = np.concatenate(inside_luminance_samples)
    combined_chroma = np.concatenate(inside_chroma_samples)
    reliable = (
        (combined_luminance >= _MIN_RELIABLE_PAPER_LUMINANCE)
        & (combined_luminance <= _MAX_RELIABLE_PAPER_LUMINANCE)
    )
    reliable_ratio = float(np.mean(reliable))
    if reliable_ratio < _MIN_RELIABLE_PAPER_SAMPLE_RATIO:
        raise ValueError("A4 青蓝纸色的可靠曝光区域不足")

    reliable_chroma = combined_chroma[reliable]
    paper_chroma_median = float(np.median(reliable_chroma))
    paper_chroma_ratio = float(
        np.mean(reliable_chroma >= _MIN_PAPER_CHROMA)
    )
    if paper_chroma_median < _MIN_PAPER_CHROMA:
        raise ValueError("A4 内侧没有稳定的青蓝卡纸色差")
    if paper_chroma_ratio < _MIN_PAPER_CHROMA_RATIO:
        raise ValueError("A4 青蓝卡纸有效区域不足")
    return min(min(luminance_advantages), paper_chroma_median)


def _reference_edge_fit(
    start: np.ndarray,
    end: np.ndarray,
) -> _EdgeFit:
    """把参考边包装成兜底时可用的直线。"""

    direction = end - start
    direction = direction / (np.linalg.norm(direction) + 1e-12)
    return _EdgeFit(
        point=start.astype(np.float64),
        direction=direction.astype(np.float64),
        residual=0.0,
        support_ratio=0.0,
        gradient_threshold=0.0,
    )


def _fit_black_blue_fallback(
    image_bgr: np.ndarray,
    reference: np.ndarray,
    *,
    luminance=None,
    paper_chroma=None,
) -> np.ndarray:
    """由断续黑蓝边生成重试种子；调用方必须重新拟合并校验四边。

    找不到线段时保留参考边仅用于搜索，不能直接作为透视变换依据。
    """

    if paper_chroma is None:
        paper_chroma = _paper_chroma_image(image_bgr)
    chroma = np.clip(paper_chroma, 0, 255).astype(
        np.uint8
    )
    if luminance is None:
        luminance = _luminance_image(image_bgr)
    chroma = cv2.GaussianBlur(chroma, (5, 5), 0)
    edges = cv2.Canny(
        chroma,
        _FALLBACK_CANNY_LOW,
        _FALLBACK_CANNY_HIGH,
        apertureSize=3,
    )
    short_side = min(image_bgr.shape[:2])
    segments = cv2.HoughLinesP(
        edges,
        rho=1.0,
        theta=np.pi / 180.0,
        threshold=max(24, int(round(0.035 * short_side))),
        minLineLength=max(18, int(round(0.055 * short_side))),
        maxLineGap=max(12, int(round(0.035 * short_side))),
    )
    grouped_points: list[list[np.ndarray]] = [[], [], [], []]
    if segments is not None:
        minimum_dot = cos(radians(_FALLBACK_LINE_ANGLE_DEGREES))
        reference_directions = []
        reference_midpoints = []
        for index in range(4):
            start = reference[index]
            end = reference[(index + 1) % 4]
            direction = end - start
            direction /= np.linalg.norm(direction) + 1e-12
            reference_directions.append(direction)
            reference_midpoints.append((start + end) / 2.0)

        for segment in segments.reshape(-1, 4):
            start = segment[:2].astype(np.float64)
            end = segment[2:].astype(np.float64)
            direction = end - start
            length = float(np.linalg.norm(direction))
            if length < 1e-6:
                continue
            direction /= length
            eligible = [
                index
                for index, reference_direction in enumerate(
                    reference_directions
                )
                if abs(float(direction @ reference_direction)) >= minimum_dot
            ]
            if not eligible:
                continue
            midpoint = (start + end) / 2.0
            # 同向的上下（或左右）边仅按与参考边的法向距离归属，
            # 不要求线段连续、闭合或满足额外的可信度条件。
            index = min(
                eligible,
                key=lambda candidate: abs(
                    float(
                        (midpoint - reference_midpoints[candidate])
                        @ np.asarray(
                            [
                                -reference_directions[candidate][1],
                                reference_directions[candidate][0],
                            ]
                        )
                    )
                ),
            )
            edge_distance = abs(
                float(
                    (midpoint - reference_midpoints[index])
                    @ np.asarray(
                        [
                            -reference_directions[index][1],
                            reference_directions[index][0],
                        ]
                    )
                )
            )
            if edge_distance > 0.13 * short_side:
                continue
            inward = np.asarray(
                [
                    -reference_directions[index][1],
                    reference_directions[index][0],
                ],
                dtype=np.float32,
            )
            inside_point = (midpoint + 8.0 * inward).astype(np.float32)
            outside_point = (midpoint - 8.0 * inward).astype(np.float32)
            inside_chroma = float(
                cv2.getRectSubPix(chroma, (1, 1), inside_point)[0, 0]
            )
            outside_chroma = float(
                cv2.getRectSubPix(chroma, (1, 1), outside_point)[0, 0]
            )
            outside_luminance = float(
                cv2.getRectSubPix(luminance, (1, 1), outside_point)[0, 0]
            )
            # 只把外黑内蓝的边送入拟合。纸片和蓝纸内部接缝不具备
            # 这个方向关系，因此不会把它们连成工作区边。
            if (
                inside_chroma < outside_chroma + 5.0
                or outside_luminance > 100.0
            ):
                continue
            grouped_points[index].extend((start, end))

    edge_fits: list[_EdgeFit] = []
    for index, points in enumerate(grouped_points):
        start = reference[index]
        end = reference[(index + 1) % 4]
        if len(points) < 4:
            edge_fits.append(_reference_edge_fit(start, end))
            continue
        points_array = np.asarray(points, dtype=np.float32)
        vx, vy, x0, y0 = cv2.fitLine(
            points_array,
            cv2.DIST_HUBER,
            0,
            0.01,
            0.01,
        ).reshape(4)
        direction = np.asarray([vx, vy], dtype=np.float64)
        direction /= np.linalg.norm(direction) + 1e-12
        expected = end - start
        expected /= np.linalg.norm(expected) + 1e-12
        if float(direction @ expected) < 0.0:
            direction = -direction
        edge_fits.append(
            _EdgeFit(
                point=np.asarray([x0, y0], dtype=np.float64),
                direction=direction,
                residual=0.0,
                support_ratio=1.0,
                gradient_threshold=0.0,
            )
        )

    corners = np.asarray(
        [
            _line_intersection(edge_fits[index - 1], edge_fits[index])
            for index in range(4)
        ],
        dtype=np.float32,
    )
    if not np.all(np.isfinite(corners)):
        return reference.copy()
    return corners


def _locate_workspace(
    image_bgr: np.ndarray,
    config: ResizeConfig,
    source_seed: np.ndarray | None = None,
) -> tuple[np.ndarray, float, float, float]:
    """先粗定位再在原图精拟合四边并返回几何诊断。"""

    source_height, source_width = image_bgr.shape[:2]
    source_images = _WorkspaceImages(image_bgr)
    source_reference = _reference_corners(config, (source_width, source_height))
    if source_seed is not None:
        try:
            # The previous corners only seed a fresh four-edge fit. Every frame
            # still passes the original geometric and appearance checks; neither
            # the old homography nor an old image is reused as an observation.
            band = max(6.0, _FINE_SEARCH_BAND_RATIO * min(source_width, source_height))
            edge_images = _WorkspaceEdgeImages(image_bgr, source_seed, band)
            corners, edges = _fit_four_edges(
                edge_images.luminance(), source_seed, band,
                _MAX_FINE_RESIDUAL_PX,
            )
            _validate_geometry(corners, source_reference, (source_width, source_height),
                               config.min_workspace_ratio, config.search_band_ratio)
            edge_images.require_appearance_coverage(corners)
            appearance = _validate_workspace_appearance(
                image_bgr, corners, luminance=edge_images.luminance(),
                paper_chroma=edge_images.chroma())
            return (corners, min(float(np.mean([e.gradient_threshold for e in edges])), appearance),
                    abs(cv2.contourArea(corners))/(source_width*source_height),
                    float(np.mean([e.residual for e in edges])))
        except ValueError:
            pass  # Retry the complete coarse/fine search on this same frame.
    detect_size = (config.detect_width, config.detect_height)
    if detect_size == (source_width, source_height):
        detection_image = image_bgr
    else:
        detection_image = cv2.resize(
            image_bgr,
            detect_size,
            interpolation=cv2.INTER_AREA,
        )

    detection_images = source_images if detection_image is image_bgr else _WorkspaceImages(detection_image)
    detect_reference = _reference_corners(config, detect_size)
    coarse_band = max(
        10.0,
        config.search_band_ratio * min(detect_size),
    )
    source_reference = _reference_corners(
        config,
        (source_width, source_height),
    )
    try:
        coarse_corners, coarse_edges = _fit_four_edges(
            detection_images.luminance(),
            detect_reference,
            coarse_band,
            _MAX_COARSE_RESIDUAL_PX,
        )
        scale = np.asarray(
            [
                (source_width - 1) / max(config.detect_width - 1, 1),
                (source_height - 1) / max(config.detect_height - 1, 1),
            ],
            dtype=np.float32,
        )
        source_seed = coarse_corners * scale
        fine_band = max(
            6.0,
            _FINE_SEARCH_BAND_RATIO * min(source_width, source_height),
        )
        source_corners, fine_edges = _fit_four_edges(
            source_images.luminance(),
            source_seed,
            fine_band,
            _MAX_FINE_RESIDUAL_PX,
        )
        _validate_geometry(
            source_corners,
            source_reference,
            (source_width, source_height),
            config.min_workspace_ratio,
            config.search_band_ratio,
        )
        appearance_score = _validate_workspace_appearance(
            image_bgr,
            source_corners,
            luminance=source_images.luminance(),
            paper_chroma=source_images.chroma(),
        )
    except ValueError as primary_error:
        source_seed = _fit_black_blue_fallback(
            image_bgr,
            source_reference,
            luminance=source_images.luminance(),
            paper_chroma=source_images.chroma(),
        )
        coarse_edges = ()
        try:
            source_corners, fine_edges = _fit_four_edges(
                source_images.luminance(), source_seed,
                max(6.0, _FINE_SEARCH_BAND_RATIO * min(source_width, source_height)),
                _MAX_FINE_RESIDUAL_PX,
            )
            _validate_geometry(
                source_corners, source_reference, (source_width, source_height),
                config.min_workspace_ratio, config.search_band_ratio,
            )
            appearance_score = _validate_workspace_appearance(
                image_bgr, source_corners, luminance=source_images.luminance(),
                paper_chroma=source_images.chroma(),
            )
        except ValueError as fallback_error:
            raise ValueError(
                f"工作区定位失败，请检查参考四角、遮挡或光照：{primary_error}；"
                f"边线重试：{fallback_error}"
            ) from fallback_error
    workspace_ratio = abs(
        cv2.contourArea(source_corners)
    ) / float(source_width * source_height)
    line_residual = float(
        np.mean([edge.residual for edge in fine_edges])
        if fine_edges else 0.0
    )
    gradient_threshold = float(
        np.mean(
            [
                edge.gradient_threshold
                for edge in (*coarse_edges, *fine_edges)
            ]
        )
        if coarse_edges or fine_edges
        else 0.0
    )
    diagnostic_threshold = min(
        gradient_threshold,
        appearance_score,
    )
    return (
        source_corners,
        diagnostic_threshold,
        workspace_ratio,
        line_residual,
    )


class Resize:
    """封装参考位置约束的蓝色 A4 定位和透视校正能力。"""

    def __init__(self, config: ResizeConfig | None = None) -> None:
        """保存已经校验的配置，构造阶段不处理图像。"""

        if config is not None and not isinstance(config, ResizeConfig):
            raise TypeError("config 必须是 ResizeConfig 或 None")
        self.config = config or ResizeConfig()
        self._source_seed = None
        self._source_size = None

    def resize(self, image_bgr: np.ndarray) -> ResizeResult:
        """定位黑底上的蓝色 A4 并返回原图直接透视校正结果。"""

        validate_bgr_image(image_bgr)
        source_height, source_width = image_bgr.shape[:2]
        if (
            self.config.detect_width > source_width
            or self.config.detect_height > source_height
        ):
            raise ValueError("工作区定位分辨率不能大于输入图像")
        source_ratio = source_width / source_height
        detect_ratio = (
            self.config.detect_width / self.config.detect_height
        )
        if aspect_error(detect_ratio, source_ratio) > (
            ASPECT_RATIO_TOLERANCE
        ):
            raise ValueError(
                "工作区定位分辨率必须保持输入图像宽高比，"
                "允许的相对误差为 0.5%"
            )

        try:
            (source_corners, threshold, workspace_ratio, line_residual) = _locate_workspace(
                image_bgr, self.config,
                self._source_seed if self._source_size == (source_width, source_height) else None)
        except ValueError:
            self._source_seed = self._source_size = None
            raise
        self._source_seed = source_corners.copy()
        self._source_size = (source_width, source_height)
        target_width = self.config.target_width
        target_height = self.config.target_height
        target_corners = np.asarray(
            [
                [0.0, 0.0],
                [target_width - 1.0, 0.0],
                [target_width - 1.0, target_height - 1.0],
                [0.0, target_height - 1.0],
            ],
            dtype=np.float32,
        )
        homography = cv2.getPerspectiveTransform(
            source_corners.astype(np.float32),
            target_corners,
        )
        rectified = cv2.warpPerspective(
            image_bgr,
            homography,
            (target_width, target_height),
            flags=cv2.INTER_LINEAR,
        )
        return ResizeResult(
            image_bgr=rectified,
            source_size=(source_width, source_height),
            target_size=(target_width, target_height),
            source_corners_px=tuple(
                (round(float(x), 2), round(float(y), 2))
                for x, y in source_corners
            ),
            homography=homography,
            workspace_threshold=round(threshold, 4),
            workspace_ratio=round(workspace_ratio, 4),
            line_residual=round(line_residual, 3),
        )
