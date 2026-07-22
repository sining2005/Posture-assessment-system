from __future__ import annotations

from datetime import date

from posture_assessment.pelvis.service import PelvisService
from posture_assessment.pelvis.types import PELVIS_VIEW_SEQUENCE
from posture_assessment.posture.camera import MockCameraAdapter
from posture_assessment.services import UserInput
from posture_assessment.ui.pelvis_page import PelvisPage, PelvisUiState


def _context(user_service):
    user = user_service.create(
        UserInput(
            patient_no="UI-PELVIS",
            name="骨盆界面测试",
            sex="女",
            birth_date=date(1994, 6, 6),
            mobile="13800138000",
            height_cm=166,
            weight_kg=56,
            address="南京市",
        )
    )
    return user, user_service.start_assessment(user.id)


def test_pelvis_page_four_view_state_and_result_render(qtbot, db, settings, user_service):
    user, assessment = _context(user_service)
    adapter = MockCameraAdapter()
    service = PelvisService(db, settings, adapter=adapter, use_process=False)
    page = PelvisPage(service, "admin")
    qtbot.addWidget(page)
    page.set_detection_context(user, assessment)
    assert page.state is PelvisUiState.OVERVIEW
    assert all(not page.view_cards[view].button.isEnabled() for view in PELVIS_VIEW_SEQUENCE)

    page._device_ready(service.device_self_check())
    assert all(page.view_cards[view].button.isEnabled() for view in PELVIS_VIEW_SEQUENCE)
    for view in PELVIS_VIEW_SEQUENCE:
        _, quality = service.capture_view(assessment.id, view)
        page._capture_ready(view, quality)
    assert page.analyze_button.isEnabled()

    result = service.analyze(assessment.id)
    page._analysis_ready(result)
    assert page.state is PelvisUiState.RESULT
    assert page.result_table.rowCount() >= 5
    assert "算法版本" in page.review_label.text()
    page.close_camera()
    assert not adapter._open
