from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import func, select

from posture_assessment.models import (
    PostureAnalysisRun,
    PostureCapture,
    PostureMeasurement,
    PostureReview,
)
from posture_assessment.posture.camera import (
    CameraProcessClient,
    CameraError,
    MockCameraAdapter,
    ReplayCameraAdapter,
)
from posture_assessment.posture.service import PostureService
from posture_assessment.posture.types import JOINT_INDEX, POSE_SEQUENCE, PoseKind
from posture_assessment.services import UserInput


def _assessment(user_service, patient_no="POSTURE01"):
    user = user_service.create(
        UserInput(
            patient_no=patient_no,
            name="体态测试用户",
            sex="男",
            birth_date=date(1990, 1, 1),
            mobile="13800138000",
            height_cm=175,
            weight_kg=70,
            address="北京市海淀区",
        )
    )
    return user, user_service.start_assessment(user.id)


def test_complete_five_pose_flow_persists_artifacts_metrics_and_review(
    db, settings, user_service
):
    _, assessment = _assessment(user_service)
    service = PostureService(
        db, settings, adapter=MockCameraAdapter(), use_process=False
    )
    info = service.device_self_check()
    assert info.is_mock is True
    assert (settings.calibration_dir / "MOCK-AZURE-KINECT-001.json").exists()

    for pose in POSE_SEQUENCE:
        capture, quality = service.capture_pose(assessment.id, pose)
        assert quality.passed is True
        root = Path(capture.artifact_dir)
        for filename in (
            "capture.mkv",
            "calibration.json",
            "joints.npz",
            "frames.npz",
            "depth_median.png",
            "body_mask.png",
            "preview.png",
            "manifest.json",
        ):
            assert (root / filename).exists()
        assert (root / "depth_median.png").read_bytes().startswith(b"\x89PNG")
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        digest = hashlib.sha256((root / "manifest.json").read_bytes()).hexdigest()
        assert digest == capture.manifest_sha256
        assert manifest["quality"]["passed"] is True

    service.record_review(
        assessment.id,
        "admin",
        [{"tool": "量尺", "affects_automatic_measurement": False}],
    )
    result = service.analyze(assessment.id)
    assert result.retake_poses == ()
    assert result.review_reasons == ()
    assert len(result.measurements) >= 39
    assert any("+" in item.source_pose for item in result.measurements)
    assert all("诊断" not in item.screening_level for item in result.measurements)
    assert service.latest_result(assessment.id)["disclaimer"].endswith("医学诊断。")

    with db.session() as session:
        assert session.scalar(select(func.count(PostureCapture.id))) == 5
        assert session.scalar(select(func.count(PostureAnalysisRun.id))) == 1
        assert session.scalar(select(func.count(PostureMeasurement.id))) == len(
            result.measurements
        )
        assert session.scalar(select(func.count(PostureReview.id))) == 1
    service.close()


def test_retake_archives_previous_raw_window_and_updates_database_path(
    db, settings, user_service
):
    _, assessment = _assessment(user_service, "RETAKE01")
    service = PostureService(
        db, settings, adapter=MockCameraAdapter(), use_process=False
    )
    first, _ = service.capture_pose(assessment.id, PoseKind.FRONT)
    first_path = Path(first.artifact_dir)
    second, _ = service.capture_pose(assessment.id, PoseKind.FRONT)
    assert second.attempt_no == 2
    assert Path(second.artifact_dir) == first_path
    with db.session() as session:
        old = session.get(PostureCapture, first.id)
        assert old.status == "已替代"
        assert "_retake_history" in old.artifact_dir
        assert Path(old.artifact_dir).exists()


def test_saved_capture_is_replayable(db, settings, user_service):
    _, assessment = _assessment(user_service, "REPLAY01")
    service = PostureService(
        db, settings, adapter=MockCameraAdapter(), use_process=False
    )
    capture, _ = service.capture_pose(assessment.id, PoseKind.BACK)
    replay = ReplayCameraAdapter(Path(capture.artifact_dir).parent)
    replay.open()
    frames = replay.capture_stable_window(PoseKind.BACK)
    replay.close()
    assert len(frames) >= 8
    assert frames[0].joints_mm.shape == (32, 3)


