from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import replace

import numpy as np

from posture_assessment.posture.quality import quality_confidence
from posture_assessment.posture.pointcloud import (
    analyze_back_sections,
    fit_side_surface_proxy,
)
from posture_assessment.posture.thresholds import ThresholdConfig
from posture_assessment.posture.types import (
    JOINT_INDEX,
    FrameBundle,
    PoseKind,
    PostureMeasurement,
    QualityAssessment,
)


ALGORITHM_VERSION = "POSTURE-2.0.0"


def signed_angle_deg(vector: np.ndarray, reference: np.ndarray) -> float:
    vector = np.asarray(vector, dtype=float)
    reference = np.asarray(reference, dtype=float)
    cross = vector[0] * reference[1] - vector[1] * reference[0]
    dot = float(np.dot(vector, reference))
    return math.degrees(math.atan2(cross, dot))


def _joint(joints: np.ndarray, name: str) -> np.ndarray:
    return joints[JOINT_INDEX[name]]


def to_body_coordinates(joints_mm: np.ndarray) -> np.ndarray:
    """Project ground-aligned joints into a subject-centric right/up/forward basis."""
    joints = np.asarray(joints_mm, dtype=float)
    origin = _joint(joints, "pelvis")
    shoulder_axis = _joint(joints, "shoulder_right") - _joint(
        joints, "shoulder_left"
    )
    right = np.array([shoulder_axis[0], 0.0, shoulder_axis[2]])
    right /= max(np.linalg.norm(right), 1e-9)
    up = np.array([0.0, 1.0, 0.0])
    forward = np.cross(right, up)
    forward /= max(np.linalg.norm(forward), 1e-9)
    relative = joints - origin
    return np.column_stack((relative @ right, relative @ up, relative @ forward))


def _measurement(
    code: str,
    name: str,
    value: float,
    unit: str,
    pose: PoseKind,
    quality: QualityAssessment,
    thresholds: ThresholdConfig,
    *,
    direction: str | None = None,
    proxy: bool = False,
    fit_score: float = 0.92,
) -> PostureMeasurement:
    confidence = quality_confidence(quality, fit_score)
    return PostureMeasurement(
        code=code,
        name=f"{name}（代理值）" if proxy else name,
        value=round(float(value), 2),
        unit=unit,
        direction=direction,
        screening_level=thresholds.classify(code, abs(float(value)), confidence),
        confidence=confidence,
        source_pose=pose.value,
        algorithm_version=ALGORITHM_VERSION,
        experimental=not thresholds.validated,
        proxy=proxy,
    )


def _direction(value: float, negative: str, positive: str, epsilon: float = 0.1) -> str:
    if abs(value) <= epsilon:
        return "居中"
    return positive if value > 0 else negative


