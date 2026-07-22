from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass(frozen=True, slots=True)
class BackSurfaceProfile:
    rotation_deg: float
    height_difference_mm: float
    level: str
    fit_score: float


def depth_to_points(
    depth_mm: np.ndarray,
    mask: np.ndarray | None,
    calibration: dict[str, Any],
) -> np.ndarray:
    depth = np.asarray(depth_mm, dtype=float)
    valid = depth > 0
    if mask is not None:
        valid &= np.asarray(mask) > 0
    ys, xs = np.nonzero(valid)
    if not len(xs):
        return np.empty((0, 3), dtype=float)
    z = depth[ys, xs]
    intrinsics = calibration.get("intrinsics", {})
    fx = float(intrinsics.get("fx", max(depth.shape)))
    fy = float(intrinsics.get("fy", max(depth.shape)))
    cx = float(intrinsics.get("cx", (depth.shape[1] - 1) / 2))
    cy = float(intrinsics.get("cy", (depth.shape[0] - 1) / 2))
    x = (xs - cx) * z / max(fx, 1e-6)
    y = -(ys - cy) * z / max(fy, 1e-6)
    return np.column_stack((x, y, z))


def filter_points(points: np.ndarray, voxel_mm: float = 4.0) -> np.ndarray:
    points = np.asarray(points, dtype=float)
    if len(points) < 20:
        return points
    try:
        import open3d as o3d  # Optional hardware extra, loaded only when available.

        cloud = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(points))
        cloud = cloud.voxel_down_sample(voxel_size=voxel_mm)
        cloud, _ = cloud.remove_statistical_outlier(nb_neighbors=20, std_ratio=2.0)
        return np.asarray(cloud.points)
    except (ImportError, RuntimeError):
        # Deterministic fallback for CI/replay environments without Open3D.
        median = np.median(points, axis=0)
        distance = np.linalg.norm(points - median, axis=1)
        limit = np.percentile(distance, 99)
        kept = points[distance <= limit]
        keys = np.floor(kept / max(voxel_mm, 1e-6)).astype(np.int64)
        _, unique = np.unique(keys, axis=0, return_index=True)
        return kept[np.sort(unique)]


def fit_plane_ransac(
    points: np.ndarray, threshold_mm: float = 6.0, iterations: int = 160
) -> tuple[np.ndarray, float, float]:
    points = filter_points(points, voxel_mm=8.0)
    if len(points) < 20:
        raise ValueError("地面点不足，无法建立地面坐标系")
    try:
        import open3d as o3d

        cloud = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(points))
        plane, inliers = cloud.segment_plane(
            distance_threshold=threshold_mm, ransac_n=3, num_iterations=iterations
        )
        normal = np.asarray(plane[:3], dtype=float)
        normal /= max(np.linalg.norm(normal), 1e-9)
        selected = points[np.asarray(inliers, dtype=int)]
    except (ImportError, RuntimeError):
        rng = np.random.default_rng(20240722)
        best = np.empty(0, dtype=int)
        best_normal = np.array([0.0, 1.0, 0.0])
        for _ in range(iterations):
            sample = points[rng.choice(len(points), 3, replace=False)]
            normal = np.cross(sample[1] - sample[0], sample[2] - sample[0])
            norm = np.linalg.norm(normal)
            if norm < 1e-6:
                continue
            normal /= norm
            distance = np.abs((points - sample[0]) @ normal)
            inliers = np.flatnonzero(distance <= threshold_mm)
            if len(inliers) > len(best):
                best, best_normal = inliers, normal
        if len(best) < 20:
            raise ValueError("地面 RANSAC 内点不足")
        selected = points[best]
        center = selected.mean(axis=0)
        _, _, vh = np.linalg.svd(selected - center, full_matrices=False)
        normal = vh[-1]
        if np.dot(normal, best_normal) < 0:
            normal = -normal
    center = selected.mean(axis=0)
    residual = float(np.median(np.abs((selected - center) @ normal)))
    camera_height = float(abs(np.dot(center, normal)))
    return normal, camera_height, residual


