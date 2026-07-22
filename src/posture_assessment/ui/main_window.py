from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from posture_assessment.config import AppSettings
from posture_assessment.dongle import DongleAdapter
from posture_assessment.import_export import UserExportService, UserImportService
from posture_assessment.models import AssessmentSession, User
from posture_assessment.pelvis.service import PelvisService
from posture_assessment.posture.service import PostureService
from posture_assessment.services import UserService
from posture_assessment.ui.dialogs import SettingsDialog
from posture_assessment.ui.pelvis_page import PelvisPage
from posture_assessment.ui.posture_page import PosturePage
from posture_assessment.ui.theme import NAV, PINK, PINK_DARK
from posture_assessment.ui.user_page import UserPage


class PlaceholderPage(QWidget):
    def __init__(self, module_name: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.module_name = module_name
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title = QLabel(module_name)
        self.title.setStyleSheet("font-size:32px; font-weight:700;")
        layout.addWidget(self.title, alignment=Qt.AlignmentFlag.AlignCenter)
        self.context = QLabel("该模块将在后续阶段实现")
        self.context.setObjectName("muted")
        self.context.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.context, alignment=Qt.AlignmentFlag.AlignCenter)

    def set_detection_context(self, user: User, assessment: AssessmentSession) -> None:
        self.context.setText(
            f"当前检测对象：{user.name}（{user.patient_no}）\n"
            f"检测会话：{assessment.session_no}\n\n用户信息阶段已完成检测入口交接。"
        )


class MainWindow(QMainWindow):
    MODULES = [
        "用户信息",
        "体态检测",
        "步态检测",
        "骨盆检测",
        "脊柱评估",
        "关节活动度",
        "足底检测",
        "查看报告",
        "生成",
    ]

    def __init__(
        self,
        settings: AppSettings,
        user_service: UserService,
        import_service: UserImportService,
        export_service: UserExportService,
        dongle: DongleAdapter,
        operator_name: str,
        parent: QWidget | None = None,
        posture_service: PostureService | None = None,
        pelvis_service: PelvisService | None = None,
    ):
        super().__init__(parent)
        self.settings = settings
        self.dongle = dongle
        self.operator_name = operator_name
        self.posture_service = posture_service or PostureService(user_service.db, settings)
        self.pelvis_service = pelvis_service or PelvisService(user_service.db, settings)
        self.setWindowTitle("体态评估系统")
        self.setMinimumSize(1200, 760)
        self._build_ui(user_service, import_service, export_service)

    def _build_ui(
        self,
        user_service: UserService,
        import_service: UserImportService,
        export_service: UserExportService,
    ) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        title_bar = QFrame()
        title_bar.setFixedHeight(36)
        title_bar.setStyleSheet("background:#f6f6f6; border-bottom:1px solid #ddd;")
        title_layout = QHBoxLayout(title_bar)
        title_layout.setContentsMargins(10, 0, 12, 0)
        logo = QLabel("P  POSTURE")
        logo.setStyleSheet("color:#777; font-weight:700;")
        title_layout.addWidget(logo)
        title_layout.addStretch()
        title_layout.addWidget(QLabel(f"当前用户：{self.operator_name}"))
        settings_button = QPushButton("系统设置")
        settings_button.setStyleSheet("border:none; background:transparent;")
        settings_button.clicked.connect(self._open_settings)
        title_layout.addWidget(settings_button)
        root.addWidget(title_bar)

        nav = QFrame()
        nav.setFixedHeight(82)
        nav.setStyleSheet(f"background:{NAV};")
        nav_layout = QHBoxLayout(nav)
        nav_layout.setContentsMargins(22, 16, 22, 16)
        nav_layout.setSpacing(20)
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self.nav_buttons: dict[str, QPushButton] = {}
        self.pages = QStackedWidget()
        self.page_indexes: dict[str, int] = {}
        for module in self.MODULES:
            button = QPushButton(module)
            button.setCheckable(True)
            button.setMinimumSize(130, 48)
            button.setStyleSheet(
                f"QPushButton {{ color:white; font-size:17px; font-weight:600; border:none; "
                f"border-radius:3px; background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 {PINK},stop:1 {PINK_DARK}); }}"
                "QPushButton:hover { border:1px solid white; }"
                "QPushButton:checked { border:2px solid white; background:#c53e50; }"
            )
            button.clicked.connect(lambda checked=False, name=module: self.show_module(name))
            self.nav_group.addButton(button)
            self.nav_buttons[module] = button
            nav_layout.addWidget(button)
        nav_layout.addStretch()
        root.addWidget(nav)

        self.user_page = UserPage(
            user_service,
            import_service,
            export_service,
            self.settings.export_dir,
        )
        self.user_page.start_detection.connect(self._start_detection)
        self.page_indexes["用户信息"] = self.pages.addWidget(self.user_page)
        self.posture_page = PosturePage(self.posture_service, self.operator_name)
        self.posture_page.analysis_ready.connect(self._analysis_ready)
        self.page_indexes["体态检测"] = self.pages.addWidget(self.posture_page)
        for module in self.MODULES[2:]:
            if module == "骨盆检测":
                self.pelvis_page = PelvisPage(self.pelvis_service, self.operator_name)
                self.pelvis_page.analysis_ready.connect(self._pelvis_analysis_ready)
                page = self.pelvis_page
            else:
                page = PlaceholderPage(module)
            self.page_indexes[module] = self.pages.addWidget(page)
        root.addWidget(self.pages, 1)
        self.setCentralWidget(central)
        self.nav_buttons["用户信息"].setChecked(True)
        self.show_module("用户信息")

    def show_module(self, module: str) -> None:
        # A single Azure Kinect cannot be owned by both worker processes at once.
        if module == "骨盆检测":
            self.posture_page.close_camera()
        elif module == "体态检测" and hasattr(self, "pelvis_page"):
            self.pelvis_page.close_camera()
        self.pages.setCurrentIndex(self.page_indexes[module])
        self.nav_buttons[module].setChecked(True)

    def _start_detection(self, user: User, assessment: AssessmentSession) -> None:
        self.posture_page.set_detection_context(user, assessment)
        self.pelvis_page.set_detection_context(user, assessment)
        self.show_module("体态检测")

    def _analysis_ready(self, assessment_id: int) -> None:
        page = self.pages.widget(self.page_indexes["查看报告"])
        if isinstance(page, PlaceholderPage):
            page.context.setText(
                f"体态 AnalysisResult 已就绪（session_id={assessment_id}）。\n"
                "报告模块可通过 analysis_ready(session_id) 读取稳定数据包。"
            )

    def _pelvis_analysis_ready(self, assessment_id: int) -> None:
        page = self.pages.widget(self.page_indexes["查看报告"])
        if isinstance(page, PlaceholderPage):
            page.context.setText(
                f"骨盆 PelvisAnalysisResult 已就绪（session_id={assessment_id}）。\n"
                "报告模块可读取结构化代理指标、质量门数据、算法版本和免责声明。"
            )

    def _open_settings(self) -> None:
        SettingsDialog(
            self.settings,
            self.dongle,
            self,
            posture_service=self.posture_service,
        ).exec()

    def closeEvent(self, event) -> None:
        self.posture_page.close_camera()
        self.pelvis_page.close_camera()
        super().closeEvent(event)
