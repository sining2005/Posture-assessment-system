from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import numpy as np
from sqlalchemy import func, select

from posture_assessment.models import (
    PelvisAnalysisRun,
    PelvisCapture,
    PelvisMeasurement,
)
from posture_assessment.pelvis.service import PelvisService
from posture_assessment.pelvis.types import PELVIS_VIEW_SEQUENCE
import pytest

from posture_assessment.posture.camera import (
    CameraError,
    CameraProcessClient,
    MockCameraAdapter,
    ReplayCameraAdapter,
)
from posture_assessment.posture.types import PoseKind
from posture_assessment.services import UserInput


def _assessment(user_service, patient_no: str):
    user = user_service.create(
        UserInput(
            patient_no=patient_no,
            name="骨盆测试用户",
            sex="女",
            birth_date=date(1991, 4, 5),
            mobile="13800138000",
            height_cm=168,
            weight_kg=58,
            address="杭州市",
        )
    )
    return user, user_service.start_assessment(user.id)


def test_complete_four_view_pelvis_flow_persists_namespaced_artifacts_and_metrics(
    db, settings, user_service
):
    _, assessment = _assessment(user_service, "PELVIS01")
    service = PelvisService(db, settings, adapter=MockCameraAdapter(), use_process=False)
    info = service.device_self_check()
    assert info.is_mock

    for view in PELVIS_VIEW_SEQUENCE:
        capture, quality = service.capture_view(assessment.id, view)
        assert quality.passed
        assert capture.orientation_available
        root = Path(capture.artifact_dir)
        assert root.parent.name == "pelvis"
        assert root.name == view.value
        with np.load(root / "frames.npz", allow_pickle=False) as data:
            assert data["joint_orientations_wxyz"].shape[1:] == (32, 4)
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["namespace"] == "pelvis"
        assert manifest["algorithm_version"].startswith("PELVIS-SCREEN-")
        assert hashlib.sha256((root / "manifest.json").read_bytes()).hexdigest() == capture.manifest_sha256

    result = service.analyze(assessment.id)
    assert result.retake_views == ()
    assert result.review_reasons == ()
    assert any("+" in item.source_view for item in result.measurements)
    assert all("实验性" in item.screening_level for item in result.measurements)
    latest = service.latest_result(assessment.id)
    assert latest is not None
    assert "非诊断性" in latest["disclaimer"]

    with db.session() as session:
        assert session.scalar(select(func.count(PelvisCapture.id))) == 4
        assert session.scalar(select(func.count(PelvisAnalysisRun.id))) == 1
        assert session.scalar(select(func.count(PelvisMeasurement.id))) == len(result.measurements)
    service.close()


def test_pelvis_retake_archives_previous_capture_inside_pelvis_namespace(
    db, settings, user_service
):
    _, assessment = _assessment(user_service, "PELVIS-RETAKE")
    service = PelvisService(db, settings, adapter=MockCameraAdapter(), use_process=False)
    first, _ = service.capture_view(assessment.id, PoseKind.FRONT)
    second, _ = service.capture_view(assessment.id, PoseKind.FRONT)
    assert second.attempt_no == 2
    with db.session() as session:
        old = session.get(PelvisCapture, first.id)
        assert old.status == "已替代"
        old_path = Path(old.artifact_dir)
        assert "pelvis" in old_path.parts
        assert "_retake_history" in old_path.parts
        assert old_path.exists()


def test_pelvis_default_camera_process_persists_orientation_and_namespace(
    db, settings, user_service
):
    _, assessment = _assessment(user_service, "PELVIS-PROCESS")
    service = PelvisService(db, settings, use_process=True)
    try:
        capture, quality = service.capture_view(assessment.id, PoseKind.FRONT)
        assert quality.passed
        assert capture.orientation_available
        assert Path(capture.artifact_dir).parent.name == "pelvis"
        assert isinstance(service._camera, CameraProcessClient)
    finally:
        service.close()


def test_replay_supports_new_and_legacy_npz_without_orientation(tmp_path):
    camera = MockCameraAdapter()
    frames = camera.capture_stable_window(PoseKind.FRONT, 0.6)
    view_root = tmp_path / PoseKind.FRONT.value
    view_root.mkdir()
    arrays = {
        "color": np.stack([frame.color for frame in frames]),
        "depth_mm": np.stack([frame.depth_mm for frame in frames]),
        "body_mask": np.stack([frame.body_mask for frame in frames]),
        "joints_mm": np.stack([frame.joints_mm for frame in frames]),
        "joint_confidence": np.stack([frame.joint_confidence for frame in frames]),
        "timestamp_usec": np.asarray([frame.timestamp_usec for frame in frames]),
    }
    np.savez_compressed(view_root / "frames.npz", **arrays)
    replay = ReplayCameraAdapter(tmp_path)
    replay.open()
    legacy = replay.capture_stable_window(PoseKind.FRONT)
    replay.close()
    assert len(legacy) == len(frames)
    assert all(frame.joint_orientations_wxyz is None for frame in legacy)

    arrays["joint_orientations_wxyz"] = np.stack(
        [frame.joint_orientations_wxyz for frame in frames]
    )
    np.savez_compressed(view_root / "frames.npz", **arrays)
    replay.open()
    current = replay.capture_stable_window(PoseKind.FRONT)
    replay.close()
    assert current[0].joint_orientations_wxyz.shape == (32, 4)


def test_replay_rejects_a_tampered_analysis_window(
    db, settings, user_service
):
    _, assessment = _assessment(user_service, "PELVIS-CHECKSUM")
    service = PelvisService(db, settings, adapter=MockCameraAdapter(), use_process=False)
    capture, _ = service.capture_view(assessment.id, PoseKind.FRONT)
    root = Path(capture.artifact_dir)
    analysis_path = root / "analysis_window.npz"
    analysis_path.write_bytes(analysis_path.read_bytes() + b"tampered")
    replay = ReplayCameraAdapter(root.parent)
    replay.open()
    with pytest.raises(CameraError, match="校验失败"):
        replay.capture_stable_window(PoseKind.FRONT)
    replay.close()


def test_incomplete_four_view_state_restores_and_requires_retake(
    db, settings, user_service
):
    _, assessment = _assessment(user_service, "PELVIS-STATE")
    service = PelvisService(db, settings, adapter=MockCameraAdapter(), use_process=False)
    capture, quality = service.capture_view(assessment.id, PoseKind.FRONT)
    assert quality.passed
    restored = service.latest_captures(assessment.id)
    assert restored[PoseKind.FRONT].id == capture.id
    result = service.analyze(assessment.id)
    assert set(result.retake_views) == set(PELVIS_VIEW_SEQUENCE[1:])
