from __future__ import annotations

import math

import numpy as np


def normalize_quaternion(quaternion_wxyz: np.ndarray) -> np.ndarray:
    quaternion = np.asarray(quaternion_wxyz, dtype=float)
    if quaternion.shape != (4,):
        raise ValueError("四元数必须为 (4,) 的 wxyz 数组")
    norm = float(np.linalg.norm(quaternion))
    if not np.isfinite(norm) or norm <= 1e-9:
        raise ValueError("四元数不可归一化")
    quaternion = quaternion / norm
    return quaternion if quaternion[0] >= 0 else -quaternion


def quaternion_to_matrix(quaternion_wxyz: np.ndarray) -> np.ndarray:
    w, x, y, z = normalize_quaternion(quaternion_wxyz)
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
            [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
            [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
        ],
        dtype=float,
    )


def matrix_to_quaternion(matrix: np.ndarray) -> np.ndarray:
    rotation = np.asarray(matrix, dtype=float)
    if rotation.shape != (3, 3):
        raise ValueError("旋转矩阵必须为 (3, 3)")
    trace = float(np.trace(rotation))
    if trace > 0:
        scale = math.sqrt(trace + 1.0) * 2
        values = np.array(
            [
                0.25 * scale,
                (rotation[2, 1] - rotation[1, 2]) / scale,
                (rotation[0, 2] - rotation[2, 0]) / scale,
                (rotation[1, 0] - rotation[0, 1]) / scale,
            ]
        )
    else:
        diagonal = np.diag(rotation)
        index = int(np.argmax(diagonal))
        if index == 0:
            scale = math.sqrt(max(1e-12, 1.0 + rotation[0, 0] - rotation[1, 1] - rotation[2, 2])) * 2
            values = np.array([(rotation[2, 1] - rotation[1, 2]) / scale, 0.25 * scale, (rotation[0, 1] + rotation[1, 0]) / scale, (rotation[0, 2] + rotation[2, 0]) / scale])
        elif index == 1:
            scale = math.sqrt(max(1e-12, 1.0 + rotation[1, 1] - rotation[0, 0] - rotation[2, 2])) * 2
            values = np.array([(rotation[0, 2] - rotation[2, 0]) / scale, (rotation[0, 1] + rotation[1, 0]) / scale, 0.25 * scale, (rotation[1, 2] + rotation[2, 1]) / scale])
        else:
            scale = math.sqrt(max(1e-12, 1.0 + rotation[2, 2] - rotation[0, 0] - rotation[1, 1])) * 2
            values = np.array([(rotation[1, 0] - rotation[0, 1]) / scale, (rotation[0, 2] + rotation[2, 0]) / scale, (rotation[1, 2] + rotation[2, 1]) / scale, 0.25 * scale])
    return normalize_quaternion(values)


def rotation_about(axis: str, angle_deg: float) -> np.ndarray:
    angle = math.radians(float(angle_deg))
    cosine, sine = math.cos(angle), math.sin(angle)
    if axis == "x":
        return np.array([[1, 0, 0], [0, cosine, -sine], [0, sine, cosine]], dtype=float)
    if axis == "y":
        return np.array([[cosine, 0, sine], [0, 1, 0], [-sine, 0, cosine]], dtype=float)
    if axis == "z":
        return np.array([[cosine, -sine, 0], [sine, cosine, 0], [0, 0, 1]], dtype=float)
    raise ValueError(f"未知旋转轴：{axis}")


def average_quaternions(quaternions_wxyz: np.ndarray, weights: np.ndarray | None = None) -> np.ndarray:
    quaternions = np.asarray(quaternions_wxyz, dtype=float)
    if quaternions.ndim != 2 or quaternions.shape[1] != 4 or len(quaternions) == 0:
        raise ValueError("四元数集合必须为非空 (N, 4) 数组")
    normalized = np.stack([normalize_quaternion(value) for value in quaternions])
    weights_array = np.ones(len(normalized), dtype=float) if weights is None else np.asarray(weights, dtype=float)
    if weights_array.shape != (len(normalized),):
        raise ValueError("权重数量与四元数数量不一致")
    accumulator = np.einsum("n,ni,nj->ij", weights_array, normalized, normalized)
    eigenvalues, eigenvectors = np.linalg.eigh(accumulator)
    return normalize_quaternion(eigenvectors[:, int(np.argmax(eigenvalues))])


def quaternion_angular_distance_deg(first_wxyz: np.ndarray, second_wxyz: np.ndarray) -> float:
    first = normalize_quaternion(first_wxyz)
    second = normalize_quaternion(second_wxyz)
    dot = min(1.0, max(-1.0, abs(float(np.dot(first, second)))))
    return math.degrees(2.0 * math.acos(dot))


def reflect_camera_y_to_up(quaternion_wxyz: np.ndarray) -> np.ndarray:
    """Convert Azure's right/down/forward joint frame to right/up/forward."""
    reflection = np.diag([1.0, -1.0, 1.0])
    converted = reflection @ quaternion_to_matrix(quaternion_wxyz) @ reflection
    return matrix_to_quaternion(converted)
