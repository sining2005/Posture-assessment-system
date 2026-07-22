from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from posture_assessment.config import AppSettings
from posture_assessment.database import DatabaseManager
from posture_assessment.models import (
    AssessmentSession,
    PostureAnalysisRun,
    PostureCapture,
    PostureMeasurement as MeasurementRow,
    PostureReview,
)
from posture_assessment.posture.calibration import CalibrationManager
from posture_assessment.posture.camera import (
    CameraError,
    CameraProcessClient,
    DepthCameraAdapter,
    DeviceInfo,
    ReplayCameraAdapter,
    create_adapter,
)
from posture_assessment.posture.geometry import (
    ALGORITHM_VERSION,
    calculate_pose_measurements,
    cross_pose_review,
    fuse_consistent_measurements,
)
from posture_assessment.posture.quality import evaluate_quality
from posture_assessment.posture.storage import ArtifactStore
from posture_assessment.posture.thresholds import ThresholdConfig
from posture_assessment.posture.types import (
    POSE_SEQUENCE,
    AnalysisResult,
    FrameBundle,
    PoseKind,
    PostureMeasurement,
    QualityAssessment,
)


SESSION_STATUSES = {
    "待检测",
    "采集中",
    "待复核",
    "分析中",
    "需重拍",
    "已完成",
    "失败",
}


