from __future__ import annotations

import importlib
import hashlib
import json
import multiprocessing as mp
import os
import queue
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np

from posture_assessment.posture.orientation import (
    matrix_to_quaternion,
    reflect_camera_y_to_up,
    rotation_about,
)
from posture_assessment.posture.types import (
    JOINT_INDEX,
    FrameBundle,
    JointConfidence,
    PoseKind,
    QualityAssessment,
)


class CameraError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    available: bool
    backend: str
    serial_number: str
    model: str
    tracking_mode: str
    message: str
    is_mock: bool = False
    requires_calibration: bool = False


@dataclass(frozen=True, slots=True)
class StoredCapturePayload:
    artifact_dir: str
    manifest_sha256: str
    archived_path: str | None
    analysis_frames: list[FrameBundle]
    quality: QualityAssessment
    device: DeviceInfo


class DepthCameraAdapter(ABC):
    @abstractmethod
    def probe(self) -> DeviceInfo: ...

    @abstractmethod
    def open(self) -> DeviceInfo: ...

    @abstractmethod
    def preview_frame(self, pose: PoseKind) -> FrameBundle: ...

    @abstractmethod
    def capture_stable_window(
        self, pose: PoseKind, duration_seconds: float = 2.0
    ) -> list[FrameBundle]: ...

    @abstractmethod
    def close(self) -> None: ...


def _base_joints(pose: PoseKind) -> np.ndarray:
    points = np.zeros((32, 3), dtype=np.float64)

    def set_joint(name: str, x: float, y: float, z: float = 2000.0) -> None:
        points[JOINT_INDEX[name]] = (x, y, z)

    set_joint("pelvis", 0, 980)
    set_joint("spine_navel", 0, 1160)
    set_joint("spine_chest", 4, 1390)
    set_joint("neck", 3, 1530)
    set_joint("head", 8, 1710)
    set_joint("nose", 8, 1690, 1940)
    for side, sign in (("left", -1), ("right", 1)):
        set_joint(f"clavicle_{side}", 115 * sign, 1490)
        set_joint(f"shoulder_{side}", 220 * sign, 1450 + (5 if side == "right" else -5))
        set_joint(f"elbow_{side}", 330 * sign, 1200)
        set_joint(f"wrist_{side}", 365 * sign, 980)
        set_joint(f"hand_{side}", 370 * sign, 920)
        set_joint(f"handtip_{side}", 375 * sign, 870)
        set_joint(f"thumb_{side}", 350 * sign, 920)
        set_joint(f"hip_{side}", 145 * sign, 970 + (2 if side == "right" else -2))
        set_joint(f"knee_{side}", 140 * sign, 520)
        set_joint(f"ankle_{side}", 150 * sign, 90)
        set_joint(f"foot_{side}", 155 * sign, 35, 1920)
    set_joint("eye_left", -30, 1740, 1950)
    set_joint("ear_left", -75, 1725, 1995)
    set_joint("eye_right", 45, 1740, 1950)
    set_joint("ear_right", 85, 1725, 1995)

    if pose in (PoseKind.LEFT, PoseKind.RIGHT):
        # Side acquisitions keep real 3-D coordinates but expose a mild forward head.
        points[JOINT_INDEX["head"], 2] = 2050
        points[JOINT_INDEX["neck"], 2] = 2005
        points[JOINT_INDEX["spine_chest"], 2] = 1995
        points[JOINT_INDEX["pelvis"], 2] = 1980
    if pose is PoseKind.ADAMS:
        for name in ("spine_navel", "spine_chest", "neck", "head"):
            index = JOINT_INDEX[name]
            vertical = points[index, 1]
            points[index, 2] = 2000 - (vertical - 980) * 0.95
            points[index, 1] = 980 + (vertical - 980) * 0.08
    return points


