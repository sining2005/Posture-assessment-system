from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now()


class Base(DeclarativeBase):
    pass


class Operator(Base):
    __tablename__ = "operators"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    salt: Mapped[str] = mapped_column(String(64))
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    patient_no: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(80), index=True)
    sex: Mapped[str] = mapped_column(String(8))
    birth_date: Mapped[date] = mapped_column(Date)
    mobile: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    height_cm: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    weight_kg: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    address: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)

    assessments: Mapped[list["AssessmentSession"]] = relationship(
        back_populates="user", cascade="save-update, merge"
    )

    @property
    def age(self) -> int:
        today = date.today()
        return today.year - self.birth_date.year - (
            (today.month, today.day) < (self.birth_date.month, self.birth_date.day)
        )

    @property
    def profile_complete(self) -> bool:
        return bool(
            self.mobile
            and self.height_cm is not None
            and self.weight_kg is not None
            and self.address
        )


class AssessmentSession(Base):
    __tablename__ = "assessment_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_no: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    assessment_type: Mapped[str] = mapped_column(String(40), default="综合检测")
    status: Mapped[str] = mapped_column(String(20), default="待检测")
    report_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    user: Mapped[User] = relationship(back_populates="assessments")


class ImportBatch(Base):
    __tablename__ = "import_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_no: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    source_name: Mapped[str] = mapped_column(String(255))
    source_sha256: Mapped[str] = mapped_column(String(64))
    operator_name: Mapped[str] = mapped_column(String(64))
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    duplicate_count: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    log_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PostureCapture(Base):
    __tablename__ = "posture_captures"
    __table_args__ = (
        UniqueConstraint(
            "assessment_session_id", "pose_kind", "attempt_no", name="uq_capture_attempt"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    assessment_session_id: Mapped[int] = mapped_column(
        ForeignKey("assessment_sessions.id"), index=True
    )
    pose_kind: Mapped[str] = mapped_column(String(16), index=True)
    attempt_no: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="已采集")
    artifact_dir: Mapped[str] = mapped_column(String(500))
    manifest_sha256: Mapped[str] = mapped_column(String(64))
    backend: Mapped[str] = mapped_column(String(32))
    device_serial: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    distance_m: Mapped[float] = mapped_column(Float)
    depth_coverage: Mapped[float] = mapped_column(Float)
    contour_coverage: Mapped[float] = mapped_column(Float)
    joint_valid_count: Mapped[int] = mapped_column(Integer)
    quality_json: Mapped[str] = mapped_column(Text)
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PostureAnalysisRun(Base):
    __tablename__ = "posture_analysis_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    assessment_session_id: Mapped[int] = mapped_column(
        ForeignKey("assessment_sessions.id"), index=True
    )
    status: Mapped[str] = mapped_column(String(20), default="分析中")
    algorithm_version: Mapped[str] = mapped_column(String(40))
    threshold_version: Mapped[str] = mapped_column(String(40))
    summary_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class PostureMeasurement(Base):
    __tablename__ = "posture_measurements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    assessment_session_id: Mapped[int] = mapped_column(
        ForeignKey("assessment_sessions.id"), index=True
    )
    analysis_run_id: Mapped[int] = mapped_column(
        ForeignKey("posture_analysis_runs.id"), index=True
    )
    capture_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("posture_captures.id"), nullable=True, index=True
    )
    metric_code: Mapped[str] = mapped_column(String(80), index=True)
    metric_name: Mapped[str] = mapped_column(String(100))
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(16))
    direction: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    screening_level: Mapped[str] = mapped_column(String(20))
    confidence: Mapped[float] = mapped_column(Float)
    source_pose: Mapped[str] = mapped_column(String(40))
    method_version: Mapped[str] = mapped_column(String(40))
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PostureReview(Base):
    __tablename__ = "posture_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    assessment_session_id: Mapped[int] = mapped_column(
        ForeignKey("assessment_sessions.id"), index=True
    )
    capture_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("posture_captures.id"), nullable=True, index=True
    )
    operator_name: Mapped[str] = mapped_column(String(64))
    accepted: Mapped[bool] = mapped_column(default=False)
    annotations_json: Mapped[str] = mapped_column(Text, default="[]")
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PelvisCapture(Base):
    __tablename__ = "pelvis_captures"
    __table_args__ = (
        UniqueConstraint(
            "assessment_session_id",
            "view_kind",
            "attempt_no",
            name="uq_pelvis_capture_attempt",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    assessment_session_id: Mapped[int] = mapped_column(
        ForeignKey("assessment_sessions.id"), index=True
    )
    view_kind: Mapped[str] = mapped_column(String(16), index=True)
    attempt_no: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="已采集")
    artifact_dir: Mapped[str] = mapped_column(String(500))
    manifest_sha256: Mapped[str] = mapped_column(String(64))
    backend: Mapped[str] = mapped_column(String(32))
    device_serial: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    tracking_mode: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    distance_m: Mapped[float] = mapped_column(Float)
    depth_coverage: Mapped[float] = mapped_column(Float)
    contour_coverage: Mapped[float] = mapped_column(Float)
    joint_valid_count: Mapped[int] = mapped_column(Integer)
    orientation_available: Mapped[bool] = mapped_column(default=False)
    quality_json: Mapped[str] = mapped_column(Text)
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PelvisAnalysisRun(Base):
    __tablename__ = "pelvis_analysis_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    assessment_session_id: Mapped[int] = mapped_column(
        ForeignKey("assessment_sessions.id"), index=True
    )
    status: Mapped[str] = mapped_column(String(20), default="分析中")
    algorithm_version: Mapped[str] = mapped_column(String(40))
    summary_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class PelvisMeasurement(Base):
    __tablename__ = "pelvis_measurements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    assessment_session_id: Mapped[int] = mapped_column(
        ForeignKey("assessment_sessions.id"), index=True
    )
    analysis_run_id: Mapped[int] = mapped_column(
        ForeignKey("pelvis_analysis_runs.id"), index=True
    )
    capture_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("pelvis_captures.id"), nullable=True, index=True
    )
    metric_code: Mapped[str] = mapped_column(String(80), index=True)
    metric_name: Mapped[str] = mapped_column(String(100))
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(16))
    direction: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    screening_level: Mapped[str] = mapped_column(String(24))
    confidence: Mapped[float] = mapped_column(Float)
    source_view: Mapped[str] = mapped_column(String(40))
    method_version: Mapped[str] = mapped_column(String(40))
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
