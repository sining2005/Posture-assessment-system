from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import replace

import numpy as np

from posture_assessment.pelvis.types import PelvisMeasurement
from posture_assessment.posture.orientation import (
    average_quaternions,
    matrix_to_quaternion,
    quaternion_to_matrix,
    rotation_about,
)
from posture_assessment.posture.quality import quality_confidence
from posture_assessment.posture.types import (
    JOINT_INDEX,
    FrameBundle,
    JointConfidence,
    PoseKind,
    QualityAssessment,
)


ALGORITHM_VERSION = "PELVIS-SCREEN-1.0.0"
VIEW_YAW_DEG = {
    PoseKind.FRONT: 0.0,
    PoseKind.LEFT: 90.0,
    PoseKind.BACK: 180.0,
    PoseKind.RIGHT: -90.0,
}


def _ground_rotation(frame: FrameBundle) -> np.ndarray:
    value = frame.calibration.get("ground_rotation")
    if value is None:
        return np.eye(3)
    matrix = np.asarray(value, dtype=float)
    return matrix if matrix.shape == (3, 3) else np.eye(3)


def _ground_joints(frame: FrameBundle) -> np.ndarray:
    return np.asarray(frame.joints_mm, dtype=float) @ _ground_rotation(frame).T


def _direction(value: float, negative: str, positive: str, epsilon: float = 0.1) -> str:
    if abs(value) <= epsilon:
        return "居中"
    return positive if value > 0 else negative


def _measurement(
    code: str,
    name: str,
    value: float,
    unit: str,
    view: PoseKind,
    quality: QualityAssessment,
    *,
    direction: str | None = None,
    fit_score: float = 0.92,
    metadata: dict | None = None,
) -> PelvisMeasurement:
    confidence = quality_confidence(quality, fit_score)
    return PelvisMeasurement(
        code=code,
        name=name,
        value=round(float(value), 2),
        unit=unit,
        direction=direction,
        confidence=confidence,
        source_view=view.value,
        algorithm_version=ALGORITHM_VERSION,
        screening_level=(
            "实验性/待验证" if confidence >= 70.0 else "置信度不足"
        ),
        metadata=metadata or {},
    )


def _orientation_angles(
    frames: Sequence[FrameBundle], view: PoseKind
) -> tuple[float, float, float, int] | None:
    pelvis_index = JOINT_INDEX["pelvis"]
    quaternions: list[np.ndarray] = []
    weights: list[float] = []
    for frame in frames:
        orientations = frame.joint_orientations_wxyz
        if orientations is None:
            continue
        quaternion = np.asarray(orientations[pelvis_index], dtype=float)
        if quaternion.shape != (4,) or not np.all(np.isfinite(quaternion)):
            continue
        if frame.joint_confidence[pelvis_index] < JointConfidence.MEDIUM:
            continue
        rotation_ground = _ground_rotation(frame) @ quaternion_to_matrix(quaternion)
        canonical = rotation_about("y", -VIEW_YAW_DEG[view]) @ rotation_ground
        quaternions.append(matrix_to_quaternion(canonical))
        weights.append(float(frame.joint_confidence[pelvis_index]))
    if not quaternions:
        return None
    rotation = quaternion_to_matrix(
        average_quaternions(np.stack(quaternions), np.asarray(weights))
    )
    yaw = math.degrees(math.atan2(rotation[0, 2], rotation[2, 2]))
    pitch = math.degrees(
        math.atan2(-rotation[1, 2], math.hypot(rotation[0, 2], rotation[2, 2]))
    )
    obliquity = math.degrees(math.atan2(rotation[1, 0], rotation[1, 1]))
    return pitch, yaw, obliquity, len(quaternions)