def analyze_back_sections(
    depth_mm: np.ndarray,
    mask: np.ndarray,
    calibration: dict[str, Any],
    layers: int = 24,
) -> BackSurfaceProfile:
    depth = np.asarray(depth_mm, dtype=float)
    body = (np.asarray(mask) > 0) & (depth > 0)
    ys, xs = np.nonzero(body)
    if len(xs) < 100:
        return BackSurfaceProfile(0.0, 0.0, "未知", 0.2)
    y_top, y_bottom = np.percentile(ys, (18, 66))
    sample_rows = np.linspace(y_top, y_bottom, layers).astype(int)
    fx = float(calibration.get("intrinsics", {}).get("fx", max(depth.shape)))
    profiles: list[tuple[float, float, int, float]] = []
    for row in sample_rows:
        row_x = np.flatnonzero(body[row])
        if len(row_x) < 16:
            continue
        left_edge, right_edge = int(np.percentile(row_x, 8)), int(np.percentile(row_x, 92))
        middle = (left_edge + right_edge) // 2
        band = max(3, (right_edge - left_edge) // 8)
        left = depth[max(0, row - 1):row + 2, max(left_edge, middle - 2 * band):middle]
        right = depth[max(0, row - 1):row + 2, middle:min(depth.shape[1], middle + 2 * band)]
        left = left[left > 0]
        right = right[right > 0]
        if len(left) < 4 or len(right) < 4:
            continue
        left_height, right_height = float(np.percentile(left, 15)), float(np.percentile(right, 15))
        difference = left_height - right_height
        mean_depth = (left_height + right_height) / 2.0
        width_mm = (right_edge - left_edge) * mean_depth / max(fx, 1e-6)
        angle = math.degrees(math.atan2(difference, max(width_mm, 1.0)))
        residual = float((np.std(left) + np.std(right)) / 2.0)
        profiles.append((angle, difference, row, residual))
    if not profiles:
        return BackSurfaceProfile(0.0, 0.0, "未知", 0.25)
    selected = max(profiles, key=lambda value: abs(value[0]))
    row_fraction = (selected[2] - y_top) / max(1.0, y_bottom - y_top)
    level = "胸段" if row_fraction < 0.55 else "腰段"
    residual = float(np.median([profile[3] for profile in profiles]))
    fit_score = max(0.35, min(1.0, 1.0 - residual / 45.0))
    return BackSurfaceProfile(selected[0], selected[1], level, fit_score)


def fit_side_surface_proxy(
    depth_mm: np.ndarray, mask: np.ndarray
) -> tuple[float, float, float]:
    depth = np.asarray(depth_mm, dtype=float)
    body = (np.asarray(mask) > 0) & (depth > 0)
    ys, _ = np.nonzero(body)
    if len(ys) < 100:
        return 0.0, 0.0, 0.25
    top, bottom = np.percentile(ys, (18, 68))
    rows = np.linspace(top, bottom, 28).astype(int)
    profile_y: list[float] = []
    profile_z: list[float] = []
    for row in rows:
        values = depth[row][body[row]]
        if len(values) >= 4:
            profile_y.append(float(row))
            profile_z.append(float(np.percentile(values, 15)))
    if len(profile_y) < 8:
        return 0.0, 0.0, 0.3
    y = np.asarray(profile_y)
    z = np.asarray(profile_z)
    coefficients = np.polyfit(y, z, 3)
    fitted = np.polyval(coefficients, y)
    residual = float(np.sqrt(np.mean((z - fitted) ** 2)))
    split = len(y) // 2
    thoracic_slope = float(np.polyval(np.polyder(coefficients), y[:split]).mean())
    lumbar_slope = float(np.polyval(np.polyder(coefficients), y[split:]).mean())
    thoracic = math.degrees(math.atan(thoracic_slope))
    lumbar = math.degrees(math.atan(lumbar_slope))
    return thoracic, lumbar, max(0.35, min(1.0, 1.0 - residual / 35.0))