class PostureService:
    def __init__(
        self,
        db: DatabaseManager,
        settings: AppSettings,
        *,
        adapter: DepthCameraAdapter | None = None,
        use_process: bool = True,
    ):
        self.db = db
        self.settings = settings
        self.store = ArtifactStore(settings.assessment_dir)
        self.calibrations = CalibrationManager(settings.calibration_dir)
        self.thresholds = ThresholdConfig.load(settings.posture_threshold_path)
        self._adapter = adapter
        self._use_process = use_process and adapter is None
        self._camera: CameraProcessClient | DepthCameraAdapter | None = None
        self._device_info: DeviceInfo | None = None
        self._frame_cache: dict[tuple[int, PoseKind], list[FrameBundle]] = {}

    def _get_camera(self) -> CameraProcessClient | DepthCameraAdapter:
        if self._camera is None:
            if self._adapter is not None:
                self._camera = self._adapter
            elif self._use_process:
                self._camera = CameraProcessClient(
                    self.settings.camera_backend, self.settings.camera_replay_root
                )
            else:
                self._camera = create_adapter(
                    self.settings.camera_backend, self.settings.camera_replay_root
                )
        return self._camera

    def device_self_check(self) -> DeviceInfo:
        camera = self._get_camera()
        info = camera.open()
        self._device_info = info
        profile = self.calibrations.load(info.serial_number)
        if profile is None or not info.is_mock:
            if isinstance(camera, CameraProcessClient):
                profile_data = camera.calibrate(self.settings.calibration_dir)
                residual = float(profile_data["plane_residual_mm"])
            else:
                frames = [camera.preview_frame(PoseKind.FRONT) for _ in range(6)]
                profile = self.calibrations.calibrate(info, frames)
                residual = profile.plane_residual_mm
        else:
            residual = profile.plane_residual_mm
        if not info.is_mock and residual > 15.0:
            raise CameraError(
                f"地面标定残差 {residual:.1f} mm 超过 15 mm，请检查地面可见范围后重新自检"
            )
        return info

    def restart_camera(self) -> DeviceInfo:
        if self._camera is not None:
            try:
                self._camera.close()
            finally:
                self._camera = None
                self._device_info = None
        return self.device_self_check()

    def preview(self, pose: PoseKind) -> FrameBundle:
        if self._device_info is None:
            self.device_self_check()
        return self._get_camera().preview_frame(pose)

    def representative_frame(
        self, assessment_id: int, pose: PoseKind
    ) -> FrameBundle | None:
        frames = self._frame_cache.get((assessment_id, pose))
        return frames[len(frames) // 2] if frames else None

    def _set_session_status(self, assessment_id: int, status: str) -> None:
        if status not in SESSION_STATUSES:
            raise ValueError(f"未知体态会话状态：{status}")
        with self.db.session() as session:
            assessment = session.get(AssessmentSession, assessment_id)
            if assessment is None:
                raise ValueError("检测会话不存在")
            assessment.status = status
            if status == "已完成":
                assessment.completed_at = datetime.now()

    def _get_assessment(self, assessment_id: int) -> AssessmentSession:
        with self.db.session() as session:
            assessment = session.get(AssessmentSession, assessment_id)
            if assessment is None:
                raise ValueError("检测会话不存在")
            return assessment

    def capture_pose(
        self, assessment_id: int, pose: PoseKind
    ) -> tuple[PostureCapture, QualityAssessment]:
        assessment = self._get_assessment(assessment_id)
        self._set_session_status(assessment_id, "采集中")
        try:
            if self._device_info is None:
                self.device_self_check()
            with self.db.session() as session:
                previous = session.scalar(
                    select(PostureCapture)
                    .where(
                        PostureCapture.assessment_session_id == assessment_id,
                        PostureCapture.pose_kind == pose.value,
                    )
                    .order_by(PostureCapture.attempt_no.desc())
                )
                attempt = (previous.attempt_no if previous else 0) + 1
                previous_id = previous.id if previous else None
            camera = self._get_camera()
            if isinstance(camera, CameraProcessClient):
                payload = camera.capture_and_store(
                    pose,
                    2.0,
                    self.settings.assessment_dir,
                    assessment.session_no,
                    attempt,
                )
                artifact_dir = Path(payload.artifact_dir)
                manifest_sha = payload.manifest_sha256
                archived_path = Path(payload.archived_path) if payload.archived_path else None
                frames = payload.analysis_frames
                quality = payload.quality
                device = payload.device
            else:
                raw_frames = camera.capture_stable_window(pose, 2.0)
                quality = evaluate_quality(raw_frames, pose)
                device = self._device_info or camera.probe()
                artifact_dir, manifest_sha, archived_path, frames = self.store.save_capture(
                    assessment.session_no,
                    pose,
                    raw_frames,
                    quality,
                    device,
                    ALGORITHM_VERSION,
                    attempt,
                )
            with self.db.session() as session:
                if previous_id is not None and archived_path is not None:
                    previous_row = session.get(PostureCapture, previous_id)
                    if previous_row is not None:
                        previous_row.artifact_dir = str(archived_path)
                        previous_row.status = "已替代"
                row = PostureCapture(
                    assessment_session_id=assessment_id,
                    pose_kind=pose.value,
                    attempt_no=attempt,
                    status="已采集" if quality.passed else "需重拍",
                    artifact_dir=str(artifact_dir),
                    manifest_sha256=manifest_sha,
                    backend=device.backend,
                    device_serial=device.serial_number,
                    duration_ms=max(0, int((frames[-1].timestamp_usec - frames[0].timestamp_usec) / 1000)),
                    distance_m=quality.distance_m,
                    depth_coverage=quality.depth_coverage,
                    contour_coverage=quality.contour_coverage,
                    joint_valid_count=quality.joint_valid_count,
                    quality_json=json.dumps(quality.to_dict(), ensure_ascii=False),
                )
                session.add(row)
                session.flush()
                session.refresh(row)
            self._frame_cache[(assessment_id, pose)] = frames
            self._set_session_status(assessment_id, "待复核" if quality.passed else "需重拍")
            return row, quality
        except Exception:
            self._set_session_status(assessment_id, "失败")
            raise

    def latest_captures(self, assessment_id: int) -> dict[PoseKind, PostureCapture]:
        with self.db.session() as session:
            rows = list(
                session.scalars(
                    select(PostureCapture)
                    .where(PostureCapture.assessment_session_id == assessment_id)
                    .order_by(PostureCapture.pose_kind, PostureCapture.attempt_no.desc())
                )
            )
        output: dict[PoseKind, PostureCapture] = {}
        for row in rows:
            pose = PoseKind(row.pose_kind)
            output.setdefault(pose, row)
        return output

    def _frames_for(self, assessment_id: int, pose: PoseKind, capture: PostureCapture) -> list[FrameBundle]:
        cached = self._frame_cache.get((assessment_id, pose))
        if cached:
            return cached
        pose_dir = Path(capture.artifact_dir)
        replay = ReplayCameraAdapter(pose_dir.parent)
        replay.open()
        try:
            frames = replay.capture_stable_window(pose)
        finally:
            replay.close()
        self._frame_cache[(assessment_id, pose)] = frames
        return frames

    def record_review(
        self,
        assessment_id: int,
        operator_name: str,
        annotations: list[dict[str, Any]],
        *,
        capture_id: int | None = None,
        accepted: bool = True,
        notes: str = "",
    ) -> PostureReview:
        row = PostureReview(
            assessment_session_id=assessment_id,
            capture_id=capture_id,
            operator_name=operator_name,
            accepted=accepted,
            annotations_json=json.dumps(annotations, ensure_ascii=False),
            notes=notes.strip() or None,
        )
        with self.db.session() as session:
            session.add(row)
            session.flush()
            session.refresh(row)
        return row

    def finalize_analysis_review(
        self,
        assessment_id: int,
        operator_name: str,
        notes: str,
    ) -> PostureReview:
        review = self.record_review(
            assessment_id,
            operator_name,
            [],
            accepted=True,
            notes=notes or "已复核跨姿势不一致项，保留自动原始值。",
        )
        self._set_session_status(assessment_id, "已完成")
        return review

    def analyze(self, assessment_id: int) -> AnalysisResult:
        captures = self.latest_captures(assessment_id)
        missing = tuple(pose for pose in POSE_SEQUENCE if pose not in captures)
        failed = tuple(
            pose
            for pose, capture in captures.items()
            if not json.loads(capture.quality_json).get("passed", False)
        )
        retakes = tuple(dict.fromkeys((*missing, *failed)))
        if retakes:
            self._set_session_status(assessment_id, "需重拍")
            return AnalysisResult(
                assessment_session_id=assessment_id,
                pose_quality={pose.value: json.loads(capture.quality_json) for pose, capture in captures.items()},
                measurements=(),
                retake_poses=retakes,
                review_reasons=("五姿势未全部通过硬质量门",),
                algorithm_version=ALGORITHM_VERSION,
                threshold_version=self.thresholds.version,
            )

        self._set_session_status(assessment_id, "分析中")
        run = PostureAnalysisRun(
            assessment_session_id=assessment_id,
            status="分析中",
            algorithm_version=ALGORITHM_VERSION,
            threshold_version=self.thresholds.version,
        )
        with self.db.session() as session:
            session.add(run)
            session.flush()
            session.refresh(run)
        try:
            measurements: list[PostureMeasurement] = []
            pose_quality: dict[str, dict[str, Any]] = {}
            for pose in POSE_SEQUENCE:
                capture = captures[pose]
                quality_data = json.loads(capture.quality_json)
                quality = QualityAssessment(
                    passed=bool(quality_data["passed"]),
                    failure_codes=tuple(quality_data["failure_codes"]),
                    distance_m=float(quality_data["distance_m"]),
                    stability_mm=float(quality_data["stability_mm"]),
                    orientation_std_deg=float(quality_data["orientation_std_deg"]),
                    orientation_error_deg=float(quality_data["orientation_error_deg"]),
                    depth_coverage=float(quality_data["depth_coverage"]),
                    contour_coverage=float(quality_data["contour_coverage"]),
                    joint_valid_count=int(quality_data["joint_valid_count"]),
                    required_joints_complete=bool(quality_data["required_joints_complete"]),
                    body_count=int(quality_data["body_count"]),
                    details=quality_data.get("details", {}),
                )
                pose_quality[pose.value] = quality.to_dict()
                measurements.extend(
                    calculate_pose_measurements(
                        pose,
                        self._frames_for(assessment_id, pose, capture),
                        quality,
                        self.thresholds,
                    )
                )

            reasons, inconsistent = cross_pose_review(measurements)
            adjusted: list[PostureMeasurement] = []
            for item in measurements:
                if item.code in inconsistent:
                    confidence = max(0.0, item.confidence - 15.0)
                    item = replace(
                        item,
                        confidence=confidence,
                        screening_level=self.thresholds.classify(item.code, abs(item.value), confidence),
                        metadata={**item.metadata, "cross_pose_inconsistent": True},
                    )
                adjusted.append(item)
            adjusted.extend(fuse_consistent_measurements(adjusted, inconsistent))
            result = AnalysisResult(
                assessment_session_id=assessment_id,
                pose_quality=pose_quality,
                measurements=tuple(adjusted),
                retake_poses=(),
                review_reasons=tuple(reasons),
                algorithm_version=ALGORITHM_VERSION,
                threshold_version=self.thresholds.version,
            )
            with self.db.session() as session:
                db_run = session.get(PostureAnalysisRun, run.id)
                if db_run is None:
                    raise RuntimeError("分析记录丢失")
                for item in adjusted:
                    session.add(
                        MeasurementRow(
                            assessment_session_id=assessment_id,
                            analysis_run_id=run.id,
                            capture_id=captures[PoseKind(item.source_pose)].id if "+" not in item.source_pose else None,
                            metric_code=item.code,
                            metric_name=item.name,
                            value=item.value,
                            unit=item.unit,
                            direction=item.direction,
                            screening_level=item.screening_level,
                            confidence=item.confidence,
                            source_pose=item.source_pose,
                            method_version=item.algorithm_version,
                            metadata_json=json.dumps(item.metadata, ensure_ascii=False),
                        )
                    )
                db_run.status = "已完成"
                db_run.summary_json = json.dumps(result.to_dict(), ensure_ascii=False)
                db_run.completed_at = datetime.now()
            self._set_session_status(assessment_id, "待复核" if reasons else "已完成")
            return result
        except Exception as exc:
            with self.db.session() as session:
                db_run = session.get(PostureAnalysisRun, run.id)
                if db_run is not None:
                    db_run.status = "失败"
                    db_run.error_message = str(exc)
                    db_run.completed_at = datetime.now()
            self._set_session_status(assessment_id, "失败")
            raise

    def latest_result(self, assessment_id: int) -> dict[str, Any] | None:
        with self.db.session() as session:
            row = session.scalar(
                select(PostureAnalysisRun)
                .where(
                    PostureAnalysisRun.assessment_session_id == assessment_id,
                    PostureAnalysisRun.status == "已完成",
                )
                .order_by(PostureAnalysisRun.id.desc())
            )
            return json.loads(row.summary_json) if row and row.summary_json else None

    def storage_summary(self) -> tuple[int, int]:
        session_dirs = [path for path in self.settings.assessment_dir.iterdir() if path.is_dir()]
        total = sum(path.stat().st_size for path in self.settings.assessment_dir.rglob("*") if path.is_file())
        return len(session_dirs), total

    def cleanup_session_raw_data(self, session_no: str) -> int:
        with self.db.session() as session:
            assessment = session.scalar(
                select(AssessmentSession).where(AssessmentSession.session_no == session_no)
            )
            if assessment is None:
                raise ValueError("指定检测会话不存在")
            size = self.store.session_size(session_no)
            self.store.delete_session(session_no)
            rows = session.scalars(
                select(PostureCapture).where(
                    PostureCapture.assessment_session_id == assessment.id
                )
            )
            for row in rows:
                row.artifact_dir = ""
                row.status = "原始数据已清理"
        return size

    def cleanup_older_than(self, days: int) -> tuple[int, int]:
        if days < 1:
            raise ValueError("清理天数必须至少为 1")
        cutoff = datetime.now() - timedelta(days=days)
        with self.db.session() as session:
            session_numbers = list(
                session.scalars(
                    select(AssessmentSession.session_no).where(
                        AssessmentSession.started_at < cutoff,
                        AssessmentSession.status.in_(("已完成", "失败")),
                    )
                )
            )
        count = 0
        freed = 0
        for session_no in session_numbers:
            path = self.settings.assessment_dir / session_no
            if path.exists():
                freed += self.cleanup_session_raw_data(session_no)
                count += 1
        return count, freed

    def close(self) -> None:
        if self._camera is not None:
            self._camera.close()
            self._camera = None
