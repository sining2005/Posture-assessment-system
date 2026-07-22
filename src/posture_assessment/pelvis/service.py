from __future__ import annotations

import json
import time
from collections import deque
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy import select

from posture_assessment.config import AppSettings
from posture_assessment.database import DatabaseManager
from posture_assessment.models import (
    AssessmentSession,
    PelvisAnalysisRun,
    PelvisCapture,
    PelvisMeasurement as MeasurementRow,
)
from posture_assessment.pelvis.geometry import (
    ALGORITHM_VERSION,
    calculate_view_measurements,
    fuse_cross_view_measurements,
)
from posture_assessment.pelvis.quality import evaluate_pelvis_quality
from posture_assessment.pelvis.types import (
    PELVIS_VIEW_SEQUENCE,
    PelvisAnalysisResult,
    PelvisMeasurement,
)
from posture_assessment.posture.calibration import (
    CalibrationManager,
    CalibrationProfile,
)
from posture_assessment.posture.camera import (
    CameraError,
    CameraProcessClient,
    DepthCameraAdapter,
    DeviceInfo,
    ReplayCameraAdapter,
    create_adapter,
)
from posture_assessment.posture.orientation import (
    average_quaternions,
    quaternion_angular_distance_deg,
)
from posture_assessment.posture.storage import ArtifactStore
from posture_assessment.posture.types import (
    JOINT_INDEX,
    FrameBundle,
    PoseKind,
    QualityAssessment,
)


