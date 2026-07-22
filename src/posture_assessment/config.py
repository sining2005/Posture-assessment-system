from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class AppSettings:
    data_dir: Path
    database_path: Path
    export_dir: Path
    import_log_dir: Path
    assessment_dir: Path
    calibration_dir: Path
    posture_threshold_path: Path
    camera_backend: str
    camera_replay_root: Path | None
    dongle_state: str

    @classmethod
    def load(cls, data_dir: Path | None = None) -> "AppSettings":
        root = data_dir or Path(
            os.environ.get("POSTURE_APP_DATA_DIR", Path.cwd() / "data")
        )
        root = root.resolve()
        export_dir = root / "exports"
        import_log_dir = root / "import_logs"
        assessment_dir = root / "assessments"
        calibration_dir = root / "calibrations"
        for directory in (
            root,
            export_dir,
            import_log_dir,
            assessment_dir,
            calibration_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        return cls(
            data_dir=root,
            database_path=root / "posture_assessment.sqlite3",
            export_dir=export_dir,
            import_log_dir=import_log_dir,
            assessment_dir=assessment_dir,
            calibration_dir=calibration_dir,
            posture_threshold_path=root / "posture_thresholds.json",
            camera_backend=os.environ.get("POSTURE_CAMERA_BACKEND", "auto").lower(),
            camera_replay_root=(
                Path(os.environ["POSTURE_REPLAY_ROOT"]).resolve()
                if os.environ.get("POSTURE_REPLAY_ROOT")
                else None
            ),
            dongle_state=os.environ.get("POSTURE_DONGLE_STATE", "present").lower(),
        )
