from __future__ import annotations

import math

import numpy as np
import pytest

from posture_assessment.posture.camera import MockCameraAdapter
from posture_assessment.posture.geometry import signed_angle_deg, to_body_coordinates
from posture_assessment.posture.quality import evaluate_quality
from posture_assessment.posture.pointcloud import (
    analyze_back_sections,
    depth_to_points,
    fit_plane_ransac,
)
from posture_assessment.posture.thresholds import ThresholdConfig
from posture_assessment.posture.types import PoseKind


def test_signed_angle_is_rotation_invariant():
    vector = np.array([2.0, 5.0])
    reference = np.array([-3.0, 4.0])
    angle = math.radians(37.0)
    rotation = np.array(
        [[math.cos(angle), -math.sin(angle)], [math.sin(angle), math.cos(angle)]]
    )
    assert signed_angle_deg(rotation @ vector, rotation @ reference) == pytest.approx(
        signed_angle_deg(vector, reference)
    )


def test_body_coordinates_are_invariant_to_camera_yaw():
    camera = MockCameraAdapter()
    joints = camera.preview_frame(PoseKind.FRONT).joints_mm
    angle = math.radians(63)
    yaw = np.array(
        [
            [math.cos(angle), 0, math.sin(angle)],
            [0, 1, 0],
            [-math.sin(angle), 0, math.cos(angle)],
        ]
    )
    assert np.allclose(
        to_body_coordinates(joints), to_body_coordinates(joints @ yaw.T), atol=1e-6
    )


def test_normal_mock_window_passes_all_hard_quality_gates():
    camera = MockCameraAdapter()
    quality = evaluate_quality(camera.capture_stable_window(PoseKind.FRONT), PoseKind.FRONT)
    assert quality.passed is True
    assert quality.failure_codes == ()
    assert 1.8 <= quality.distance_m <= 2.2
    assert quality.joint_valid_count == 32


@pytest.mark.parametrize(
    ("scenario", "pose", "failure"),
    [
        ("multiple_bodies", PoseKind.FRONT, "MULTIPLE_BODIES"),
        ("too_far", PoseKind.FRONT, "DISTANCE_OUT_OF_RANGE"),
        ("occluded", PoseKind.FRONT, "JOINTS_INCOMPLETE"),
        ("depth_holes", PoseKind.FRONT, "DEPTH_COVERAGE_LOW"),
        ("unstable", PoseKind.FRONT, "BODY_UNSTABLE"),
        ("wrong_orientation", PoseKind.FRONT, "POSE_ORIENTATION_INVALID"),
        ("bad_adams", PoseKind.ADAMS, "ADAMS_FLEXION_INVALID"),
        ("garment_artifact", PoseKind.ADAMS, "GARMENT_OR_DEPTH_ARTIFACT"),
    ],
)
def test_quality_gate_rejects_invalid_replay_scenarios(scenario, pose, failure):
    frames = MockCameraAdapter(scenario=scenario).capture_stable_window(pose)
    quality = evaluate_quality(frames, pose)
    assert quality.passed is False
    assert failure in quality.failure_codes


def test_unvalidated_thresholds_and_low_confidence_never_claim_diagnosis(tmp_path):
    thresholds = ThresholdConfig.load(tmp_path / "thresholds.json")
    assert thresholds.classify("shoulder_line_angle_deg", 4.5, 90).endswith(
        "（实验性/待验证）"
    )
    assert thresholds.classify("shoulder_line_angle_deg", 4.5, 69) == "置信度不足"
    assert thresholds.classify("head_tilt_deg", 8.0, 95) == "仅显示实测值"


def test_depth_projection_and_ransac_ground_plane():
    depth = np.full((24, 32), 2000, dtype=np.uint16)
    points = depth_to_points(
        depth,
        np.ones_like(depth),
        {"intrinsics": {"fx": 100, "fy": 100, "cx": 16, "cy": 12}},
    )
    assert points.shape == (24 * 32, 3)
    xx, zz = np.meshgrid(np.linspace(-500, 500, 30), np.linspace(1000, 2500, 30))
    ground = np.column_stack((xx.ravel(), np.full(xx.size, -1000.0), zz.ravel()))
    ground[:20, 1] += 80  # outliers must not dominate the RANSAC fit
    normal, height, residual = fit_plane_ransac(ground)
    assert abs(abs(normal[1]) - 1.0) < 0.02
    assert height == pytest.approx(1000, abs=2)
    assert residual < 1.0


def test_adams_layered_cross_sections_find_asymmetric_side():
    depth = np.zeros((120, 100), dtype=np.uint16)
    mask = np.zeros_like(depth, dtype=np.uint8)
    mask[20:100, 20:80] = 1
    depth[20:100, 20:50] = 1990
    depth[20:100, 50:80] = 2010
    profile = analyze_back_sections(
        depth,
        mask,
        {"intrinsics": {"fx": 120, "fy": 120, "cx": 50, "cy": 60}},
    )
    assert abs(profile.height_difference_mm) >= 15
    assert abs(profile.rotation_deg) > 0
    assert profile.level in {"胸段", "腰段"}
