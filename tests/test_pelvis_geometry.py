from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from posture_assessment.pelvis.geometry import (
    calculate_view_measurements,
    fuse_cross_view_measurements,
)
from posture_assessment.pelvis.quality import evaluate_pelvis_quality
from posture_assessment.posture.camera import MockCameraAdapter
from posture_assessment.posture.orientation import (
    average_quaternions,
    matrix_to_quaternion,
    normalize_quaternion,
    quaternion_angular_distance_deg,
    quaternion_to_matrix,
    rotation_about,
)
from posture_assessment.posture.types import JOINT_INDEX, PoseKind


def test_quaternion_normalization_roundtrip_and_antipodal_average():
    rotation = rotation_about("y", 23.0) @ rotation_about("x", -11.0)
    quaternion = matrix_to_quaternion(rotation)
    assert np.linalg.det(quaternion_to_matrix(quaternion)) == pytest.approx(1.0)
    assert quaternion_to_matrix(quaternion) == pytest.approx(rotation, abs=1e-8)
    assert normalize_quaternion(quaternion * 5.0) == pytest.approx(quaternion)
    average = average_quaternions(np.stack([quaternion, -quaternion]))
    assert quaternion_angular_distance_deg(average, quaternion) == pytest.approx(0.0)
    with pytest.raises(ValueError, match="不可归一化"):
        normalize_quaternion(np.zeros(4))


def test_mock_four_view_geometry_uses_model_orientation_and_proxy_names():
    camera = MockCameraAdapter()
    measurements = []
    for view in (PoseKind.FRONT, PoseKind.LEFT, PoseKind.BACK, PoseKind.RIGHT):
        frames = camera.capture_stable_window(view)
        quality = evaluate_pelvis_quality(frames, view)
        assert quality.passed
        measurements.extend(calculate_view_measurements(view, frames, quality))

    fused, reasons = fuse_cross_view_measurements(measurements)
    assert reasons == []
    pitch = next(
        item
        for item in fused
        if item.code == "model_pelvis_pitch_deg" and "+" in item.source_view
    )
    yaw = next(
        item
        for item in fused
        if item.code == "model_pelvis_yaw_deg" and "+" in item.source_view
    )
    assert pitch.value == pytest.approx(6.0, abs=0.25)
    assert yaw.value == pytest.approx(2.2, abs=0.25)
    assert "模型" in pitch.name and "代理角" in pitch.name
    assert all("真实髂嵴" not in item.name and "骨骼重建" not in item.name for item in fused)


def test_cross_view_inconsistency_is_not_averaged_and_marks_review():
    camera = MockCameraAdapter()
    all_measurements = []
    for view in (PoseKind.FRONT, PoseKind.BACK):
        frames = camera.capture_stable_window(view)
        if view is PoseKind.BACK:
            for frame in frames:
                frame.joints_mm[JOINT_INDEX["hip_right"], 1] += 40.0
        quality = evaluate_pelvis_quality(frames, view)
        all_measurements.extend(calculate_view_measurements(view, frames, quality))
    fused, reasons = fuse_cross_view_measurements(all_measurements)
    assert any("髋中心高低差" in reason for reason in reasons)
    assert not any(
        item.code == "hip_center_height_diff_mm" and "+" in item.source_view
        for item in fused
    )
    marked = [item for item in fused if item.code == "hip_center_height_diff_mm"]
    assert all(item.screening_level == "需人工复核" for item in marked)


def test_missing_orientation_omits_only_model_orientation_metrics():
    camera = MockCameraAdapter()
    frames = [
        replace(frame, joint_orientations_wxyz=None)
        for frame in camera.capture_stable_window(PoseKind.LEFT)
    ]
    quality = evaluate_pelvis_quality(frames, PoseKind.LEFT)
    measurements = calculate_view_measurements(PoseKind.LEFT, frames, quality)
    assert not any(item.code.startswith("model_pelvis_") for item in measurements)


def test_back_garment_or_depth_artifact_fails_pelvis_quality_gate():
    frames = MockCameraAdapter("garment_artifact").capture_stable_window(PoseKind.BACK)
    quality = evaluate_pelvis_quality(frames, PoseKind.BACK)
    assert not quality.passed
    assert "PELVIS_SURFACE_ARTIFACT" in quality.failure_codes