def _synthetic_images(pose: PoseKind) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    height, width = 180, 120
    yy, xx = np.mgrid[:height, :width]
    center = width // 2
    side_scale = 0.58 if pose in (PoseKind.LEFT, PoseKind.RIGHT) else 1.0
    torso = (((xx - center) / (26 * side_scale)) ** 2 + ((yy - 78) / 54) ** 2) <= 1
    head = ((xx - center) ** 2 + (yy - 25) ** 2) <= 13**2
    legs = (
        (((xx - (center - 12 * side_scale)) / (9 * side_scale)) ** 2 + ((yy - 142) / 43) ** 2 <= 1)
        | (((xx - (center + 12 * side_scale)) / (9 * side_scale)) ** 2 + ((yy - 142) / 43) ** 2 <= 1)
    )
    arms = (((xx - center) / (39 * side_scale)) ** 2 + ((yy - 85) / 45) ** 2) <= 1
    mask = (torso | head | legs | arms).astype(np.uint8)
    base = 2000 + (xx - center) * 0.22
    if pose is PoseKind.ADAMS:
        base = base + np.where(xx >= center, 8.0, -4.0)
    depth = np.where(mask, base, 0).astype(np.uint16)
    color = np.zeros((height, width, 3), dtype=np.uint8)
    valid = mask > 0
    color[valid, 0] = np.clip(35 + yy[valid] // 3, 0, 255)
    color[valid, 1] = np.clip(100 + (180 - yy[valid]) // 4, 0, 255)
    color[valid, 2] = 210
    return color, depth, mask


def _mock_joint_orientations(pose: PoseKind, index: int) -> np.ndarray:
    view_yaw = {
        PoseKind.FRONT: 0.0,
        PoseKind.LEFT: 90.0,
        PoseKind.BACK: 180.0,
        PoseKind.RIGHT: -90.0,
        PoseKind.ADAMS: 0.0,
    }[pose]
    pitch = 6.0 + 0.08 * np.sin(index / 2)
    yaw = 2.2 + 0.08 * np.cos(index / 3)
    obliquity = 0.8 + 0.05 * np.sin(index / 4)
    rotation = (
        rotation_about("y", view_yaw)
        @ rotation_about("y", yaw)
        @ rotation_about("x", pitch)
        @ rotation_about("z", obliquity)
    )
    quaternion = matrix_to_quaternion(rotation)
    return np.repeat(quaternion[np.newaxis, :], 32, axis=0)


class MockCameraAdapter(DepthCameraAdapter):
    def __init__(
        self, scenario: str = "normal", seed: int = 7, fallback_message: str = ""
    ):
        self.scenario = scenario
        self.random = np.random.default_rng(seed)
        self.fallback_message = fallback_message
        self._open = False

    def probe(self) -> DeviceInfo:
        return DeviceInfo(
            available=True,
            backend="mock",
            serial_number="MOCK-AZURE-KINECT-001",
            model="Azure Kinect DK 模拟器",
            tracking_mode="CPU（模拟）",
            message=(
                "未连接真机，当前使用可重复的模拟 RGB-D 数据"
                + (f"；降级原因：{self.fallback_message}" if self.fallback_message else "")
            ),
            is_mock=True,
            requires_calibration=False,
        )

    def open(self) -> DeviceInfo:
        self._open = True
        return self.probe()

    def _frame(self, pose: PoseKind, index: int = 0) -> FrameBundle:
        color, depth, mask = _synthetic_images(pose)
        joints = _base_joints(pose)
        jitter = self.random.normal(0, 1.1, size=joints.shape)
        joints = joints + jitter
        confidence = np.full(32, JointConfidence.HIGH, dtype=np.uint8)
        orientations = _mock_joint_orientations(pose, index)
        metadata: dict[str, Any] = {
            "body_count": 1,
            "orientation_deg": float(self.random.normal(0, 0.4)),
            "orientation_error_deg": 1.2,
            "contour_coverage": 0.98,
            "adams_flexion_deg": 90.0,
            "scenario": self.scenario,
        }
        if self.scenario == "multiple_bodies":
            metadata["body_count"] = 2
        elif self.scenario == "too_far":
            joints[:, 2] += 450
        elif self.scenario == "occluded":
            confidence[18:26] = JointConfidence.LOW
        elif self.scenario == "depth_holes":
            depth[mask > 0] = np.where((np.indices(depth.shape)[0][mask > 0] % 3) == 0, 0, depth[mask > 0])
        elif self.scenario == "unstable":
            joints[:, 0] += 18 * np.sin(index)
        elif self.scenario == "wrong_orientation":
            metadata["orientation_error_deg"] = 23.0
        elif self.scenario == "bad_adams":
            metadata["adams_flexion_deg"] = 68.0
        elif self.scenario == "garment_artifact":
            metadata["garment_or_depth_artifact"] = True
        return FrameBundle(
            color=color,
            depth_mm=depth,
            body_mask=mask,
            joints_mm=joints,
            joint_confidence=confidence,
            calibration={
                "schema": 1,
                "coordinate_system": "ground_mm_right_up_forward",
                "intrinsics": {"fx": 100.0, "fy": 100.0, "cx": 60.0, "cy": 90.0},
                "device_serial": "MOCK-AZURE-KINECT-001",
                "calibration_version": "MOCK-GROUND-1",
            },
            timestamp_usec=int(time.time_ns() // 1000) + index * 66_667,
            joint_orientations_wxyz=orientations,
            metadata=metadata,
        )

    def preview_frame(self, pose: PoseKind) -> FrameBundle:
        if not self._open:
            self.open()
        return self._frame(pose)

    def capture_stable_window(
        self, pose: PoseKind, duration_seconds: float = 2.0
    ) -> list[FrameBundle]:
        if not self._open:
            self.open()
        sample_count = max(8, int(duration_seconds * 15))
        return [self._frame(pose, index) for index in range(sample_count)]

    def close(self) -> None:
        self._open = False


class ReplayCameraAdapter(DepthCameraAdapter):
    def __init__(self, replay_root: Path):
        self.replay_root = Path(replay_root)
        self._open = False

    def probe(self) -> DeviceInfo:
        available = self.replay_root.exists()
        return DeviceInfo(
            available=available,
            backend="replay",
            serial_number="REPLAY",
            model="录制回放",
            tracking_mode="离线",
            message="回放数据可用" if available else f"回放目录不存在：{self.replay_root}",
            is_mock=False,
        )

    def open(self) -> DeviceInfo:
        info = self.probe()
        if not info.available:
            raise CameraError(info.message)
        self._open = True
        return info

    def _load(self, pose: PoseKind) -> list[FrameBundle]:
        pose_root = self.replay_root / pose.value
        path = pose_root / "analysis_window.npz"
        if not path.exists():
            path = pose_root / "frames.npz"
        if not path.exists():
            raise CameraError(f"缺少回放文件：{path}")
        calibration_path = pose_root / "analysis_calibration.json"
        if not calibration_path.exists():
            calibration_path = pose_root / "calibration.json"
        calibration = (
            json.loads(calibration_path.read_text(encoding="utf-8"))
            if calibration_path.exists()
            else {"coordinate_system": "ground_mm_right_up_forward", "device_serial": "REPLAY"}
        )
        self._verify_recorded_files(pose_root, (path, calibration_path))
        with np.load(path, allow_pickle=False) as data:
            colors = data["color"]
            depths = data["depth_mm"]
            masks = data["body_mask"]
            joints = data["joints_mm"]
            confidence = data["joint_confidence"]
            timestamps = data["timestamp_usec"]
            orientations = (
                data["joint_orientations_wxyz"]
                if "joint_orientations_wxyz" in data.files
                else None
            )
        if colors.ndim == 3:
            colors = colors[np.newaxis, ...]
        if depths.ndim == 2:
            depths = depths[np.newaxis, ...]
        if masks.ndim == 2:
            masks = masks[np.newaxis, ...]
        return [
            FrameBundle(
                color=colors[min(index, len(colors) - 1)],
                depth_mm=depths[min(index, len(depths) - 1)],
                body_mask=masks[min(index, len(masks) - 1)],
                joints_mm=joints[index],
                joint_confidence=confidence[index],
                calibration=calibration,
                timestamp_usec=int(timestamps[index]),
                joint_orientations_wxyz=(
                    orientations[index] if orientations is not None else None
                ),
                metadata={"body_count": 1, "contour_coverage": 0.98},
            )
            for index in range(len(joints))
        ]

    @staticmethod
    def _verify_recorded_files(pose_root: Path, paths: tuple[Path, ...]) -> None:
        manifest_path = pose_root / "manifest.json"
        if not manifest_path.exists():
            return
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CameraError(f"回放 manifest 无法读取：{manifest_path}") from exc
        files = manifest.get("files", {})
        for path in paths:
            if not path.exists():
                continue
            relative = path.relative_to(pose_root).as_posix()
            expected = files.get(relative)
            if not expected:
                continue
            if path.stat().st_size != int(expected.get("bytes", -1)):
                raise CameraError(f"回放文件大小校验失败：{relative}")
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            if digest.hexdigest() != expected.get("sha256"):
                raise CameraError(f"回放文件 SHA-256 校验失败：{relative}")

    def preview_frame(self, pose: PoseKind) -> FrameBundle:
        return self._load(pose)[0]

    def capture_stable_window(
        self, pose: PoseKind, duration_seconds: float = 2.0
    ) -> list[FrameBundle]:
        del duration_seconds
        return self._load(pose)

    def close(self) -> None:
        self._open = False


class AzureKinectAdapter(DepthCameraAdapter):
    """Late-bound adapter; Microsoft SDK/runtime binaries are never bundled."""

    def __init__(self):
        self._pykinect: Any = None
        self._device: Any = None
        self._tracker: Any = None
        self._tracking_mode = "DirectML"

    @staticmethod
    def _runtime_candidates() -> tuple[Path, ...]:
        program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        return (
            program_files / "Azure Kinect SDK v1.4.1" / "sdk" / "windows-desktop" / "amd64" / "release" / "bin" / "k4a.dll",
            program_files / "Azure Kinect Body Tracking SDK" / "tools" / "k4abt.dll",
        )

    def probe(self) -> DeviceInfo:
        try:
            importlib.import_module("pykinect_azure")
        except Exception as exc:
            return DeviceInfo(
                available=False,
                backend="azure_kinect",
                serial_number="",
                model="Azure Kinect DK",
                tracking_mode="DirectML → CPU",
                message=f"Python 绑定不可用：{exc}",
                requires_calibration=True,
            )
        if not all(path.exists() for path in self._runtime_candidates()):
            return DeviceInfo(
                available=False,
                backend="azure_kinect",
                serial_number="",
                model="Azure Kinect DK",
                tracking_mode="DirectML → CPU",
                message="未检测到 Azure Kinect Sensor/Body Tracking 运行库",
                requires_calibration=True,
            )
        return DeviceInfo(
            available=True,
            backend="azure_kinect",
            serial_number="待设备打开后读取",
            model="Azure Kinect DK",
            tracking_mode="DirectML → CPU",
            message="SDK 已发现，等待打开设备",
            requires_calibration=True,
        )

    def open(self) -> DeviceInfo:
        probe = self.probe()
        if not probe.available:
            raise CameraError(probe.message)
        self._pykinect = importlib.import_module("pykinect_azure")
        self._pykinect.initialize_libraries(track_body=True)
        self._device = self._pykinect.start_device()
        try:
            config = self._pykinect.default_tracker_configuration
            mode = getattr(self._pykinect, "K4ABT_TRACKER_PROCESSING_MODE_GPU_DIRECTML")
            config.processing_mode = mode
            self._tracker = self._pykinect.start_body_tracker(config)
        except Exception:
            self._tracking_mode = "CPU（DirectML 不可用）"
            config = self._pykinect.default_tracker_configuration
            cpu_mode = getattr(self._pykinect, "K4ABT_TRACKER_PROCESSING_MODE_CPU", None)
            if cpu_mode is not None:
                config.processing_mode = cpu_mode
            self._tracker = self._pykinect.start_body_tracker(config)
        serial = str(getattr(self._device, "serial_number", "AZURE-KINECT"))
        return DeviceInfo(
            available=True,
            backend="azure_kinect",
            serial_number=serial,
            model="Azure Kinect DK",
            tracking_mode=self._tracking_mode,
            message="相机与 Body Tracking 已连接",
            requires_calibration=True,
        )

    def _read_native(self) -> FrameBundle:
        if self._device is None or self._tracker is None:
            self.open()
        capture = self._device.update()
        body_frame = self._tracker.update()
        body_count = int(body_frame.get_num_bodies())
        if body_count < 1:
            raise CameraError("未检测到人体")
        color_ok, color = capture.get_color_image()
        depth_ok, depth = capture.get_depth_image()
        if not color_ok or not depth_ok:
            raise CameraError("RGB 或 Depth 帧无效")
        skeleton = body_frame.get_body_skeleton(0)
        native_joints = np.asarray(skeleton.joints)
        joints = np.zeros((32, 3), dtype=np.float64)
        confidence = np.zeros(32, dtype=np.uint8)
        orientations = np.full((32, 4), np.nan, dtype=np.float64)
        for index in range(32):
            joint = native_joints[index]
            try:
                position = joint.position
            except AttributeError:
                position = joint["position"]
            coordinates = getattr(position, "xyz", position)
            joints[index] = (
                float(coordinates[0]),
                -float(coordinates[1]),
                float(coordinates[2]),
            )
            try:
                native_orientation = joint.orientation
            except AttributeError:
                try:
                    native_orientation = joint["orientation"]
                except (KeyError, TypeError):
                    native_orientation = None
            if native_orientation is not None:
                values = getattr(native_orientation, "wxyz", native_orientation)
                try:
                    array = np.asarray(values, dtype=float).reshape(-1)
                except (TypeError, ValueError):
                    array = np.array(
                        [
                            getattr(native_orientation, "w"),
                            getattr(native_orientation, "x"),
                            getattr(native_orientation, "y"),
                            getattr(native_orientation, "z"),
                        ],
                        dtype=float,
                    )
                if array.size == 4 and np.all(np.isfinite(array)):
                    try:
                        orientations[index] = reflect_camera_y_to_up(array)
                    except ValueError:
                        pass
            try:
                confidence[index] = int(joint.confidence_level)
            except AttributeError:
                confidence[index] = int(joint["confidence_level"])
        mask_ok, mask = body_frame.get_body_index_map()
        if not mask_ok:
            mask = np.where(np.asarray(depth) > 0, 1, 0).astype(np.uint8)
        else:
            mask = (np.asarray(mask) != 255).astype(np.uint8)
        intrinsics: dict[str, float] = {}
        try:
            native_calibration = self._device.calibration
            camera_calibration = native_calibration.depth_camera_calibration
            parameters = camera_calibration.intrinsics.parameters.param
            intrinsics = {
                "fx": float(parameters.fx),
                "fy": float(parameters.fy),
                "cx": float(parameters.cx),
                "cy": float(parameters.cy),
            }
        except (AttributeError, TypeError, ValueError):
            pass
        return FrameBundle(
            color=np.asarray(color)[..., :3],
            depth_mm=np.asarray(depth),
            body_mask=mask,
            joints_mm=joints,
            joint_confidence=confidence,
            calibration={
                "coordinate_system": "camera_mm_right_up_forward",
                "device_serial": str(getattr(self._device, "serial_number", "AZURE-KINECT")),
                "native_calibration_available": hasattr(self._device, "calibration"),
                "intrinsics": intrinsics,
            },
            timestamp_usec=int(time.time_ns() // 1000),
            joint_orientations_wxyz=(
                orientations if np.all(np.isfinite(orientations)) else None
            ),
            metadata={"body_count": body_count},
        )

    def preview_frame(self, pose: PoseKind) -> FrameBundle:
        del pose
        return self._read_native()

    def capture_stable_window(
        self, pose: PoseKind, duration_seconds: float = 2.0
    ) -> list[FrameBundle]:
        del pose
        deadline = time.monotonic() + duration_seconds
        frames: list[FrameBundle] = []
        while time.monotonic() < deadline:
            frames.append(self._read_native())
        return frames

    def close(self) -> None:
        for object_ in (self._tracker, self._device):
            close = getattr(object_, "shutdown", None) or getattr(object_, "close", None)
            if close:
                try:
                    close()
                except Exception:
                    pass
        self._tracker = None
        self._device = None


class AutoCameraAdapter(DepthCameraAdapter):
    """Prefer Azure Kinect, but make the auto-mode fallback explicit in UI."""

    def __init__(self):
        self.azure = AzureKinectAdapter()
        self.active: DepthCameraAdapter | None = None

    def probe(self) -> DeviceInfo:
        if self.active is not None:
            return self.active.probe()
        azure_info = self.azure.probe()
        if azure_info.available:
            return azure_info
        return MockCameraAdapter(
            os.environ.get("POSTURE_MOCK_SCENARIO", "normal"),
            fallback_message=azure_info.message,
        ).probe()

    def open(self) -> DeviceInfo:
        azure_info = self.azure.probe()
        if azure_info.available:
            try:
                info = self.azure.open()
                self.active = self.azure
                return info
            except Exception as exc:
                self.active = MockCameraAdapter(
                    os.environ.get("POSTURE_MOCK_SCENARIO", "normal"),
                    fallback_message=str(exc),
                )
        else:
            self.active = MockCameraAdapter(
                os.environ.get("POSTURE_MOCK_SCENARIO", "normal"),
                fallback_message=azure_info.message,
            )
        return self.active.open()

    def _require_active(self) -> DepthCameraAdapter:
        if self.active is None:
            self.open()
        assert self.active is not None
        return self.active

    def preview_frame(self, pose: PoseKind) -> FrameBundle:
        return self._require_active().preview_frame(pose)

    def capture_stable_window(
        self, pose: PoseKind, duration_seconds: float = 2.0
    ) -> list[FrameBundle]:
        return self._require_active().capture_stable_window(pose, duration_seconds)

    def close(self) -> None:
        if self.active is not None:
            self.active.close()
        self.azure.close()
        self.active = None


def create_adapter(backend: str, replay_root: Path | None = None) -> DepthCameraAdapter:
    backend = backend.lower()
    if backend == "mock":
        return MockCameraAdapter(os.environ.get("POSTURE_MOCK_SCENARIO", "normal"))
    if backend == "replay":
        if replay_root is None:
            raise CameraError("回放模式需要 replay_root")
        return ReplayCameraAdapter(replay_root)
    if backend in {"azure", "azure_kinect"}:
        return AzureKinectAdapter()
    if backend == "auto":
        return AutoCameraAdapter()
    raise CameraError(f"未知相机后端：{backend}")


def _lightweight_preview(frame: FrameBundle, max_width: int = 320) -> FrameBundle:
    step = max(1, int(np.ceil(frame.depth_mm.shape[1] / max_width)))
    depth = frame.depth_mm[::step, ::step]
    mask = frame.body_mask[::step, ::step]
    color = np.zeros((*depth.shape, 3), dtype=np.uint8)
    calibration = dict(frame.calibration)
    intrinsics = dict(calibration.get("intrinsics", {}))
    for key in ("fx", "fy", "cx", "cy"):
        if key in intrinsics:
            intrinsics[key] = float(intrinsics[key]) / step
    calibration["intrinsics"] = intrinsics
    return FrameBundle(
        color=color,
        depth_mm=depth,
        body_mask=mask,
        joints_mm=frame.joints_mm,
        joint_confidence=frame.joint_confidence,
        calibration=calibration,
        timestamp_usec=frame.timestamp_usec,
        joint_orientations_wxyz=frame.joint_orientations_wxyz,
        metadata=frame.metadata,
    )


def _camera_process_main(
    command_queue: mp.Queue, result_queue: mp.Queue, backend: str, replay_root: str | None
) -> None:
    adapter: DepthCameraAdapter | None = None
    try:
        adapter = create_adapter(backend, Path(replay_root) if replay_root else None)
        while True:
            command = command_queue.get()
            action = command.get("action")
            try:
                if action == "probe":
                    result_queue.put({"ok": True, "result": asdict(adapter.probe())})
                elif action == "open":
                    result_queue.put({"ok": True, "result": asdict(adapter.open())})
                elif action == "preview":
                    frame = adapter.preview_frame(PoseKind(command["pose"]))
                    result_queue.put({"ok": True, "result": _lightweight_preview(frame)})
                elif action == "capture":
                    frames = adapter.capture_stable_window(
                        PoseKind(command["pose"]), float(command.get("duration", 2.0))
                    )
                    result_queue.put({"ok": True, "result": frames})
                elif action == "capture_store":
                    from posture_assessment.posture.geometry import ALGORITHM_VERSION
                    from posture_assessment.posture.quality import evaluate_quality
                    from posture_assessment.posture.storage import ArtifactStore

                    pose = PoseKind(command["pose"])
                    frames = adapter.capture_stable_window(
                        pose, float(command.get("duration", 2.0))
                    )
                    calibration_update = command.get("calibration_update", {})
                    for frame in frames:
                        frame.calibration.update(calibration_update)
                    if command.get("quality_mode") == "pelvis":
                        from posture_assessment.pelvis.quality import evaluate_pelvis_quality

                        quality = evaluate_pelvis_quality(frames, pose)
                    else:
                        quality = evaluate_quality(frames, pose)
                    quality_details_update = command.get("quality_details_update", {})
                    if quality_details_update:
                        quality = replace(
                            quality,
                            details={**quality.details, **quality_details_update},
                        )
                    device = adapter.probe()
                    artifact_dir, manifest_sha, archived_path, analysis_frames = ArtifactStore(
                        Path(command["assessment_root"])
                    ).save_capture(
                        command["session_no"],
                        pose,
                        frames,
                        quality,
                        device,
                        str(command.get("algorithm_version") or ALGORITHM_VERSION),
                        int(command["attempt_no"]),
                        namespace=command.get("namespace"),
                    )
                    result_queue.put(
                        {
                            "ok": True,
                            "result": StoredCapturePayload(
                                artifact_dir=str(artifact_dir),
                                manifest_sha256=manifest_sha,
                                archived_path=str(archived_path) if archived_path else None,
                                analysis_frames=analysis_frames,
                                quality=quality,
                                device=device,
                            ),
                        }
                    )
                elif action == "calibrate":
                    from dataclasses import asdict as profile_asdict
                    from posture_assessment.posture.calibration import CalibrationManager

                    frames = [adapter.preview_frame(PoseKind.FRONT) for _ in range(6)]
                    profile = CalibrationManager(Path(command["calibration_root"])).calibrate(
                        adapter.probe(), frames
                    )
                    result_queue.put({"ok": True, "result": profile_asdict(profile)})
                elif action == "close":
                    adapter.close()
                    result_queue.put({"ok": True, "result": None})
                    return
                else:
                    raise CameraError(f"未知相机进程命令：{action}")
            except Exception as exc:
                result_queue.put({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
    finally:
        if adapter is not None:
            adapter.close()


class CameraProcessClient:
    """Crash-isolated camera client used by the PySide6 process."""

    def __init__(self, backend: str = "auto", replay_root: Path | None = None):
        context = mp.get_context("spawn")
        self._commands = context.Queue(maxsize=2)
        self._results = context.Queue(maxsize=2)
        self._process = context.Process(
            target=_camera_process_main,
            args=(self._commands, self._results, backend, str(replay_root) if replay_root else None),
            name="posture-camera-worker",
            daemon=True,
        )
        self._process.start()

    def _request(self, payload: dict[str, Any], timeout: float = 30.0) -> Any:
        if not self._process.is_alive():
            raise CameraError("相机工作进程已异常退出")
        self._commands.put(payload, timeout=2.0)
        try:
            response = self._results.get(timeout=timeout)
        except queue.Empty as exc:
            raise CameraError("相机工作进程响应超时") from exc
        if not response["ok"]:
            raise CameraError(response["error"])
        return response["result"]

    def probe(self) -> DeviceInfo:
        return DeviceInfo(**self._request({"action": "probe"}))

    def open(self) -> DeviceInfo:
        return DeviceInfo(**self._request({"action": "open"}, timeout=45.0))

    def preview_frame(self, pose: PoseKind) -> FrameBundle:
        return self._request({"action": "preview", "pose": pose.value})

    def capture_stable_window(self, pose: PoseKind, duration_seconds: float = 2.0) -> list[FrameBundle]:
        return self._request(
            {"action": "capture", "pose": pose.value, "duration": duration_seconds},
            timeout=max(30.0, duration_seconds + 20.0),
        )

    def capture_and_store(
        self,
        pose: PoseKind,
        duration_seconds: float,
        assessment_root: Path,
        session_no: str,
        attempt_no: int,
        *,
        namespace: str | None = None,
        algorithm_version: str | None = None,
        calibration_update: dict[str, Any] | None = None,
        quality_mode: str = "posture",
        quality_details_update: dict[str, Any] | None = None,
    ) -> StoredCapturePayload:
        return self._request(
            {
                "action": "capture_store",
                "pose": pose.value,
                "duration": duration_seconds,
                "assessment_root": str(assessment_root),
                "session_no": session_no,
                "attempt_no": attempt_no,
                "namespace": namespace,
                "algorithm_version": algorithm_version,
                "calibration_update": calibration_update or {},
                "quality_mode": quality_mode,
                "quality_details_update": quality_details_update or {},
            },
            timeout=max(60.0, duration_seconds + 45.0),
        )

    def calibrate(self, calibration_root: Path) -> dict[str, Any]:
        return self._request(
            {"action": "calibrate", "calibration_root": str(calibration_root)},
            timeout=45.0,
        )

    def close(self) -> None:
        if self._process.is_alive():
            try:
                self._request({"action": "close"}, timeout=5.0)
            except Exception:
                self._process.terminate()
            self._process.join(timeout=3.0)
        if self._process.is_alive():
            self._process.terminate()
            self._process.join(timeout=2.0)