def calculate_pose_measurements(
    pose: PoseKind,
    frames: Sequence[FrameBundle],
    quality: QualityAssessment,
    thresholds: ThresholdConfig,
) -> list[PostureMeasurement]:
    joints = to_body_coordinates(
        np.median(np.stack([frame.joints_mm for frame in frames]), axis=0)
    )
    output: list[PostureMeasurement] = []
    shoulder_l = _joint(joints, "shoulder_left")
    shoulder_r = _joint(joints, "shoulder_right")
    hip_l = _joint(joints, "hip_left")
    hip_r = _joint(joints, "hip_right")
    pelvis = _joint(joints, "pelvis")
    chest = _joint(joints, "spine_chest")
    head = _joint(joints, "head")

    if pose in (PoseKind.FRONT, PoseKind.BACK):
        shoulder_diff = shoulder_r[1] - shoulder_l[1]
        shoulder_angle = math.degrees(math.atan2(shoulder_diff, shoulder_r[0] - shoulder_l[0]))
        pelvic_diff = hip_r[1] - hip_l[1]
        pelvic_angle = math.degrees(math.atan2(pelvic_diff, hip_r[0] - hip_l[0]))
        head_tilt = math.degrees(math.atan2(head[0] - chest[0], head[1] - chest[1]))
        pelvic_shift = pelvis[0] - (hip_l[0] + hip_r[0]) / 2.0
        trunk_shift = chest[0] - pelvis[0]
        knee_l, knee_r = _joint(joints, "knee_left"), _joint(joints, "knee_right")
        ankle_l, ankle_r = _joint(joints, "ankle_left"), _joint(joints, "ankle_right")
        knee_gap = abs(knee_r[0] - knee_l[0])
        ankle_gap = abs(ankle_r[0] - ankle_l[0])
        left_leg = signed_angle_deg(knee_l[[0, 1]] - hip_l[[0, 1]], ankle_l[[0, 1]] - knee_l[[0, 1]])
        right_leg = signed_angle_deg(knee_r[[0, 1]] - hip_r[[0, 1]], ankle_r[[0, 1]] - knee_r[[0, 1]])
        output.extend(
            [
                _measurement("head_tilt_deg", "头部侧倾", head_tilt, "°", pose, quality, thresholds, direction=_direction(head_tilt, "左", "右")),
                _measurement("shoulder_height_diff_mm", "双肩高度差", shoulder_diff, "mm", pose, quality, thresholds, direction=_direction(shoulder_diff, "左高", "右高")),
                _measurement("shoulder_line_angle_deg", "肩线角", shoulder_angle, "°", pose, quality, thresholds, direction=_direction(shoulder_angle, "左高", "右高")),
                _measurement("pelvic_height_diff_mm", "骨盆高低差", pelvic_diff, "mm", pose, quality, thresholds, direction=_direction(pelvic_diff, "左高", "右高")),
                _measurement("pelvic_coronal_tilt_deg", "骨盆冠状倾斜", pelvic_angle, "°", pose, quality, thresholds, direction=_direction(pelvic_angle, "左高", "右高")),
                _measurement("pelvic_shift_mm", "骨盆侧移", pelvic_shift, "mm", pose, quality, thresholds, direction=_direction(pelvic_shift, "左移", "右移")),
                _measurement("trunk_shift_mm", "躯干侧移", trunk_shift, "mm", pose, quality, thresholds, direction=_direction(trunk_shift, "左移", "右移")),
                _measurement("knee_gap_mm", "膝中心距离", knee_gap, "mm", pose, quality, thresholds),
                _measurement("ankle_gap_mm", "踝中心距离", ankle_gap, "mm", pose, quality, thresholds),
                _measurement("left_leg_alignment_deg", "左下肢内外翻角", left_leg, "°", pose, quality, thresholds),
                _measurement("right_leg_alignment_deg", "右下肢内外翻角", right_leg, "°", pose, quality, thresholds),
            ]
        )
        if pose is PoseKind.BACK:
            profile = analyze_back_sections(
                frames[-1].depth_mm,
                frames[-1].body_mask,
                frames[-1].calibration,
            )
            spine_deviation = math.degrees(math.atan2(chest[0] - pelvis[0], chest[1] - pelvis[1]))
            output.extend(
                [
                    _measurement("spine_midline_deviation_deg", "脊柱中线侧偏", spine_deviation, "°", pose, quality, thresholds, proxy=True),
                    _measurement("scapular_surface_proxy_deg", "肩胛表面不对称", profile.rotation_deg, "°", pose, quality, thresholds, proxy=True, fit_score=profile.fit_score),
                ]
            )

    elif pose in (PoseKind.LEFT, PoseKind.RIGHT):
        side = "left" if pose is PoseKind.LEFT else "right"
        hip = _joint(joints, f"hip_{side}")
        knee = _joint(joints, f"knee_{side}")
        ankle = _joint(joints, f"ankle_{side}")
        neck = _joint(joints, "neck")
        forward_head = head[2] - shoulder_l[2] if pose is PoseKind.LEFT else head[2] - shoulder_r[2]
        trunk_lean = math.degrees(math.atan2(chest[2] - pelvis[2], chest[1] - pelvis[1]))
        knee_hyperextension = signed_angle_deg(hip[[2, 1]] - knee[[2, 1]], ankle[[2, 1]] - knee[[2, 1]])
        # Body Tracking does not expose a directly validated pelvic landmark plane.
        # The sagittal proxy therefore uses the pelvis-to-navel segment and remains
        # explicitly labelled as a proxy until local inclinometer validation.
        navel = _joint(joints, "spine_navel")
        pelvic_proxy = math.degrees(
            math.atan2(navel[2] - pelvis[2], navel[1] - pelvis[1])
        )
        surface_thoracic, surface_lumbar, fit = fit_side_surface_proxy(
            frames[-1].depth_mm, frames[-1].body_mask
        )
        thoracic_proxy = math.degrees(math.atan2(neck[2] - chest[2], neck[1] - chest[1])) + surface_thoracic
        lumbar_proxy = math.degrees(math.atan2(chest[2] - pelvis[2], chest[1] - pelvis[1])) + surface_lumbar
        output.extend(
            [
                _measurement("forward_head_mm", "头前伸距离", forward_head, "mm", pose, quality, thresholds),
                _measurement("trunk_lean_deg", "躯干前倾", trunk_lean, "°", pose, quality, thresholds),
                _measurement("knee_hyperextension_deg", "膝过伸", knee_hyperextension, "°", pose, quality, thresholds),
                _measurement("pelvic_sagittal_proxy_deg", "骨盆矢状倾斜", pelvic_proxy, "°", pose, quality, thresholds, proxy=True),
                _measurement("thoracic_surface_proxy_deg", "胸椎体表曲率", thoracic_proxy, "°", pose, quality, thresholds, proxy=True, fit_score=fit),
                _measurement("lumbar_surface_proxy_deg", "腰椎体表曲率", lumbar_proxy, "°", pose, quality, thresholds, proxy=True, fit_score=fit),
            ]
        )

    else:
        profile = analyze_back_sections(
            frames[-1].depth_mm,
            frames[-1].body_mask,
            frames[-1].calibration,
        )
        output.extend(
            [
                _measurement("adams_rotation_proxy_deg", "最大躯干表面旋转", profile.rotation_deg, "°", pose, quality, thresholds, direction=_direction(profile.rotation_deg, "左侧高", "右侧高"), proxy=True, fit_score=profile.fit_score),
                _measurement("adams_height_diff_mm", "左右背部高度差", profile.height_difference_mm, "mm", pose, quality, thresholds, direction=_direction(profile.height_difference_mm, "左侧高", "右侧高"), proxy=True, fit_score=profile.fit_score),
                _measurement("adams_abnormal_level", "最大差异所在节段", 1.0 if profile.level == "胸段" else 2.0, "段", pose, quality, thresholds, direction=profile.level, proxy=True, fit_score=profile.fit_score),
            ]
        )
    return output