class PelvisService:
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
        self._adapter = adapter
        self._use_process = use_process and adapter is None
        self._camera: CameraProcessClient | DepthCameraAdapter | None = None
        self._device_info: DeviceInfo | None = None
        self._calibration_profile: CalibrationProfile | None = None
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
        if profile is None:
            if isinstance(camera, CameraProcessClient):
                profile_data = camera.calibrate(self.settings.calibration_dir)
                profile = CalibrationProfile(**profile_data)
            else:
                frames = [camera.preview_frame(PoseKind.FRONT) for _ in range(6)]
                profile = self.calibrations.calibrate(info, frames)
        self._calibration_profile = profile
        if not info.is_mock and profile.plane_residual_mm > 15.0:
            raise CameraError(
                f"地面标定残差 {profile.plane_residual_mm:.1f} mm 超过 15 mm，请重新自检"
            )
        return info

    def restart_camera(self) -> DeviceInfo:
        self.close()
        return self.device_self_check()

    def preview(self, view: PoseKind) -> FrameBundle:
        if self._device_info is None:
            self.device_self_check()
        return self._get_camera().preview_frame(view)

    @staticmethod
    def _ground_rotation(profile: CalibrationProfile | None) -> np.ndarray:
        if profile is None:
            return np.eye(3)
        up = np.asarray(profile.ground_normal, dtype=float)
        up /= max(float(np.linalg.norm(up)), 1e-9)
        camera_forward = np.array([0.0, 0.0, 1.0])
        forward = camera_forward - np.dot(camera_forward, up) * up
        if np.linalg.norm(forward) <= 1e-6:
            forward = np.array([0.0, 1.0, 0.0])
            forward -= np.dot(forward, up) * up
        forward /= max(float(np.linalg.norm(forward)), 1e-9)
        right = np.cross(up, forward)
        right /= max(float(np.linalg.norm(right)), 1e-9)
        forward = np.cross(right, up)
        return np.stack([right, up, forward])

    def _calibration_update(self) -> dict[str, Any]:
        profile = self._calibration_profile
        return {
            "ground_rotation": self._ground_rotation(profile).tolist(),
            "ground_profile_version": profile.version if profile else "identity",
            "pelvis_coordinate_system": "ground_right_up_forward",
        }

    def _wait_until_stable(self, view: PoseKind) -> dict[str, Any]:
        info = self._device_info
        if info is None or info.is_mock or info.backend == "replay":
            return {
                "warmup_applied": False,
                "warmup_frames": 0,
                "warmup_position_rms_mm": 0.0,
                "warmup_orientation_spread_deg": 0.0,
            }
        history: deque[FrameBundle] = deque(maxlen=10)
        deadline = time.monotonic() + 6.0
        last_position_rms = float("inf")
        last_orientation_spread = float("inf")
        while time.monotonic() < deadline:
            history.append(self._get_camera().preview_frame(view))
            if len(history) < history.maxlen:
                continue
            tracked_indices = [
                JOINT_INDEX["pelvis"],
                JOINT_INDEX["hip_left"],
                JOINT_INDEX["hip_right"],
            ]
            positions = np.stack(
                [frame.joints_mm[tracked_indices] for frame in history]
            )
            center = np.median(positions, axis=0)
            last_position_rms = float(
                np.sqrt(np.mean(np.sum((positions - center) ** 2, axis=2)))
            )
            quaternion_groups = [
                [
                    frame.joint_orientations_wxyz[index]
                    for frame in history
                    if frame.joint_orientations_wxyz is not None
                ]
                for index in tracked_indices
            ]
            if all(quaternion_groups):
                last_orientation_spread = max(
                    quaternion_angular_distance_deg(value, average_quaternions(np.stack(group)))
                    for group in quaternion_groups
                    for value in group
                )
            else:
                last_orientation_spread = 0.0
            if last_position_rms <= 8.0 and last_orientation_spread <= 2.0:
                return {
                    "warmup_applied": True,
                    "warmup_frames": len(history),
                    "warmup_joints": ["pelvis", "hip_left", "hip_right"],
                    "warmup_position_rms_mm": round(last_position_rms, 2),
                    "warmup_orientation_spread_deg": round(last_orientation_spread, 2),
                }
        raise CameraError(
            "Body Tracking 初始化未稳定："
            f"位置 RMS {last_position_rms:.1f} mm，姿态离散 {last_orientation_spread:.1f}°"
        )

    def _assessment(self, assessment_id: int) -> AssessmentSession:
        with self.db.session() as session:
            assessment = session.get(AssessmentSession, assessment_id)
            if assessment is None:
                raise ValueError("检测会话不存在")
            return assessment

    def _set_status(self, assessment_id: int, status: str) -> None:
        with self.db.session() as session:
            assessment = session.get(AssessmentSession, assessment_id)
            if assessment is None:
                raise ValueError("检测会话不存在")
            assessment.status = status
            if status == "已完成":
                assessment.completed_at = datetime.now()

    def capture_view(
        self, assessment_id: int, view: PoseKind
    ) -> tuple[PelvisCapture, QualityAssessment]:
        if view not in PELVIS_VIEW_SEQUENCE:
            raise ValueError("骨盆检测仅支持正面、左侧、背面和右侧")
        assessment = self._assessment(assessment_id)
        self._set_status(assessment_id, "采集中")
        try:
            if self._device_info is None:
                self.device_self_check()
            warmup = self._wait_until_stable(view)
            calibration_update = self._calibration_update()
            with self.db.session() as session:
                previous = session.scalar(
                    select(PelvisCapture)
                    .where(
                        PelvisCapture.assessment_session_id == assessment_id,
                        PelvisCapture.view_kind == view.value,
                    )
                    .order_by(PelvisCapture.attempt_no.desc())
                )
                attempt = (previous.attempt_no if previous else 0) + 1
                previous_id = previous.id if previous else None
            camera = self._get_camera()
            if isinstance(camera, CameraProcessClient):
                payload = camera.capture_and_store(
                    view,
                    2.0,
                    self.settings.assessment_dir,
                    assessment.session_no,
                    attempt,
                    namespace="pelvis",
                    algorithm_version=ALGORITHM_VERSION,
                    calibration_update=calibration_update,
                    quality_mode="pelvis",
                    quality_details_update=warmup,
                )
                artifact_dir = Path(payload.artifact_dir)
                manifest_sha = payload.manifest_sha256
                archived_path = Path(payload.archived_path) if payload.archived_path else None
                frames = payload.analysis_frames
                quality = payload.quality
                device = payload.device
            else:
                raw_frames = camera.capture_stable_window(view, 2.0)
                for frame in raw_frames:
                    frame.calibration.update(calibration_update)
                quality = evaluate_pelvis_quality(raw_frames, view)
                quality = replace(quality, details={**quality.details, **warmup})
                device = self._device_info or camera.probe()
                artifact_dir, manifest_sha, archived_path, frames = self.store.save_capture(
                    assessment.session_no,
                    view,
                    raw_frames,
                    quality,
                    device,
                    ALGORITHM_VERSION,
                    attempt,
                    namespace="pelvis",
                )
            orientation_available = all(
                frame.joint_orientations_wxyz is not None for frame in frames
            )
            with self.db.session() as session:
                if previous_id is not None and archived_path is not None:
                    previous_row = session.get(PelvisCapture, previous_id)
                    if previous_row is not None:
                        previous_row.artifact_dir = str(archived_path)
                        previous_row.status = "已替代"
                row = PelvisCapture(
                    assessment_session_id=assessment_id,
                    view_kind=view.value,
                    attempt_no=attempt,
                    status="已采集" if quality.passed else "需重拍",
                    artifact_dir=str(artifact_dir),
                    manifest_sha256=manifest_sha,
                    backend=device.backend,
                    device_serial=device.serial_number,
                    tracking_mode=device.tracking_mode,
                    duration_ms=max(
                        0,
                        int((frames[-1].timestamp_usec - frames[0].timestamp_usec) / 1000),
                    ),
                    distance_m=quality.distance_m,
                    depth_coverage=quality.depth_coverage,
                    contour_coverage=quality.contour_coverage,
                    joint_valid_count=quality.joint_valid_count,
                    orientation_available=orientation_available,
                    quality_json=json.dumps(quality.to_dict(), ensure_ascii=False),
                )
                session.add(row)
                session.flush()
                session.refresh(row)
            self._frame_cache[(assessment_id, view)] = frames
            self._set_status(assessment_id, "待复核" if quality.passed else "需重拍")
            return row, quality
        except Exception:
            self._set_status(assessment_id, "失败")
            raise

    # CaptureDialog uses the posture service protocol name.  Keeping this thin
    # adapter lets both modules share the camera preview/capture dialog without
    # coupling the pelvis domain model to PostureCapture.
    def capture_pose(
        self, assessment_id: int, view: PoseKind
    ) -> tuple[PelvisCapture, QualityAssessment]:
        return self.capture_view(assessment_id, view)

    def representative_frame(
        self, assessment_id: int, view: PoseKind
    ) -> FrameBundle | None:
        frames = self._frame_cache.get((assessment_id, view))
        return frames[len(frames) // 2] if frames else None

    def latest_captures(self, assessment_id: int) -> dict[PoseKind, PelvisCapture]:
        with self.db.session() as session:
            rows = list(
                session.scalars(
                    select(PelvisCapture)
                    .where(PelvisCapture.assessment_session_id == assessment_id)
                    .order_by(PelvisCapture.view_kind, PelvisCapture.attempt_no.desc())
                )
            )
        output: dict[PoseKind, PelvisCapture] = {}
        for row in rows:
            output.setdefault(PoseKind(row.view_kind), row)
        return output

    def _frames_for(
        self, assessment_id: int, view: PoseKind, capture: PelvisCapture
    ) -> list[FrameBundle]:
        cached = self._frame_cache.get((assessment_id, view))
        if cached:
            return cached
        view_dir = Path(capture.artifact_dir)
        replay = ReplayCameraAdapter(view_dir.parent)
        replay.open()
        try:
            frames = replay.capture_stable_window(view)
        finally:
            replay.close()
        self._frame_cache[(assessment_id, view)] = frames
        return frames

    @staticmethod
    def _quality_from_json(payload: str) -> QualityAssessment:
        data = json.loads(payload)
        return QualityAssessment(
            passed=bool(data["passed"]),
            failure_codes=tuple(data["failure_codes"]),
            distance_m=float(data["distance_m"]),
            stability_mm=float(data["stability_mm"]),
            orientation_std_deg=float(data["orientation_std_deg"]),
            orientation_error_deg=float(data["orientation_error_deg"]),
            depth_coverage=float(data["depth_coverage"]),
            contour_coverage=float(data["contour_coverage"]),
            joint_valid_count=int(data["joint_valid_count"]),
            required_joints_complete=bool(data["required_joints_complete"]),
            body_count=int(data["body_count"]),
            details=data.get("details", {}),
        )

    def analyze(self, assessment_id: int) -> PelvisAnalysisResult:
        captures = self.latest_captures(assessment_id)
        missing = tuple(view for view in PELVIS_VIEW_SEQUENCE if view not in captures)
        failed = tuple(
            view
            for view, capture in captures.items()
            if not json.loads(capture.quality_json).get("passed", False)
        )
        retakes = tuple(dict.fromkeys((*missing, *failed)))
        if retakes:
            self._set_status(assessment_id, "需重拍")
            return PelvisAnalysisResult(
                assessment_session_id=assessment_id,
                view_quality={
                    view.value: json.loads(capture.quality_json)
                    for view, capture in captures.items()
                },
                measurements=(),
                retake_views=retakes,
                review_reasons=("四方向未全部通过硬质量门",),
                algorithm_version=ALGORITHM_VERSION,
            )

        self._set_status(assessment_id, "分析中")
        run = PelvisAnalysisRun(
            assessment_session_id=assessment_id,
            status="分析中",
            algorithm_version=ALGORITHM_VERSION,
        )
        with self.db.session() as session:
            session.add(run)
            session.flush()
            session.refresh(run)
        try:
            raw_measurements: list[PelvisMeasurement] = []
            view_quality: dict[str, dict[str, Any]] = {}
            orientation_missing: list[PoseKind] = []
            for view in PELVIS_VIEW_SEQUENCE:
                capture = captures[view]
                quality = self._quality_from_json(capture.quality_json)
                view_quality[view.value] = quality.to_dict()
                frames = self._frames_for(assessment_id, view, capture)
                if not capture.orientation_available:
                    orientation_missing.append(view)
                raw_measurements.extend(
                    calculate_view_measurements(view, frames, quality)
                )
            measurements, reasons = fuse_cross_view_measurements(raw_measurements)
            if orientation_missing:
                labels = "、".join(view.label for view in orientation_missing)
                reasons.append(f"{labels}缺少 SDK 关节姿态字段，相关旋转代理值未输出")
            result = PelvisAnalysisResult(
                assessment_session_id=assessment_id,
                view_quality=view_quality,
                measurements=tuple(measurements),
                retake_views=(),
                review_reasons=tuple(reasons),
                algorithm_version=ALGORITHM_VERSION,
            )
            with self.db.session() as session:
                db_run = session.get(PelvisAnalysisRun, run.id)
                if db_run is None:
                    raise RuntimeError("骨盆分析记录丢失")
                for item in measurements:
                    session.add(
                        MeasurementRow(
                            assessment_session_id=assessment_id,
                            analysis_run_id=run.id,
                            capture_id=(
                                captures[PoseKind(item.source_view)].id
                                if "+" not in item.source_view
                                else None
                            ),
                            metric_code=item.code,
                            metric_name=item.name,
                            value=item.value,
                            unit=item.unit,
                            direction=item.direction,
                            screening_level=item.screening_level,
                            confidence=item.confidence,
                            source_view=item.source_view,
                            method_version=item.algorithm_version,
                            metadata_json=json.dumps(item.metadata, ensure_ascii=False),
                        )
                    )
                db_run.status = "已完成"
                db_run.summary_json = json.dumps(result.to_dict(), ensure_ascii=False)
                db_run.completed_at = datetime.now()
            self._set_status(assessment_id, "待复核" if reasons else "已完成")
            return result
        except Exception as exc:
            with self.db.session() as session:
                db_run = session.get(PelvisAnalysisRun, run.id)
                if db_run is not None:
                    db_run.status = "失败"
                    db_run.error_message = str(exc)
                    db_run.completed_at = datetime.now()
            self._set_status(assessment_id, "失败")
            raise

    def latest_result(self, assessment_id: int) -> dict[str, Any] | None:
        with self.db.session() as session:
            row = session.scalar(
                select(PelvisAnalysisRun)
                .where(
                    PelvisAnalysisRun.assessment_session_id == assessment_id,
                    PelvisAnalysisRun.status == "已完成",
                )
                .order_by(PelvisAnalysisRun.id.desc())
            )
            return json.loads(row.summary_json) if row and row.summary_json else None

    def close(self) -> None:
        if self._camera is not None:
            self._camera.close()
            self._camera = None
        self._device_info = None
