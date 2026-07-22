from __future__ import annotations

from datetime import date

from posture_assessment.posture.camera import MockCameraAdapter
from posture_assessment.posture.service import PostureService
from posture_assessment.posture.types import POSE_SEQUENCE
from posture_assessment.services import UserInput
from posture_assessment.ui.posture_page import PosturePage, PostureUiState


def test_posture_page_context_and_device_ready_state(
    qtbot, db, settings, user_service
):
    user = user_service.create(
        UserInput(
            patient_no="UI-POSTURE",
            name="界面测试",
            sex="女",
            birth_date=date(1992, 2, 2),
            mobile="13800138000",
            height_cm=165,
            weight_kg=55,
            address="上海市",
        )
    )
    assessment = user_service.start_assessment(user.id)
    adapter = MockCameraAdapter()
    service = PostureService(db, settings, adapter=adapter, use_process=False)
    page = PosturePage(service, "admin")
    qtbot.addWidget(page)
    page.set_detection_context(user, assessment)
    assert page.state is PostureUiState.OVERVIEW
    assert assessment.session_no in page.patient_bar.text()
    assert all(not page.pose_cards[pose].button.isEnabled() for pose in POSE_SEQUENCE)

    page._device_ready(service.device_self_check())
    assert all(page.pose_cards[pose].button.isEnabled() for pose in POSE_SEQUENCE)
    assert "模拟演示模式" in page.device_label.text()
    page.close_camera()
    assert adapter._open is False


def test_posture_page_renders_completed_analysis_and_emits_report_hook(
    qtbot, db, settings, user_service
):
    user = user_service.create(
        UserInput(
            patient_no="UI-RESULT",
            name="结果界面",
            sex="男",
            birth_date=date(1988, 3, 3),
            mobile="13800138000",
            height_cm=178,
            weight_kg=72,
            address="广州市",
        )
    )
    assessment = user_service.start_assessment(user.id)
    service = PostureService(
        db, settings, adapter=MockCameraAdapter(), use_process=False
    )
    page = PosturePage(service, "admin")
    qtbot.addWidget(page)
    page.set_detection_context(user, assessment)
    service.device_self_check()
    for pose in POSE_SEQUENCE:
        _, quality = service.capture_pose(assessment.id, pose)
        page.qualities[pose] = quality
        page.frames[pose] = service.representative_frame(assessment.id, pose)
    result = service.analyze(assessment.id)
    with qtbot.waitSignal(page.analysis_ready, timeout=1000) as blocker:
        page._analysis_finished(result)
    assert blocker.args == [assessment.id]
    assert page.state is PostureUiState.RESULT
    assert page.result_table.rowCount() > 0
    assert "不作为医学诊断" in page.result_disclaimer.text()
    page.close_camera()
