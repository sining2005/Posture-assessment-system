from __future__ import annotations

import os
from pathlib import Path

import pytest

from posture_assessment.config import AppSettings
from posture_assessment.database import DatabaseManager
from posture_assessment.services import UserService

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AppSettings:
    # 服务测试必须使用确定性模拟相机，避免真机插入后 auto 后端接管
    monkeypatch.setenv("POSTURE_CAMERA_BACKEND", "mock")
    return AppSettings.load(tmp_path / "app-data")


@pytest.fixture
def db(settings: AppSettings) -> DatabaseManager:
    manager = DatabaseManager(settings.database_path)
    manager.initialize()
    return manager


@pytest.fixture
def user_service(db: DatabaseManager) -> UserService:
    return UserService(db)

