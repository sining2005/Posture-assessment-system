from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import tempfile
import zlib
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from posture_assessment.posture.camera import DeviceInfo
from posture_assessment.posture.types import FrameBundle, PoseKind, QualityAssessment


class ArtifactError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)


def write_png(path: Path, image: np.ndarray) -> None:
    array = np.asarray(image)
    if array.ndim == 2 and array.dtype == np.uint16:
        bit_depth, color_type = 16, 0
        payload = array.astype(">u2", copy=False).tobytes()
        stride = array.shape[1] * 2
    elif array.ndim == 2:
        array = array.astype(np.uint8)
        bit_depth, color_type = 8, 0
        payload = array.tobytes()
        stride = array.shape[1]
    elif array.ndim == 3 and array.shape[2] == 3:
        array = array.astype(np.uint8)
        bit_depth, color_type = 8, 2
        payload = array.tobytes()
        stride = array.shape[1] * 3
    else:
        raise ArtifactError(f"不支持的 PNG 数组：shape={array.shape}, dtype={array.dtype}")
    rows = b"".join(b"\x00" + payload[offset:offset + stride] for offset in range(0, len(payload), stride))
    header = struct.pack(">IIBBBBB", array.shape[1], array.shape[0], bit_depth, color_type, 0, 0, 0)
    encoded = b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", header) + _png_chunk(b"IDAT", zlib.compress(rows, 6)) + _png_chunk(b"IEND", b"")
    path.write_bytes(encoded)


