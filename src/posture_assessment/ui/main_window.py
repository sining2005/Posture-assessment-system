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
from posture_assessment.services import UserService
# 修改：新增脊柱扫描、关节活动度与脊柱评估页面导入
from posture_assessment.ui.dialogs import SettingsDialog
from posture_assessment.ui.joint_mobility_page import JointMobilityPage
from posture_assessment.ui.spine_assessment_page import SpineAssessmentPage
from posture_assessment.ui.spine_scan_page import SpineScanPage
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
    ):
        super().__init__(parent)
        self.settings = settings
        self.dongle = dongle
        self.operator_name = operator_name
        # 新增：保存当前检测对象与会话，用于子页面跳转
        self.current_user: User | None = None
        self.current_assessment: AssessmentSession | None = None
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

        # 修改：将"脊柱评估"和"关节活动度"替换为真实页面，其余模块保持占位页
        self.spine_page = SpineAssessmentPage()
        self.page_indexes["脊柱评估"] = self.pages.addWidget(self.spine_page)

        # 新增：注册脊柱评估实时扫描子页面（不在顶部导航栏显示）
        self.spine_scan_page = SpineScanPage()
        self.page_indexes["脊柱扫描"] = self.pages.addWidget(self.spine_scan_page)

        self.joint_page = JointMobilityPage()
        self.page_indexes["关节活动度"] = self.pages.addWidget(self.joint_page)

        for module in self.MODULES[1:]:
            if module in ("脊柱评估", "关节活动度"):
                continue  # 已单独注册真实页面
            self.page_indexes[module] = self.pages.addWidget(PlaceholderPage(module))

        # 新增：连接脊柱评估首页的扫描信号到主窗口处理槽
        self.spine_page.start_standing_scan.connect(
            lambda: self._on_start_spine_scan("站位", 1)
        )
        self.spine_page.start_sitting_scan.connect(
            lambda: self._on_start_spine_scan("坐位", 2)
        )
        self.spine_page.start_adams_scan.connect(
            lambda: self._on_start_spine_scan("Adams", 3)
        )
        # 新增：扫描页取消/完成信号回退到脊柱评估首页
        self.spine_scan_page.scan_cancelled.connect(
            lambda: self.show_module("脊柱评估")
        )
        self.spine_scan_page.scan_finished.connect(
            lambda: self.show_module("脊柱评估")
        )
        root.addWidget(self.pages, 1)
        self.setCentralWidget(central)
        self.nav_buttons["用户信息"].setChecked(True)
        self.show_module("用户信息")

    def show_module(self, module: str) -> None:
        self.pages.setCurrentIndex(self.page_indexes[module])
        # 修改：子页面不在顶部导航栏中，需映射回所属主模块高亮
        nav_module = module
        if module == "脊柱扫描":
            nav_module = "脊柱评估"
        if nav_module in self.nav_buttons:
            self.nav_buttons[nav_module].setChecked(True)

    def _start_detection(self, user: User, assessment: AssessmentSession) -> None:
        # 修改：保存当前上下文，供子页面跳转使用
        self.current_user = user
        self.current_assessment = assessment
        # 修改：同时向体态检测、脊柱评估和关节活动度页面传递检测上下文
        posture_page = self.pages.widget(self.page_indexes["体态检测"])
        if isinstance(posture_page, PlaceholderPage):
            posture_page.set_detection_context(user, assessment)
        # 新增：注入脊柱评估页面的上下文
        self.spine_page.set_detection_context(user, assessment)
        # 新增：注入关节活动度页面的上下文
        self.joint_page.set_detection_context(user, assessment)
        self.show_module("体态检测")

    # 新增：从脊柱评估首页进入实时扫描页
    def _on_start_spine_scan(self, scan_type: str, task_index: int) -> None:
        if self.current_user is None or self.current_assessment is None:
            return
        self.spine_scan_page.start_scan(
            self.current_user, self.current_assessment, scan_type, task_index, 3
        )
        self.show_module("脊柱扫描")

    def _open_settings(self) -> None:
        SettingsDialog(self.settings, self.dongle, self).exec()
