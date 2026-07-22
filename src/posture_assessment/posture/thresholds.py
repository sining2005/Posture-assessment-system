from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_THRESHOLDS: dict[str, Any] = {
    "version": "LITERATURE-DRAFT-1",
    "validated": False,
    "bands": {
        "shoulder_line_angle_deg": [2.0, 4.0],
        "pelvic_coronal_tilt_deg": [2.0, 4.0],
        "spine_midline_deviation_deg": [2.0, 4.0],
        "forward_head_mm": [25.0, 50.0],
        "pelvic_sagittal_proxy_deg": [15.0, 25.0],
        "scapular_surface_proxy_deg": [5.0, 10.0],
        "adams_rotation_proxy_deg": [5.0, 7.0],
    },
    "value_only": ["head_tilt_deg", "pelvic_shift_mm", "knee_gap_mm"],
}


@dataclass(frozen=True, slots=True)
class ThresholdConfig:
    version: str
    validated: bool
    bands: dict[str, tuple[float, float]]
    value_only: frozenset[str]

    @classmethod
    def load(cls, path: Path) -> "ThresholdConfig":
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
        else:
            raw = DEFAULT_THRESHOLDS
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(DEFAULT_THRESHOLDS, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        bands = {
            str(code): (float(values[0]), float(values[1]))
            for code, values in raw.get("bands", {}).items()
        }
        return cls(
            version=str(raw.get("version", "LITERATURE-DRAFT-1")),
            validated=bool(raw.get("validated", False)),
            bands=bands,
            value_only=frozenset(str(value) for value in raw.get("value_only", [])),
        )

    def classify(self, code: str, absolute_value: float, confidence: float) -> str:
        if confidence < 70.0:
            return "置信度不足"
        if code in self.value_only or code not in self.bands:
            return "仅显示实测值"
        low, high = self.bands[code]
        if absolute_value < low:
            level = "参考范围内"
        elif absolute_value < high:
            level = "轻度关注"
        else:
            level = "建议进一步评估"
        return level if self.validated else f"{level}（实验性/待验证）"
