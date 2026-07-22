from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum, IntEnum
from typing import Any

import numpy as np


class PoseKind(str, Enum):
    FRONT = "front"
    LEFT = "left"
    BACK = "back"
    RIGHT = "right"
    ADAMS = "adams"

    @property
    def label(self) -> str:
        return {
            self.FRONT: "正面",
            self.LEFT: "左侧",
            self.BACK: "背面",
            self.RIGHT: "右侧",
            self.ADAMS: "Adams 前屈",
        }[self]


POSE_SEQUENCE = (
    PoseKind.FRONT,
    PoseKind.LEFT,
    PoseKind.BACK,
    PoseKind.RIGHT,
    PoseKind.ADAMS,
)


class JointConfidence(IntEnum):
    NONE = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3


JOINT_NAMES = (
    "pelvis",
    "spine_navel",
    "spine_chest",
    "neck",
    "clavicle_left",
    "shoulder_left",
    "elbow_left",
    "wrist_left",
    "hand_left",
    "handtip_left",
    "thumb_left",
    "clavicle_right",
    "shoulder_right",
    "elbow_right",
    "wrist_right",
    "hand_right",
    "handtip_right",
    "thumb_right",
    "hip_left",
    "knee_left",
    "ankle_left",
    "foot_left",
    "hip_right",
    "knee_right",
    "ankle_right",
    "foot_right",
    "head",
    "nose",
    "eye_left",
    "ear_left",
    "eye_right",
    "ear_right",
)
JOINT_INDEX = {name: index for index, name in enumerate(JOINT_NAMES)}


@dataclass(slots=True)
class FrameBundle:
    """A synchronized, gravity-aligned RGB-D/body frame.

    Joint coordinates are millimetres in a right/up/forward ground coordinate
    system. The adapter is responsible for converting native camera axes.
    """

    color: np.ndarray
    depth_mm: np.ndarray
    body_mask: np.ndarray
    joints_mm: np.ndarray
    joint_confidence: np.ndarray
    calibration: dict[str, Any]
    timestamp_usec: int
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.color = np.asarray(self.color, dtype=np.uint8)
        self.depth_mm = np.asarray(self.depth_mm, dtype=np.uint16)
        self.body_mask = np.asarray(self.body_mask, dtype=np.uint8)
        self.joints_mm = np.asarray(self.joints_mm, dtype=np.float64)
        self.joint_confidence = np.asarray(self.joint_confidence, dtype=np.uint8)
        if self.joints_mm.shape != (32, 3):
            raise ValueError("joints_mm 必须为 (32, 3)")
        if self.joint_confidence.shape != (32,):
            raise ValueError("joint_confidence 必须为 (32,)")


@dataclass(frozen=True, slots=True)
class QualityAssessment:
    passed: bool
    failure_codes: tuple[str, ...]
    distance_m: float
    stability_mm: float
    orientation_std_deg: float
    orientation_error_deg: float
    depth_coverage: float
    contour_coverage: float
    joint_valid_count: int
    required_joints_complete: bool
    body_count: int
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["failure_codes"] = list(self.failure_codes)
        return data


@dataclass(frozen=True, slots=True)
class PostureMeasurement:
    code: str
    name: str
    value: float
    unit: str
    direction: str | None
    screening_level: str
    confidence: float
    source_pose: str
    algorithm_version: str
    experimental: bool = True
    proxy: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    assessment_session_id: int
    pose_quality: dict[str, dict[str, Any]]
    measurements: tuple[PostureMeasurement, ...]
    retake_poses: tuple[PoseKind, ...]
    review_reasons: tuple[str, ...]
    algorithm_version: str
    threshold_version: str
    disclaimer: str = "仅用于体态辅助筛查，不作为医学诊断。"

    def to_dict(self) -> dict[str, Any]:
        return {
            "assessment_session_id": self.assessment_session_id,
            "pose_quality": self.pose_quality,
            "measurements": [item.to_dict() for item in self.measurements],
            "retake_poses": [pose.value for pose in self.retake_poses],
            "review_reasons": list(self.review_reasons),
            "algorithm_version": self.algorithm_version,
            "threshold_version": self.threshold_version,
            "disclaimer": self.disclaimer,
        }
