from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from posture_assessment.posture.quality import FAILURE_MESSAGES, evaluate_quality
from posture_assessment.posture.types import FrameBundle, PoseKind, QualityAssessment


FAILURE_MESSAGES["PELVIS_SURFACE_ARTIFACT"] = "宽松衣物、褶皱或深度空洞影响髋臀区体表分析"


def evaluate_pelvis_quality(
    frames: Sequence[FrameBundle], view: PoseKind
) -> QualityAssessment:
    quality = evaluate_quality(frames, view)
    failures = list(quality.failure_codes)
    surface_artifact = any(
        bool(frame.metadata.get("garment_or_depth_artifact", False))
        for frame in frames
    )
    if view is PoseKind.BACK and surface_artifact:
        failures.append("PELVIS_SURFACE_ARTIFACT")
    if tuple(failures) == quality.failure_codes:
        return quality
    details = {
        **quality.details,
        "failure_messages": [FAILURE_MESSAGES[code] for code in failures],
    }
    return replace(
        quality,
        passed=False,
        failure_codes=tuple(failures),
        details=details,
    )
