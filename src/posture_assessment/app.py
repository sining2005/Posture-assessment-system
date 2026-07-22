from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QObject
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

from posture_assessment.config import AppSettings
from posture_assessment.database import DatabaseManager
from posture_assessment.dongle import MockDongleAdapter
from posture_assessment.import_export import UserExportService, UserImportService
from posture_assessment.services import AuthService, UserService
from posture_assessment.ui.login import LoginWindow
from posture_assessment.ui.main_window import MainWindow
from posture_assessment.ui.theme import APP_STYLESHEET


def load_chinese_font(app: QApplication) -> None:
    candidates = [
        Path(r"C:\Windows\Fonts\msyh.ttc"),
        Path(r"C:\Windows\Fonts\simhei.ttf"),
        Path(r"C:\Windows\Fonts\simsun.ttc"),
    ]
    for path in candidates:
        if not path.exists():
            continue
        font_id = QFontDatabase.addApplicationFont(str(path))
        families = QFontDatabase.applicationFontFamilies(font_id)
        if families:
            app.setFont(QFont(families[0], 10))
            return


class ApplicationController(QObject):
    def __init__(self, settings: AppSettings):
        super().__init__()
        self.settings = settings
        self.db = DatabaseManager(settings.database_path)
        self.db.initialize()
        self.dongle = MockDongleAdapter(settings.dongle_state)
        self.auth_service = AuthService(self.db)
        self.user_service = UserService(self.db)
        self.import_service = UserImportService(
            self.db, self.user_service, settings.import_log_dir
        )
        self.export_service = UserExportService(self.db, settings.export_dir)
        self.login_window: LoginWindow | None = None
        self.main_window: MainWindow | None = None

    def start(self) -> None:
        self.login_window = LoginWindow(self.auth_service, self.dongle)
        self.login_window.login_success.connect(self._show_main_window)
        self.login_window.showMaximized()

    def _show_main_window(self, operator_name: str) -> None:
        self.main_window = MainWindow(
            self.settings,
            self.user_service,
            self.import_service,
            self.export_service,
            self.dongle,
            operator_name,
        )
        self.main_window.showMaximized()
        if self.login_window:
            self.login_window.close()
            self.login_window.deleteLater()
            self.login_window = None


def main(argv: list[str] | None = None, data_dir: Path | None = None) -> int:
    app = QApplication(argv or sys.argv)
    app.setApplicationName("体态评估系统")
    app.setOrganizationName("PostureAssessment")
    app.setStyle("Fusion")
    load_chinese_font(app)
    app.setStyleSheet(APP_STYLESHEET)
    settings = AppSettings.load(data_dir)
    controller = ApplicationController(settings)
    controller.start()
    app._controller = controller  # type: ignore[attr-defined]
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
