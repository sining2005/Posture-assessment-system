from __future__ import annotations

from posture_assessment.dongle import MockDongleAdapter
from posture_assessment.import_export import UserExportService, UserImportService
from posture_assessment.services import AuthService
from posture_assessment.ui.login import LoginWindow
from posture_assessment.ui.main_window import MainWindow


def test_login_window_smoke(qtbot, db):
    window = LoginWindow(AuthService(db), MockDongleAdapter("present"))
    qtbot.addWidget(window)
    window.show()
    assert window.windowTitle() == "体态评估系统 - 登录"


def test_main_window_contains_user_page(qtbot, db, user_service, settings):
    import_service = UserImportService(db, user_service, settings.import_log_dir)
    export_service = UserExportService(db, settings.export_dir)
    window = MainWindow(
        settings,
        user_service,
        import_service,
        export_service,
        MockDongleAdapter("present"),
        "admin",
    )
    qtbot.addWidget(window)
    window.show()
    assert window.pages.currentWidget() is window.user_page
    assert window.nav_buttons["用户信息"].isChecked()