def test_invalid_pose_is_kept_but_requires_retake(db, settings, user_service):
    _, assessment = _assessment(user_service, "INVALID01")
    service = PostureService(
        db,
        settings,
        adapter=MockCameraAdapter("multiple_bodies"),
        use_process=False,
    )
    capture, quality = service.capture_pose(assessment.id, PoseKind.FRONT)
    assert quality.passed is False
    assert capture.status == "需重拍"
    result = service.analyze(assessment.id)
    assert PoseKind.FRONT in result.retake_poses
    assert len(result.retake_poses) == 5


def test_camera_and_body_tracking_contract_runs_in_separate_process():
    client = CameraProcessClient("mock")
    try:
        assert client.open().is_mock is True
        frame = client.preview_frame(PoseKind.FRONT)
        assert frame.depth_mm.shape == frame.body_mask.shape
        frames = client.capture_stable_window(PoseKind.ADAMS, 0.6)
        assert len(frames) >= 8
    finally:
        client.close()


def test_service_process_persists_raw_window_before_returning_to_ui(
    db, settings, user_service
):
    _, assessment = _assessment(user_service, "PROCESS01")
    service = PostureService(db, settings, use_process=True)
    try:
        capture, quality = service.capture_pose(assessment.id, PoseKind.FRONT)
        assert quality.passed
        root = Path(capture.artifact_dir)
        assert (root / "manifest.json").exists()
        assert (root / "analysis_window.npz").exists()
        representative = service.representative_frame(assessment.id, PoseKind.FRONT)
        assert representative is not None
        assert representative.depth_mm.shape[1] <= 320
        client = service._camera
        assert isinstance(client, CameraProcessClient)
        client._process.terminate()
        client._process.join(timeout=2)
        with pytest.raises(CameraError, match="异常退出"):
            service.preview(PoseKind.FRONT)
        assert service.restart_camera().is_mock
    finally:
        service.close()


def test_explicit_session_cleanup_keeps_structured_capture_audit(
    db, settings, user_service
):
    _, assessment = _assessment(user_service, "CLEANUP01")
    service = PostureService(
        db, settings, adapter=MockCameraAdapter(), use_process=False
    )
    capture, _ = service.capture_pose(assessment.id, PoseKind.FRONT)
    assert Path(capture.artifact_dir).exists()
    freed = service.cleanup_session_raw_data(assessment.session_no)
    assert freed > 0
    assert not (settings.assessment_dir / assessment.session_no).exists()
    with db.session() as session:
        row = session.get(PostureCapture, capture.id)
        assert row.status == "原始数据已清理"
        assert row.artifact_dir == ""


def test_cross_pose_inconsistency_is_not_averaged_and_requires_review(
    db, settings, user_service
):
    class InconsistentCamera(MockCameraAdapter):
        def _frame(self, pose, index=0):
            frame = super()._frame(pose, index)
            if pose is PoseKind.BACK:
                frame.joints_mm[JOINT_INDEX["shoulder_right"], 1] += 45
            return frame

    _, assessment = _assessment(user_service, "REVIEW01")
    service = PostureService(
        db, settings, adapter=InconsistentCamera(), use_process=False
    )
    for pose in POSE_SEQUENCE:
        _, quality = service.capture_pose(assessment.id, pose)
        assert quality.passed
    result = service.analyze(assessment.id)
    assert any("肩线角" in reason for reason in result.review_reasons)
    assert not any(
        item.code == "shoulder_line_angle_deg" and "+" in item.source_pose
        for item in result.measurements
    )
    with db.session() as session:
        assert session.get(type(assessment), assessment.id).status == "待复核"
    service.finalize_analysis_review(assessment.id, "admin", "复核确认")
    with db.session() as session:
        assert session.get(type(assessment), assessment.id).status == "已完成"
