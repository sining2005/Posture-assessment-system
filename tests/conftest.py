from __future__ import annotations

import os
from pathlib import Path

import pytest

from posture_assessment.config import AppSettings
from posture_assessment.database import DatabaseManager
from posture_assessment.services import UserService

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def settings(tmp_path: Path) -> AppSettings:
    return AppSettings.load(tmp_path / "app-data")


@pytest.fixture
def db(settings: AppSettings) -> DatabaseManager:
    manager = DatabaseManager(settings.database_path)
    manager.initialize()
    return manager


@pytest.fixture
def user_service(db: DatabaseManager) -> UserService:
    return UserService(db)

