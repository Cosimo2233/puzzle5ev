"""无模型轮廓检测，保留凹角；整帧不完整时不输出局部结果。"""
import time
import cv2
import numpy as np
from ec_26_e.vision.contracts import Detect1Config, Detect1Result, PieceDetection, InstanceDiagnostics, VisionDiagnostics, validate_a4_image_size
from ec_26_e.vision.geometry import canonicalize_corners, is_simple_polygon, polygon_edge_lengths, polygon_iou, relative_point, polygon_corner_depths, polygon_turn_angles_deg
from ec_26_e.vision.preprocess import CpuPreprocessor
from ec_26_e.vision.outline_edges import refine_polygon_edges

def supported_shallow_corner(physical, residual_mm, iou):
    """浅角必须有长边和高信噪比支持，不能仅凭近似多边形放行。"""
    return bool(3 <= len(physical) <= 5 and residual_mm <= 0.35 and iou >= 0.96
        and polygon_edge_lengths(physical).min() >= 16.0
        and polygon_corner_depths(physical).min() >= max(1.0, 6 * residual_mm)
        and np.abs(polygon_turn_angles_deg(physical)).min() >= 3.0)

def fit_polygon(contour, epsilon, *, mm_scale=None, config=None):
    """按轮廓顺序分边、鲁棒直线拟合及求交；不使用凸包。"""
    points = contour.reshape(-1, 2).astype(np.float64)
    coarse = cv2.approxPolyDP(contour, epsilon, True).reshape(-1, 2)
    if len(coarse) < 3:
        raise ValueError("轮廓退化")
    if config is not None:
        # 掩膜形态学可能把尖角削成很短的凸边。仅合并同时缺少角深度
        # 证据的凸角端点；保留凹角，后续仍用原轮廓拟合并检查全部质量门槛。
        while len(coarse) > config.min_vertices:
            physical = coarse * mm_scale
            lengths = polygon_edge_lengths(physical)
            turns = polygon_turn_angles_deg(physical) * np.sign(cv2.contourArea(coarse, oriented=True))
            depths = polygon_corner_depths(physical)
            removable = []
            for start in np.flatnonzero(lengths < config.short_edge_merge_mm):
                end = (start + 1) % len(coarse)
                if turns[start] <= 0 or turns[end] <= 0:
                    continue
                for vertex in (start, end):
                    if depths[vertex] < config.minimum_corner_depth_mm:
                        removable.append((depths[vertex], int(vertex)))
            if not removable:
                break
            coarse = np.delete(coarse, min(removable)[1], axis=0)
    indices = [int(np.argmin(np.linalg.norm(points - p, axis=1))) for p in coarse]
    lines, residuals = [], []
    for i, start in enumerate(indices):
        end = indices[(i + 1) % len(indices)]
        edge = points[start:end + 1] if end > start else np.concatenate((points[start:], points[:end + 1]))
        trim = min(3, (len(edge) - 2) // 2)
        edge = edge[trim:len(edge) - trim] if trim > 0 else edge
        if len(edge) < 2:
            raise ValueError("边支持不足")
        vx, vy, x, y = cv2.fitLine(edge.astype(np.float32), cv2.DIST_HUBER, 0, 0.01, 0.01).reshape(-1)
        normal = np.array([-vy, vx], dtype=np.float64)
        offset = -float(normal @ np.array([x, y]))
        residuals.extend(np.abs(edge @ normal + offset).tolist())
        lines.append((normal, offset))
    corners = []
    for i, (normal, offset) in enumerate(lines):
        previous, previous_offset = lines[i - 1]
        matrix = np.stack((previous, normal))
        if abs(np.linalg.det(matrix)) < 0.05:
            raise ValueError("相邻边近似平行")
        corners.append(np.linalg.solve(matrix, -np.array([previous_offset, offset])))
    return canonicalize_corners(np.asarray(corners)), float(np.quantile(residuals, 0.95))

class TraditionalVisionDetector:
    def __init__(self, config=None, *, expected_pieces=4, preprocessor=None):
        if expected_pieces is not None and (
            type(expected_pieces) is not int or not 2 <= expected_pieces <= 4
        ):
            raise ValueError("expected_pieces 必须为 2～4 或 None（自动）")
        self.config = config or Detect1Config(short_edge_merge_mm=8, minimum_physical_edge_mm=8)
        self.expected_pieces = expected_pieces
        self.preprocessor = preprocessor or CpuPreprocessor()
        self.last_diagnostics = VisionDiagnostics()
        self.last_failure = None
        self.last_preview_contours = ()
        self._frame_id = 0
        self._mask_buffer = None

    def detect(self, image):
        started = time.perf_counter()
        self.last_preview_contours = ()
        height, width = image.shape[:2]
        validate_a4_image_size(width, height)
        self._frame_id += 1
        if callable(getattr(self.preprocessor, 'process_into', None)):
            if self._mask_buffer is None or self._mask_buffer.shape != (height, width):
                self._mask_buffer = np.empty((height, width), dtype=np.uint8)
            mask = self.preprocessor.process_into(image, self._frame_id, self._mask_buffer)
        else:
            mask = self.preprocessor.process(image, self._frame_id)
        if mask.dtype != np.uint8 or mask.shape != (height, width) or not np.all((mask == 0) | (mask == 255)):
            raise ValueError("预处理返回非法掩膜")
        preprocessing_ms = (time.perf_counter() - started) * 1000
        self.last_failure = None
        self.last_diagnostics = VisionDiagnostics()
        margin = max(2, round(min(width, height) * self.config.workspace_erode_ratio))
        # External contours intentionally ignore enclosed card-print holes. Never use a
        # convex hull or large closing kernel: those would erase real concave seams.
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        contours = [c for c in contours if cv2.contourArea(c) >= width * height * self.config.min_piece_area_ratio]
        # 仅用于预览：片数不符时仍可观察当前候选边界，不能交给规划器。
        self.last_preview_contours = tuple(contours)
        pieces = []
        try:
            if self.expected_pieces is None and not 2 <= len(contours) <= 4:
                raise ValueError(f"随机拼图需要 2～4 片，检测到 {len(contours)} 个连通域；请分开放置")
            if self.expected_pieces is not None and len(contours) != self.expected_pieces:
                raise ValueError(f"预期 {self.expected_pieces} 片，检测到 {len(contours)} 个连通域；请分开放置")
            candidates = []
            for contour in contours:
                if cv2.contourArea(contour) > width * height * self.config.max_piece_area_ratio:
                    raise ValueError("连通域过大，疑似粘连或遮挡")
                x, y, w, h = cv2.boundingRect(contour)
                if x <= margin or y <= margin or x + w >= width - margin or y + h >= height - margin:
                    raise ValueError("纸片接触工作区边界")
                mm_scale = np.array([292.0 / (width - 1), 207.0 / (height - 1)])
                polygon, residual = fit_polygon(
                    contour, cv2.arcLength(contour, True) * self.config.polygon_epsilon_ratio,
                    mm_scale=mm_scale, config=self.config)
                if self.expected_pieces is None and len(polygon) < self.config.max_vertices:
                    # Perimeter-relative simplification can delete a real shallow fifth
                    # corner. Recover it only if separate straight edges explain the data.
                    for factor in (0.5, 0.25):
                        try:
                            candidate, candidate_residual = fit_polygon(contour,
                                cv2.arcLength(contour, True) * self.config.polygon_epsilon_ratio * factor,
                                mm_scale=mm_scale, config=self.config)
                        except ValueError:
                            continue
                        if len(candidate) > len(polygon) and supported_shallow_corner(candidate * mm_scale,
                                candidate_residual * max(mm_scale), polygon_iou(contour, candidate)):
                            polygon, residual = candidate, candidate_residual
                            break
                # PL 掩膜提供候选和边序；原图色差梯度细化交点，避免阈值和阶梯边造成偏移。
                polygon = canonicalize_corners(refine_polygon_edges(
                    image, polygon.astype(np.float32).reshape(-1, 1, 2),
                    band=3, is_polygon=True).reshape(-1, 2))
                physical = polygon * mm_scale
                iou = polygon_iou(contour, polygon)
                shallow_supported = self.expected_pieces is None and supported_shallow_corner(
                    physical, residual * max(mm_scale), iou)
                if not self.config.min_vertices <= len(polygon) <= self.config.max_vertices or not is_simple_polygon(polygon):
                    raise ValueError("无效多边形")
                if (polygon_edge_lengths(physical).min() < self.config.minimum_physical_edge_mm
                    or (not shallow_supported and (
                        np.abs(polygon_turn_angles_deg(physical)).min() < self.config.minimum_corner_turn_deg
                        or polygon_corner_depths(physical).min() < self.config.minimum_corner_depth_mm))
                    or iou < self.config.minimum_polygon_iou or residual * max(mm_scale) > 1.0):
                    raise ValueError("轮廓质量不足或疑似粘连")
                if np.any(polygon < margin) or np.any(polygon >= np.array([width - margin, height - margin])):
                    raise ValueError("拟合顶点越界")
                moments = cv2.moments(polygon)
                center = (moments['m10'] / moments['m00'], moments['m01'] / moments['m00'])
                candidates.append((center, polygon, contour, residual, iou))
            for index, (center, polygon, contour, residual, iou) in enumerate(sorted(candidates, key=lambda item: (item[0][1], item[0][0]))):
                corners = tuple((float(p[0]), float(p[1])) for p in polygon)
                area = float(cv2.contourArea(polygon))
                pieces.append(PieceDetection(index, corners, center, tuple(relative_point(p, width, height) for p in corners), relative_point(center, width, height), area, area / (width * height), iou, residual, iou))
                filled = np.zeros_like(mask)
                cv2.drawContours(filled, [contour], -1, 255, -1)
                self.last_diagnostics.instances.append(InstanceDiagnostics(score=iou, coarse_mask=filled, refined_mask=filled, normal_residual_px=residual, boundary_source="traditional_contour"))
        except ValueError as error:
            self.last_failure = str(error)
            pieces.clear()
            self.last_diagnostics.instances.clear()
        self.last_diagnostics.refined_count = len(pieces)
        self.last_diagnostics.stage_ms = {"preprocess": preprocessing_ms, "total": (time.perf_counter() - started) * 1000}
        return Detect1Result((width, height), tuple(pieces), 0.0, (width - 2 * margin) * (height - 2 * margin))

    def close(self):
        self.preprocessor.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