def _surface_asymmetry_mm(frame: FrameBundle) -> tuple[float, float]:
    depth = np.asarray(frame.depth_mm, dtype=float)
    mask = (np.asarray(frame.body_mask) > 0) & (depth > 0)
    ys, xs = np.nonzero(mask)
    if len(xs) < 100:
        return float("nan"), 0.0
    x0, x1 = int(xs.min()), int(xs.max())
    y0, y1 = int(ys.min()), int(ys.max())
    top = y0 + int((y1 - y0) * 0.42)
    bottom = y0 + int((y1 - y0) * 0.74)
    center = (x0 + x1) // 2
    half = min(center - x0, x1 - center)
    if bottom <= top or half < 4:
        return float("nan"), 0.0
    left_depth = depth[top:bottom, center - half : center]
    right_depth = np.fliplr(depth[top:bottom, center + 1 : center + 1 + half])
    left_mask = mask[top:bottom, center - half : center]
    right_mask = np.fliplr(mask[top:bottom, center + 1 : center + 1 + half])
    width = min(left_depth.shape[1], right_depth.shape[1])
    paired = left_mask[:, :width] & right_mask[:, :width]
    if int(paired.sum()) < 50:
        return float("nan"), 0.0
    differences = np.abs(left_depth[:, :width] - right_depth[:, :width])[paired]
    return float(np.median(differences)), min(1.0, float(paired.sum()) / paired.size)


