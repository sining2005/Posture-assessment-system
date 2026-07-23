from __future__ import annotations

from datetime import date

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from posture_assessment.config import AppSettings
from posture_assessment.dongle import DongleAdapter
from posture_assessment.models import User
from posture_assessment.services import UserInput, UserService
from posture_assessment.ui.theme import set_primary


def show_error(parent: QWidget, title: str, message: str) -> None:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle(title)
    box.setText(message)
    box.setStandardButtons(QMessageBox.StandardButton.Ok)
    box.exec()


def show_info(parent: QWidget, title: str, message: str) -> None:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Information)
    box.setWindowTitle(title)
    box.setText(message)
    box.setStandardButtons(QMessageBox.StandardButton.Ok)
    box.exec()


def confirm(parent: QWidget, title: str, message: str) -> bool:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle(title)
    box.setText(message)
    box.setStandardButtons(
        QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Ok
    )
    box.setDefaultButton(QMessageBox.StandardButton.Cancel)
    box.button(QMessageBox.StandardButton.Ok).setText("确认")
    box.button(QMessageBox.StandardButton.Cancel).setText("取消")
    return box.exec() == QMessageBox.StandardButton.Ok


class UserFormDialog(QDialog):
    def __init__(
        self,
        service: UserService,
        user: User | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.service = service
        self.user = user
        self.saved_user: User | None = None
        self.setWindowTitle("修改患者信息" if user else "新建患者")
        self.setMinimumWidth(820)
        self._build_ui()
        if user:
            self._load_user(user)
        else:
            self.patient_no.setText(service.generate_patient_no())
        self._update_age()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 20)
        grid = QGridLayout()
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(12)
        root.addLayout(grid)

        self.patient_no = QLineEdit()
        self.name = QLineEdit()
        self.sex = QComboBox()
        self.sex.addItems(["男", "女", "未说明"])
        self.birth_date = QDateEdit(calendarPopup=True)
        self.birth_date.setDisplayFormat("yyyy/MM/dd")
        self.birth_date.setMaximumDate(QDate.currentDate())
        self.birth_date.setDate(QDate(1990, 1, 1))
        self.birth_date.dateChanged.connect(self._update_age)
        self.age = QLineEdit()
        self.age.setReadOnly(True)
        self.mobile = QLineEdit()
        self.height = QDoubleSpinBox()
        self.height.setRange(0, 250)
        self.height.setDecimals(1)
        self.height.setSuffix(" cm")
        self.height.setSpecialValueText("未填写")
        self.weight = QDoubleSpinBox()
        self.weight.setRange(0, 300)
        self.weight.setDecimals(1)
        self.weight.setSuffix(" kg")
        self.weight.setSpecialValueText("未填写")
        self.address = QLineEdit()
        self.email = QLineEdit()
        self.notes = QLineEdit()

        fields = [
            ("患者编号 *", self.patient_no, "姓名 *", self.name),
            ("性别 *", self.sex, "出生日期 *", self.birth_date),
            ("年龄", self.age, "手机号码 *", self.mobile),
            ("身高 *", self.height, "体重 *", self.weight),
            ("地址 *", self.address, "", None),
            ("邮箱", self.email, "备注", self.notes),
        ]
        for row, (left_label, left, right_label, right) in enumerate(fields):
            grid.addWidget(QLabel(left_label), row, 0)
            grid.addWidget(left, row, 1)
            if right is not None:
                grid.addWidget(QLabel(right_label), row, 2)
                grid.addWidget(right, row, 3)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        grid.addWidget(self.address, 4, 1, 1, 3)

        hint = QLabel("患者编号必须唯一；邮箱和备注为选填项。年龄由出生日期自动计算。")
        hint.setObjectName("muted")
        root.addWidget(hint)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save
        )
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        save_button.setText("保存")
        set_primary(save_button)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self._save)
        root.addWidget(buttons)

    def _load_user(self, user: User) -> None:
        self.patient_no.setText(user.patient_no)
        self.name.setText(user.name)
        self.sex.setCurrentText(user.sex)
        self.birth_date.setDate(
            QDate(user.birth_date.year, user.birth_date.month, user.birth_date.day)
        )
        self.mobile.setText(user.mobile or "")
        self.height.setValue(user.height_cm or 0)
        self.weight.setValue(user.weight_kg or 0)
        self.address.setText(user.address or "")
        self.email.setText(user.email or "")
        self.notes.setText(user.notes or "")

    def _update_age(self) -> None:
        selected = self.birth_date.date().toPython()
        today = date.today()
        age = today.year - selected.year - (
            (today.month, today.day) < (selected.month, selected.day)
        )
        self.age.setText(str(max(0, age)))

    def _as_input(self) -> UserInput:
        return UserInput(
            patient_no=self.patient_no.text(),
            name=self.name.text(),
            sex=self.sex.currentText(),
            birth_date=self.birth_date.date().toPython(),
            mobile=self.mobile.text(),
            height_cm=self.height.value() or None,
            weight_kg=self.weight.value() or None,
            address=self.address.text(),
            email=self.email.text(),
            notes=self.notes.text(),
        )

    def _save(self) -> None:
        try:
            if self.user:
                self.saved_user = self.service.update(self.user.id, self._as_input())
            else:
                self.saved_user = self.service.create(self._as_input())
        except Exception as exc:
            show_error(self, "保存失败", str(exc))
            return
        self.accept()


