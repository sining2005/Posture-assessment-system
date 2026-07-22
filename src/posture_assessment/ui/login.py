from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from posture_assessment.dongle import DongleAdapter
from posture_assessment.services import AuthService
from posture_assessment.ui.dialogs import show_error
from posture_assessment.ui.theme import PINK, set_primary


class LoginBackground(QWidget):
    REFERENCE_WIDTH = 1920
    REFERENCE_HEIGHT = 1080
    PINK_SOURCE_HEIGHT = 780
    FOOTER_HEIGHT = 82

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        project_root = Path(__file__).resolve().parents[3]
        self._background = QPixmap(str(project_root / "resources" / "login_background.png"))

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        if self._background.isNull():
            painter.fillRect(self.rect(), QColor("#e9576a"))
            return
        pink_height = max(1, self.height() - self.FOOTER_HEIGHT)
        painter.drawPixmap(
            QRect(0, 0, self.width(), pink_height),
            self._background,
            QRect(0, 0, self.REFERENCE_WIDTH, self.PINK_SOURCE_HEIGHT),
        )
        painter.fillRect(
            QRect(0, pink_height, self.width(), self.FOOTER_HEIGHT), QColor("#ffffff")
        )
        painter.setPen(QColor("#c7c7c7"))
        footer_font = painter.font()
        footer_font.setPointSize(11)
        painter.setFont(footer_font)
        painter.drawText(
            QRect(0, pink_height, self.width(), self.FOOTER_HEIGHT),
            Qt.AlignmentFlag.AlignCenter,
            "发布版本 1　完整版本 1.1.0.0　北京鸿泰盛医疗设备有限公司",
        )


class DongleDialog(QDialog):
    def __init__(self, dongle: DongleAdapter, parent: QWidget | None = None):
        super().__init__(parent)
        self.dongle = dongle
        self.setWindowTitle("温馨提示")
        self.setModal(True)
        self.setFixedSize(330, 210)
        root = QVBoxLayout(self)
        title = QLabel("温馨提示")
        title.setObjectName("sectionTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(title)
        self.message = QLabel()
        self.message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.message, 1)
        self.retry = QPushButton("重新检测")
        set_primary(self.retry)
        self.retry.clicked.connect(self._check)
        root.addWidget(self.retry, alignment=Qt.AlignmentFlag.AlignCenter)
        self._check()

    def _check(self) -> None:
        status = self.dongle.check()
        self.message.setText(status.message)
        if status.available:
            self.retry.setText("确认")
            self.retry.clicked.disconnect()
            self.retry.clicked.connect(self.accept)


class LoginWindow(LoginBackground):
    login_success = Signal(str)

    def __init__(
        self,
        auth_service: AuthService,
        dongle: DongleAdapter,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.auth_service = auth_service
        self.dongle = dongle
        self._checked_on_show = False
        self.setWindowTitle("体态评估系统 - 登录")
        self.setMinimumSize(1100, 680)
        self._build_ui()

    def _build_ui(self) -> None:
        self.card = QFrame(self)
        self.card.setObjectName("loginCard")
        self.card.setStyleSheet("QFrame#loginCard { background:white; border-radius:14px; }")
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(44, 38, 44, 52)
        title = QLabel("— 系统登录 —")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size:22px; color:#8a8e90; padding:12px;")
        card_layout.addWidget(title)
        card_layout.addSpacing(44)
        self.username = QLineEdit()
        self.username.setPlaceholderText("请输入账号")
        self.username.setText("admin")
        self.username.setMinimumHeight(52)
        card_layout.addWidget(self.username)
        card_layout.addSpacing(14)
        password_row = QHBoxLayout()
        self.password = QLineEdit()
        self.password.setPlaceholderText("请输入密码")
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setMinimumHeight(52)
        self.password.returnPressed.connect(self._login)
        password_row.addWidget(self.password, 1)
        self.password_toggle = QPushButton("显示")
        self.password_toggle.setObjectName("passwordToggle")
        self.password_toggle.setFixedSize(76, 52)
        self.password_toggle.setStyleSheet(
            "QPushButton#passwordToggle { padding: 0; margin: 0; min-width: 76px; max-width: 76px; }"
        )
        self.password_toggle.setToolTip("显示/隐藏密码")
        self.password_toggle.clicked.connect(self._toggle_password)
        password_row.addWidget(self.password_toggle)
        card_layout.addLayout(password_row)
        card_layout.addStretch()
        login = QPushButton("登 录")
        login.setMinimumHeight(56)
        set_primary(login)
        login.clicked.connect(self._login)
        card_layout.addWidget(login)
        self._position_card()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._position_card()

    def _position_card(self) -> None:
        if not hasattr(self, "card"):
            return
        pink_height = max(1, self.height() - self.FOOTER_HEIGHT)
        x = round(self.width() * 1108 / self.REFERENCE_WIDTH)
        y = round(pink_height * 192 / self.PINK_SOURCE_HEIGHT)
        width = round(self.width() * 442 / self.REFERENCE_WIDTH)
        height = round(pink_height * 480 / self.PINK_SOURCE_HEIGHT)
        self.card.setGeometry(x, y, width, height)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._checked_on_show:
            self._checked_on_show = True
            QTimer.singleShot(100, self._initial_dongle_check)

    def _initial_dongle_check(self) -> None:
        if not self.dongle.check().available:
            DongleDialog(self.dongle, self).exec()

    def _toggle_password(self) -> None:
        show = self.password.echoMode() == QLineEdit.EchoMode.Password
        self.password.setEchoMode(QLineEdit.EchoMode.Normal if show else QLineEdit.EchoMode.Password)
        self.password_toggle.setText("隐藏" if show else "显示")

    def _login(self) -> None:
        status = self.dongle.check()
        if not status.available:
            DongleDialog(self.dongle, self).exec()
            if not self.dongle.check().available:
                return
        if not self.auth_service.authenticate(self.username.text(), self.password.text()):
            show_error(self, "温馨提示", "账号或密码错误")
            self.password.selectAll()
            self.password.setFocus()
            return
        self.login_success.emit(self.username.text().strip())