def cross_pose_review(measurements: Sequence[PostureMeasurement]) -> tuple[list[str], set[str]]:
    by_code: dict[str, list[PostureMeasurement]] = {}
    for measurement in measurements:
        by_code.setdefault(measurement.code, []).append(measurement)
    reasons: list[str] = []
    inconsistent: set[str] = set()
    for code, values in by_code.items():
        if len(values) < 2:
            continue
        spread = max(item.value for item in values) - min(item.value for item in values)
        unit = values[0].unit
        limit = 2.0 if unit == "°" else 15.0 if unit == "mm" else float("inf")
        if spread > limit:
            inconsistent.add(code)
            reasons.append(f"{values[0].name}跨姿势差异 {spread:.1f}{unit}，超过 {limit:g}{unit}")
    return reasons, inconsistent


def fuse_consistent_measurements(
    measurements: Sequence[PostureMeasurement], inconsistent: set[str]
) -> list[PostureMeasurement]:
    """Confidence-weight paired views; inconsistent pairs are never averaged."""
    by_code: dict[str, list[PostureMeasurement]] = {}
    for measurement in measurements:
        by_code.setdefault(measurement.code, []).append(measurement)
    fused: list[PostureMeasurement] = []
    valid_pairs = ({"front", "back"}, {"left", "right"})
    for code, values in by_code.items():
        poses = {value.source_pose for value in values}
        if code in inconsistent or len(values) != 2 or poses not in valid_pairs:
            continue
        weight = sum(max(value.confidence, 1.0) for value in values)
        value = sum(item.value * max(item.confidence, 1.0) for item in values) / weight
        confidence = min(100.0, sum(item.confidence for item in values) / len(values))
        template = values[0]
        fused.append(
            replace(
                template,
                value=round(value, 2),
                confidence=round(confidence, 1),
                source_pose="+".join(sorted(poses)),
                metadata={
                    **template.metadata,
                    "fusion": "confidence_weighted",
                    "source_values": [item.to_dict() for item in values],
                },
            )
        )
    return fused