class UserDetailsDialog(QDialog):
    def __init__(self, service: UserService, user: User, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("患者详情与检测记录")
        self.setMinimumSize(800, 500)
        root = QVBoxLayout(self)
        info = QFrame()
        info.setObjectName("card")
        form = QGridLayout(info)
        values = [
            ("编号", user.patient_no, "姓名", user.name),
            ("性别", user.sex, "出生日期", f"{user.birth_date:%Y/%m/%d} ({user.age}岁)"),
            (
                "身高/体重",
                f"{user.height_cm or '-'} cm / {user.weight_kg or '-'} kg",
                "联系方式",
                user.mobile or "-",
            ),
            ("地址", user.address or "-", "档案状态", "完整" if user.profile_complete else "待补全"),
        ]
        for row, item in enumerate(values):
            for col, text in enumerate(item):
                label = QLabel(str(text))
                if col % 2 == 0:
                    label.setStyleSheet("font-weight: 700; background:#f2f2f2; padding:8px;")
                form.addWidget(label, row, col)
        root.addWidget(info)
        title = QLabel("历史检测")
        title.setObjectName("sectionTitle")
        root.addWidget(title)
        table = QTableWidget(0, 4)
        table.setHorizontalHeaderLabels(["时间", "检测模块", "状态", "报告"])
        table.horizontalHeader().setStretchLastSection(True)
        assessments = service.list_assessments(user.id)
        table.setRowCount(len(assessments))
        for row, item in enumerate(assessments):
            values = [
                item.started_at.strftime("%Y/%m/%d %H:%M"),
                item.assessment_type,
                item.status,
                "查看" if item.report_path else "未生成",
            ]
            for col, value in enumerate(values):
                table.setItem(row, col, QTableWidgetItem(value))
        root.addWidget(table, 1)
        close = QPushButton("关闭")
        set_primary(close)
        close.clicked.connect(self.accept)
        footer = QHBoxLayout()
        footer.addStretch()
        footer.addWidget(close)
        root.addLayout(footer)


class SettingsDialog(QDialog):
    def __init__(
        self,
        settings: AppSettings,
        dongle: DongleAdapter,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("系统设置")
        self.setMinimumWidth(620)
        root = QVBoxLayout(self)
        form = QFormLayout()
        form.addRow("数据目录", QLabel(str(settings.data_dir)))
        form.addRow("数据库", QLabel(str(settings.database_path)))
        form.addRow("导出目录", QLabel(str(settings.export_dir)))
        self.dongle_status = QLabel()
        form.addRow("加密锁", self.dongle_status)
        root.addLayout(form)
        check = QPushButton("重新检测加密锁")
        check.clicked.connect(lambda: self._refresh_dongle(dongle))
        root.addWidget(check)
        close = QPushButton("关闭")
        set_primary(close)
        close.clicked.connect(self.accept)
        root.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)
        self._refresh_dongle(dongle)

    def _refresh_dongle(self, dongle: DongleAdapter) -> None:
        status = dongle.check()
        self.dongle_status.setText(status.message)
        self.dongle_status.setStyleSheet(
            "color:#2e9d5b; font-weight:700;" if status.available else "color:#d14949; font-weight:700;"
        )
