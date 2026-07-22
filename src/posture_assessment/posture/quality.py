from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from posture_assessment.posture.types import (
    JOINT_INDEX,
    FrameBundle,
    JointConfidence,
    PoseKind,
    QualityAssessment,
)


FAILURE_MESSAGES = {
    "NO_FRAMES": "未获得有效采集帧",
    "MULTIPLE_BODIES": "只允许一人入镜",
    "DISTANCE_OUT_OF_RANGE": "受试者距离应保持在 1.8–2.2 m",
    "DEPTH_COVERAGE_LOW": "人体有效深度覆盖不足 90%",
    "CONTOUR_INCOMPLETE": "人体轮廓不完整或主体被截断",
    "JOINTS_INCOMPLETE": "达到 Medium 的关节少于 28/32",
    "REQUIRED_JOINT_MISSING": "当前指标所需关键关节置信度不足",
    "BODY_UNSTABLE": "骨盆稳定度未达到 8 mm",
    "ORIENTATION_UNSTABLE": "躯干方向波动超过 2°",
    "POSE_ORIENTATION_INVALID": "当前身体朝向与指定姿势不符",
    "ADAMS_FLEXION_INVALID": "Adams 前屈角需保持在 80–100°",
    "GARMENT_OR_DEPTH_ARTIFACT": "衣物褶皱或反光导致背部表面空洞",
}


REQUIRED_JOINTS = {
    PoseKind.FRONT: (
        "pelvis",
        "spine_chest",
        "neck",
        "head",
        "shoulder_left",
        "shoulder_right",
        "hip_left",
        "hip_right",
        "knee_left",
        "knee_right",
        "ankle_left",
        "ankle_right",
    ),
    PoseKind.BACK: (
        "pelvis",
        "spine_navel",
        "spine_chest",
        "neck",
        "shoulder_left",
        "shoulder_right",
        "hip_left",
        "hip_right",
    ),
    PoseKind.LEFT: (
        "pelvis",
        "spine_chest",
        "neck",
        "head",
        "hip_left",
        "knee_left",
        "ankle_left",
    ),
    PoseKind.RIGHT: (
        "pelvis",
        "spine_chest",
        "neck",
        "head",
        "hip_right",
        "knee_right",
        "ankle_right",
    ),
    PoseKind.ADAMS: (
        "pelvis",
        "spine_navel",
        "spine_chest",
        "neck",
        "shoulder_left",
        "shoulder_right",
        "hip_left",
        "hip_right",
    ),
}


def _mask_coverage(frame: FrameBundle) -> tuple[float, float]:
    mask = frame.body_mask > 0
    count = int(mask.sum())
    if count == 0:
        return 0.0, 0.0
    depth_coverage = float(np.count_nonzero(frame.depth_mm[mask]) / count)
    touches = (
        np.any(mask[0, :])
        or np.any(mask[-1, :])
        or np.any(mask[:, 0])
        or np.any(mask[:, -1])
    )
    contour = float(frame.metadata.get("contour_coverage", 0.84 if touches else 0.98))
    return depth_coverage, contour


