from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from posture_assessment.posture.types import PoseKind


PELVIS_VIEW_SEQUENCE = (
    PoseKind.FRONT,
    PoseKind.LEFT,
    PoseKind.BACK,
    PoseKind.RIGHT,
)


@dataclass(frozen=True, slots=True)
class PelvisMeasurement:
    code: str
    name: str
    value: float
    unit: str
    direction: str | None
    confidence: float
    source_view: str
    algorithm_version: str
    screening_level: str = "实验性/待验证"
    experimental: bool = True
    proxy: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class PelvisAnalysisResult:
    assessment_session_id: int
    view_quality: dict[str, dict[str, Any]]
    measurements: tuple[PelvisMeasurement, ...]
    retake_views: tuple[PoseKind, ...]
    review_reasons: tuple[str, ...]
    algorithm_version: str
    disclaimer: str = (
        "仅用于非诊断性体态筛查；所有骨盆角度均为 Azure Kinect 人体模型或体表代理值，"
        "不等同于 ASIS–PSIS 临床骨盆角或医学影像测量。"
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "assessment_session_id": self.assessment_session_id,
            "view_quality": self.view_quality,
            "measurements": [item.to_dict() for item in self.measurements],
            "retake_views": [view.value for view in self.retake_views],
            "review_reasons": list(self.review_reasons),
            "algorithm_version": self.algorithm_version,
            "disclaimer": self.disclaimer,
        }