class ArtifactStore:
    def __init__(self, assessment_root: Path):
        self.assessment_root = Path(assessment_root)
        self.assessment_root.mkdir(parents=True, exist_ok=True)

    def available_bytes(self) -> int:
        return shutil.disk_usage(self.assessment_root).free

    def ensure_capacity(self, minimum_bytes: int = 512 * 1024 * 1024) -> None:
        if self.available_bytes() < minimum_bytes:
            raise ArtifactError("磁盘剩余空间不足 512 MB，无法安全保存完整采集窗口")

    def save_capture(
        self,
        session_no: str,
        pose: PoseKind,
        frames: Sequence[FrameBundle],
        quality: QualityAssessment,
        device: DeviceInfo,
        algorithm_version: str,
        attempt_no: int,
        *,
        namespace: str | None = None,
    ) -> tuple[Path, str, Path | None, list[FrameBundle]]:
        if not frames:
            raise ArtifactError("没有可保存的采集帧")
        self.ensure_capacity()
        session_root = self.assessment_root / session_no
        if namespace:
            if not namespace.replace("-", "").replace("_", "").isalnum():
                raise ArtifactError("采集命名空间包含非法字符")
            session_root = session_root / namespace
        session_root.mkdir(parents=True, exist_ok=True)
        final_dir = session_root / pose.value
        temp_dir = Path(tempfile.mkdtemp(prefix=f".{pose.value}-", dir=session_root))
        try:
            depth = np.stack([frame.depth_mm for frame in frames])
            mask = np.stack([frame.body_mask for frame in frames])
            joints = np.stack([frame.joints_mm for frame in frames])
            confidence = np.stack([frame.joint_confidence for frame in frames])
            orientations = (
                np.stack([frame.joint_orientations_wxyz for frame in frames])
                if all(frame.joint_orientations_wxyz is not None for frame in frames)
                else None
            )
            timestamps = np.array([frame.timestamp_usec for frame in frames], dtype=np.int64)
            median_depth = np.median(depth, axis=0).astype(np.uint16)
            median_mask = (np.mean(mask > 0, axis=0) >= 0.5).astype(np.uint8) * 255
            representative_color = frames[len(frames) // 2].color
            if representative_color.shape[:2] == median_depth.shape:
                preview = representative_color.astype(np.uint8)
            else:
                preview = np.zeros((*median_depth.shape, 3), dtype=np.uint8)
                valid = (median_mask > 0) & (median_depth > 0)
                if np.any(valid):
                    values = median_depth[valid].astype(float)
                    low, high = np.percentile(values, (2, 98))
                    normalized = np.clip(
                        (median_depth.astype(float) - low) / max(1.0, high - low),
                        0.0,
                        1.0,
                    )
                    preview[..., 0] = np.where(valid, 25, 3).astype(np.uint8)
                    preview[..., 1] = np.where(valid, 90 + 90 * (1 - normalized), 12).astype(np.uint8)
                    preview[..., 2] = np.where(valid, 190 + 60 * (1 - normalized), 28).astype(np.uint8)
            step = max(1, int(np.ceil(median_depth.shape[1] / 320)))
            analysis_depth = median_depth[::step, ::step]
            analysis_mask = (median_mask[::step, ::step] > 0).astype(np.uint8)
            analysis_color = preview[::step, ::step]
            analysis_calibration = dict(frames[-1].calibration)
            intrinsics = dict(analysis_calibration.get("intrinsics", {}))
            for key in ("fx", "fy", "cx", "cy"):
                if key in intrinsics:
                    intrinsics[key] = float(intrinsics[key]) / step
            analysis_calibration["intrinsics"] = intrinsics
            analysis_frames = [
                FrameBundle(
                    color=analysis_color,
                    depth_mm=analysis_depth,
                    body_mask=analysis_mask,
                    joints_mm=frame.joints_mm,
                    joint_confidence=frame.joint_confidence,
                    calibration=analysis_calibration,
                    timestamp_usec=frame.timestamp_usec,
                    joint_orientations_wxyz=frame.joint_orientations_wxyz,
                    metadata=frame.metadata,
                )
                for frame in frames
            ]

            # Mock/replay frames are small enough for a single portable tensor. Real
            # Azure frames are streamed one by one to avoid a second full RGB stack.
            if device.is_mock or device.backend == "replay":
                color = np.stack([frame.color for frame in frames])
                payload = {
                    "color": color,
                    "depth_mm": depth,
                    "body_mask": mask,
                    "joints_mm": joints,
                    "joint_confidence": confidence,
                    "timestamp_usec": timestamps,
                }
                if orientations is not None:
                    payload["joint_orientations_wxyz"] = orientations
                np.savez_compressed(temp_dir / "frames.npz", **payload)
            else:
                raw_dir = temp_dir / "raw_frames"
                raw_dir.mkdir()
                for index, frame in enumerate(frames):
                    np.savez_compressed(
                        raw_dir / f"frame-{index:04d}.npz",
                        color=frame.color,
                        depth_mm=frame.depth_mm,
                        body_mask=frame.body_mask,
                        timestamp_usec=np.array(frame.timestamp_usec, dtype=np.int64),
                    )
            analysis_payload = {
                "color": analysis_color,
                "depth_mm": analysis_depth,
                "body_mask": analysis_mask,
                "joints_mm": joints,
                "joint_confidence": confidence,
                "timestamp_usec": timestamps,
            }
            joints_payload = {
                "joints_mm": joints,
                "joint_confidence": confidence,
                "timestamp_usec": timestamps,
            }
            if orientations is not None:
                analysis_payload["joint_orientations_wxyz"] = orientations
                joints_payload["joint_orientations_wxyz"] = orientations
            np.savez_compressed(temp_dir / "analysis_window.npz", **analysis_payload)
            np.savez_compressed(temp_dir / "joints.npz", **joints_payload)
            native_mkv = frames[-1].metadata.get("native_mkv_path")
            native_mkv_path = Path(native_mkv) if native_mkv else None
            if native_mkv_path is not None and native_mkv_path.exists():
                shutil.copy2(native_mkv_path, temp_dir / "capture.mkv")
                has_native_mkv = True
            else:
                (temp_dir / "capture.mkv").write_bytes(
                    b"POSTURE-PORTABLE-REPLAY\nSee frames.npz; native MKV requires the Azure SDK recorder.\n"
                )
                has_native_mkv = False
            (temp_dir / "calibration.json").write_text(
                json.dumps(frames[-1].calibration, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            (temp_dir / "analysis_calibration.json").write_text(
                json.dumps(analysis_calibration, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            write_png(temp_dir / "depth_median.png", median_depth)
            write_png(temp_dir / "body_mask.png", median_mask)
            write_png(temp_dir / "preview.png", preview)

            files = {}
            for path in sorted(temp_dir.rglob("*")):
                if path.is_file() and path.name != "manifest.json":
                    relative = path.relative_to(temp_dir).as_posix()
                    files[relative] = {"sha256": sha256_file(path), "bytes": path.stat().st_size}
            manifest: dict[str, Any] = {
                "schema_version": 1,
                "session_no": session_no,
                "namespace": namespace,
                "pose": pose.value,
                "attempt_no": attempt_no,
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "algorithm_version": algorithm_version,
                "device": {
                    "backend": device.backend,
                    "serial_number": device.serial_number,
                    "model": device.model,
                    "tracking_mode": device.tracking_mode,
                    "is_mock": device.is_mock,
                },
                "capture_format": "native-mkv+portable-replay" if has_native_mkv else "portable-replay",
                "native_mkv": has_native_mkv,
                "quality": quality.to_dict(),
                "files": files,
            }
            manifest_path = temp_dir / "manifest.json"
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            manifest_sha = sha256_file(manifest_path)

            # Verify every staged digest before the directory becomes visible.
            for name, metadata in files.items():
                if sha256_file(temp_dir / name) != metadata["sha256"]:
                    raise ArtifactError(f"暂存文件校验失败：{name}")
            archived_path: Path | None = None
            if final_dir.exists():
                history_root = session_root / "_retake_history" / pose.value
                history_root.mkdir(parents=True, exist_ok=True)
                history_dir = history_root / f"attempt-{max(1, attempt_no - 1):03d}"
                if history_dir.exists():
                    history_dir = history_root / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
                os.replace(final_dir, history_dir)
                archived_path = history_dir
            os.replace(temp_dir, final_dir)
            return final_dir, manifest_sha, archived_path, analysis_frames
        except Exception:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise

    def delete_session(self, session_no: str) -> None:
        target = (self.assessment_root / session_no).resolve()
        root = self.assessment_root.resolve()
        if target.parent != root or not target.name.startswith("AS"):
            raise ArtifactError("拒绝删除不在 assessments 下的非会话目录")
        if target.exists():
            shutil.rmtree(target)

    def session_size(self, session_no: str) -> int:
        target = self.assessment_root / session_no
        return sum(path.stat().st_size for path in target.rglob("*") if path.is_file()) if target.exists() else 0
