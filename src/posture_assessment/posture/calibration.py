from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

from posture_assessment.posture.camera import DeviceInfo
from posture_assessment.posture.pointcloud import depth_to_points, fit_plane_ransac
from posture_assessment.posture.types import FrameBundle


@dataclass(frozen=True, slots=True)
class CalibrationProfile:
    device_serial: str
    site_id: str
    version: str
    gravity_vector: tuple[float, float, float]
    ground_normal: tuple[float, float, float]
    camera_height_mm: float
    plane_residual_mm: float
    created_at: str


class CalibrationManager:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _safe_serial(serial: str) -> str:
        return "".join(char if char.isalnum() or char in "-_" else "_" for char in serial)

    def path_for(self, serial: str) -> Path:
        return self.root / f"{self._safe_serial(serial)}.json"

    def load(self, serial: str) -> CalibrationProfile | None:
        path = self.path_for(serial)
        if not path.exists():
            return None
        return CalibrationProfile(**json.loads(path.read_text(encoding="utf-8")))

    def calibrate(
        self, device: DeviceInfo, frames: list[FrameBundle], site_id: str = "default"
    ) -> CalibrationProfile:
        # The production adapter supplies IMU gravity and ground points in metadata.
        # For replay/mock data the stored gravity-aligned coordinate convention is used.
        gravity_samples = [frame.metadata.get("gravity_vector", (0.0, -1.0, 0.0)) for frame in frames]
        gravity = np.median(np.asarray(gravity_samples, dtype=float), axis=0)
        gravity /= max(np.linalg.norm(gravity), 1e-9)
        ground_points = []
        for frame in frames:
            points = frame.metadata.get("ground_points_mm")
            if points is not None:
                ground_points.append(np.asarray(points, dtype=float))
            elif frame.calibration.get("intrinsics"):
                height = frame.depth_mm.shape[0]
                floor_mask = np.zeros_like(frame.body_mask, dtype=np.uint8)
                floor_mask[int(height * 0.72):] = (frame.body_mask[int(height * 0.72):] == 0)
                projected = depth_to_points(frame.depth_mm, floor_mask, frame.calibration)
                if len(projected):
                    ground_points.append(projected)
        if ground_points:
            points = np.concatenate(ground_points, axis=0)
            normal, height, residual = fit_plane_ransac(points)
            if np.dot(normal, -gravity) < 0:
                normal = -normal
        else:
            normal = -gravity
            residual = 0.0 if device.is_mock else 999.0
            height = 1000.0
        profile = CalibrationProfile(
            device_serial=device.serial_number,
            site_id=site_id,
            version=f"GROUND-{datetime.now():%Y%m%d%H%M%S}",
            gravity_vector=tuple(float(value) for value in gravity),
            ground_normal=tuple(float(value) for value in normal),
            camera_height_mm=height,
            plane_residual_mm=residual,
            created_at=datetime.now().isoformat(timespec="seconds"),
        )
        self.path_for(device.serial_number).write_text(
            json.dumps(asdict(profile), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return profile