def evaluate_quality(
    frames: Sequence[FrameBundle], pose: PoseKind
) -> QualityAssessment:
    if not frames:
        return QualityAssessment(
            passed=False,
            failure_codes=("NO_FRAMES",),
            distance_m=0.0,
            stability_mm=float("inf"),
            orientation_std_deg=float("inf"),
            orientation_error_deg=float("inf"),
            depth_coverage=0.0,
            contour_coverage=0.0,
            joint_valid_count=0,
            required_joints_complete=False,
            body_count=0,
        )

    joints = np.stack([frame.joints_mm for frame in frames])
    confidence = np.stack([frame.joint_confidence for frame in frames])
    pelvis = joints[:, JOINT_INDEX["pelvis"]]
    pelvis_center = np.median(pelvis, axis=0)
    stability_mm = float(np.sqrt(np.mean(np.sum((pelvis - pelvis_center) ** 2, axis=1))))
    distance_m = float(np.median(pelvis[:, 2]) / 1000.0)
    body_count = max(int(frame.metadata.get("body_count", 1)) for frame in frames)
    orientation_values: list[float] = []
    orientation_errors: list[float] = []
    flexion_values: list[float] = []
    for frame in frames:
        shoulder_left = frame.joints_mm[JOINT_INDEX["shoulder_left"]]
        shoulder_right = frame.joints_mm[JOINT_INDEX["shoulder_right"]]
        shoulder_vector = shoulder_right - shoulder_left
        cardinal_yaw = float(
            np.degrees(
                np.arctan2(abs(shoulder_vector[2]), max(abs(shoulder_vector[0]), 1e-6))
            )
        )
        orientation = float(frame.metadata.get("orientation_deg", cardinal_yaw))
        expected = 90.0 if pose in (PoseKind.LEFT, PoseKind.RIGHT) else 0.0
        orientation_values.append(orientation)
        orientation_errors.append(
            float(frame.metadata.get("orientation_error_deg", abs(orientation - expected)))
        )
        pelvis_joint = frame.joints_mm[JOINT_INDEX["pelvis"]]
        chest_joint = frame.joints_mm[JOINT_INDEX["spine_chest"]]
        trunk = chest_joint - pelvis_joint
        derived_flexion = float(
            np.degrees(np.arctan2(abs(trunk[2]), max(abs(trunk[1]), 1e-6)))
        )
        flexion_values.append(
            float(frame.metadata.get("adams_flexion_deg", derived_flexion))
        )
    orientation = np.asarray(orientation_values)
    orientation_std = float(np.std(orientation))
    orientation_error = float(np.median(orientation_errors))
    coverage = np.array([_mask_coverage(frame) for frame in frames])
    depth_coverage = float(np.median(coverage[:, 0]))
    contour_coverage = float(np.median(coverage[:, 1]))
    medium_ratio = np.mean(confidence >= JointConfidence.MEDIUM, axis=0)
    joint_valid_count = int(np.count_nonzero(medium_ratio >= 0.8))
    required_indices = [JOINT_INDEX[name] for name in REQUIRED_JOINTS[pose]]
    required_complete = bool(np.all(medium_ratio[required_indices] >= 0.8))
    flexion = float(np.median(flexion_values))
    garment_artifact = any(
        bool(frame.metadata.get("garment_or_depth_artifact", False)) for frame in frames
    )

    failures: list[str] = []
    if body_count != 1:
        failures.append("MULTIPLE_BODIES")
    if not 1.8 <= distance_m <= 2.2:
        failures.append("DISTANCE_OUT_OF_RANGE")
    if depth_coverage < 0.90:
        failures.append("DEPTH_COVERAGE_LOW")
    if contour_coverage < 0.90:
        failures.append("CONTOUR_INCOMPLETE")
    if joint_valid_count < 28:
        failures.append("JOINTS_INCOMPLETE")
    if not required_complete:
        failures.append("REQUIRED_JOINT_MISSING")
    if stability_mm > 8.0:
        failures.append("BODY_UNSTABLE")
    if orientation_std > 2.0:
        failures.append("ORIENTATION_UNSTABLE")
    if orientation_error > 10.0:
        failures.append("POSE_ORIENTATION_INVALID")
    if pose is PoseKind.ADAMS and not 80.0 <= flexion <= 100.0:
        failures.append("ADAMS_FLEXION_INVALID")
    if pose is PoseKind.ADAMS and garment_artifact:
        failures.append("GARMENT_OR_DEPTH_ARTIFACT")

    return QualityAssessment(
        passed=not failures,
        failure_codes=tuple(failures),
        distance_m=distance_m,
        stability_mm=stability_mm,
        orientation_std_deg=orientation_std,
        orientation_error_deg=orientation_error,
        depth_coverage=depth_coverage,
        contour_coverage=contour_coverage,
        joint_valid_count=joint_valid_count,
        required_joints_complete=required_complete,
        body_count=body_count,
        details={
            "adams_flexion_deg": flexion,
            "garment_protocol": "统一紧身运动服",
            "failure_messages": [FAILURE_MESSAGES[code] for code in failures],
        },
    )


def quality_confidence(quality: QualityAssessment, fit_score: float = 0.92) -> float:
    joint_score = min(1.0, quality.joint_valid_count / 32.0)
    stability_score = max(0.0, 1.0 - quality.stability_mm / 40.0)
    geometry_score = min(1.0, max(0.0, fit_score))
    score = (
        0.25 * min(1.0, quality.depth_coverage)
        + 0.25 * joint_score
        + 0.20 * stability_score
        + 0.15 * geometry_score
        + 0.15
    )
    return round(100.0 * score, 1)
