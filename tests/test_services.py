from __future__ import annotations

from datetime import date

import pytest

from posture_assessment.services import (
    AuthService,
    DuplicatePatientError,
    UserInput,
    ValidationError,
)


def complete_user(patient_no: str = "P0001") -> UserInput:
    return UserInput(
        patient_no=patient_no,
        name="测试用户",
        sex="男",
        birth_date=date(1990, 1, 2),
        mobile="13800138000",
        height_cm=175,
        weight_kg=70,
        address="北京市海淀区",
        email="test@example.com",
    )


def test_default_admin_authentication(db):
    auth = AuthService(db)
    assert auth.authenticate("admin", "admin123") is True
    assert auth.authenticate("admin", "wrong") is False


def test_user_crud_search_and_soft_delete(user_service):
    user = user_service.create(complete_user())
    assert user.profile_complete is True
    assert user.age >= 30
    assert user_service.search(name="测试").total == 1

    updated = complete_user()
    updated.name = "修改后的用户"
    user_service.update(user.id, updated)
    assert user_service.get(user.id).name == "修改后的用户"

    user_service.archive(user.id)
    assert user_service.search().total == 0


def test_unique_patient_number(user_service):
    user_service.create(complete_user("UNIQUE01"))
    with pytest.raises(DuplicatePatientError):
        user_service.create(complete_user("UNIQUE01"))


def test_incomplete_import_is_allowed_but_detection_is_blocked(user_service):
    user = user_service.create(
        UserInput(
            patient_no="IMPORT01",
            name="待补全用户",
            sex="女",
            birth_date=date(1995, 5, 5),
        ),
        require_complete=False,
    )
    assert user.profile_complete is False
    with pytest.raises(ValidationError, match="开始检测前请补齐"):
        user_service.start_assessment(user.id)


def test_start_assessment_creates_posture_session(user_service):
    user = user_service.create(complete_user("SESSION01"))
    assessment = user_service.start_assessment(user.id)
    assert assessment.assessment_type == "体态检测"
    assert assessment.status == "待检测"
    assert len(user_service.list_assessments(user.id)) == 1