def calculate_view_measurements(
    view: PoseKind,
    frames: Sequence[FrameBundle],
    quality: QualityAssessment,
) -> list[PelvisMeasurement]:
    if view not in VIEW_YAW_DEG or not frames:
        return []
    joints = np.median(np.stack([_ground_joints(frame) for frame in frames]), axis=0)
    hip_left = joints[JOINT_INDEX["hip_left"]]
    hip_right = joints[JOINT_INDEX["hip_right"]]
    pelvis = joints[JOINT_INDEX["pelvis"]]
    ankle_left = joints[JOINT_INDEX["ankle_left"]]
    ankle_right = joints[JOINT_INDEX["ankle_right"]]
    output: list[PelvisMeasurement] = []

    if view in (PoseKind.FRONT, PoseKind.BACK):
        hip_vector = hip_right - hip_left
        horizontal_span = float(np.linalg.norm(hip_vector[[0, 2]]))
        height_difference = float(hip_right[1] - hip_left[1])
        obliquity = math.degrees(
            math.atan2(height_difference, max(horizontal_span, 1e-6))
        )
        lateral_axis = hip_vector.copy()
        lateral_axis[1] = 0.0
        lateral_axis /= max(float(np.linalg.norm(lateral_axis)), 1e-9)
        support_center = (ankle_left + ankle_right) / 2.0
        lateral_shift = float(np.dot(pelvis - support_center, lateral_axis))
        output.extend(
            [
                _measurement(
                    "hip_center_height_diff_mm",
                    "左右髋中心高低差",
                    height_difference,
                    "mm",
                    view,
                    quality,
                    direction=_direction(height_difference, "左侧高", "右侧高"),
                ),
                _measurement(
                    "hip_axis_obliquity_deg",
                    "髋轴冠状倾斜代理角",
                    obliquity,
                    "°",
                    view,
                    quality,
                    direction=_direction(obliquity, "左侧高", "右侧高"),
                ),
                _measurement(
                    "pelvis_lateral_shift_mm",
                    "骨盆中心相对支撑基底侧移",
                    lateral_shift,
                    "mm",
                    view,
                    quality,
                    direction=_direction(lateral_shift, "左移", "右移"),
                ),
            ]
        )

    orientation = _orientation_angles(frames, view)
    if orientation is not None:
        pitch, yaw, model_obliquity, sample_count = orientation
        metadata = {"orientation_samples": sample_count, "sdk_model_orientation": True}
        if view in (PoseKind.LEFT, PoseKind.RIGHT):
            output.append(
                _measurement(
                    "model_pelvis_pitch_deg",
                    "模型骨盆前后倾代理角",
                    pitch,
                    "°",
                    view,
                    quality,
                    direction=_direction(pitch, "后倾", "前倾"),
                    metadata=metadata,
                )
            )
        if view in (PoseKind.FRONT, PoseKind.BACK):
            output.append(
                _measurement(
                    "model_pelvis_yaw_deg",
                    "模型骨盆水平旋转代理角",
                    yaw,
                    "°",
                    view,
                    quality,
                    direction=_direction(yaw, "向左", "向右"),
                    metadata={**metadata, "model_obliquity_deg": round(model_obliquity, 2)},
                )
            )

    if view is PoseKind.BACK:
        asymmetry, fit = _surface_asymmetry_mm(frames[len(frames) // 2])
        if np.isfinite(asymmetry):
            output.append(
                _measurement(
                    "hip_surface_asymmetry_mm",
                    "髋臀区体表对称差代理值",
                    asymmetry,
                    "mm",
                    view,
                    quality,
                    direction=None,
                    fit_score=fit,
                    metadata={"paired_surface_coverage": round(fit, 3)},
                )
            )
    return output


def _circular_difference(first: float, second: float) -> float:
    return abs((first - second + 180.0) % 360.0 - 180.0)


def _weighted_value(values: Sequence[PelvisMeasurement]) -> float:
    weights = np.asarray([max(1.0, value.confidence) for value in values], dtype=float)
    samples = np.asarray([value.value for value in values], dtype=float)
    if values[0].unit == "°":
        radians = np.radians(samples)
        return math.degrees(
            math.atan2(float(np.sum(weights * np.sin(radians))), float(np.sum(weights * np.cos(radians))))
        )
    return float(np.average(samples, weights=weights))


PAIR_LIMITS = {
    "hip_center_height_diff_mm": 15.0,
    "hip_axis_obliquity_deg": 2.0,
    "pelvis_lateral_shift_mm": 15.0,
    "model_pelvis_pitch_deg": 3.0,
    "model_pelvis_yaw_deg": 3.0,
}


def fuse_cross_view_measurements(
    measurements: Sequence[PelvisMeasurement],
) -> tuple[list[PelvisMeasurement], list[str]]:
    grouped: dict[str, list[PelvisMeasurement]] = {}
    for measurement in measurements:
        grouped.setdefault(measurement.code, []).append(measurement)
    output = list(measurements)
    reasons: list[str] = []
    for code, limit in PAIR_LIMITS.items():
        values = grouped.get(code, [])
        if len(values) != 2:
            continue
        difference = (
            _circular_difference(values[0].value, values[1].value)
            if values[0].unit == "°"
            else abs(values[0].value - values[1].value)
        )
        if difference > limit:
            reasons.append(
                f"{values[0].name}跨视图差异 {difference:.1f}{values[0].unit}，超过 {limit:g}{values[0].unit}"
            )
            for item in values:
                output[output.index(item)] = replace(
                    item,
                    confidence=max(0.0, item.confidence - 15.0),
                    screening_level="需人工复核",
                    metadata={**item.metadata, "cross_view_inconsistent": True},
                )
            continue
        fused_value = _weighted_value(values)
        confidence = round(sum(value.confidence for value in values) / 2.0, 1)
        output.append(
            replace(
                values[0],
                value=round(fused_value, 2),
                confidence=confidence,
                source_view="+".join(sorted(value.source_view for value in values)),
                direction=(
                    _direction(fused_value, "左侧高", "右侧高")
                    if code in {"hip_center_height_diff_mm", "hip_axis_obliquity_deg"}
                    else _direction(fused_value, "左移", "右移")
                    if code == "pelvis_lateral_shift_mm"
                    else _direction(fused_value, "后倾", "前倾")
                    if code == "model_pelvis_pitch_deg"
                    else _direction(fused_value, "向左", "向右")
                ),
                metadata={
                    **values[0].metadata,
                    "fusion": "confidence_weighted_circular" if values[0].unit == "°" else "confidence_weighted",
                    "source_values": [value.to_dict() for value in values],
                },
            )
        )
    return output, reasons
