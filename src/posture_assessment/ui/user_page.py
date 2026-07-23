from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from posture_assessment.import_export import UserExportService, UserImportService
from posture_assessment.models import User
from posture_assessment.services import UserService
from posture_assessment.ui.dialogs import (
    UserDetailsDialog,
    UserFormDialog,
    confirm,
    show_error,
    show_info,
)
from posture_assessment.ui.export_dialog import ExportDialog
from posture_assessment.ui.import_dialog import BatchImportDialog
from posture_assessment.ui.theme import set_primary


class UserPage(QWidget):
    start_detection = Signal(object, object)

    def __init__(
        self,
        user_service: UserService,
        import_service: UserImportService,
        export_service: UserExportService,
        export_dir,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.user_service = user_service
        self.import_service = import_service
        self.export_service = export_service
        self.export_dir = export_dir
        self.current_page = 1
        self.page_size = 20
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(34, 34, 34, 24)
        root.setSpacing(18)
        filters = QHBoxLayout()
        self.patient_no = QLineEdit()
        self.patient_no.setPlaceholderText("患者编号")
        self.patient_no.setMaximumWidth(180)
        self.name = QLineEdit()
        self.name.setPlaceholderText("姓名")
        self.name.setMaximumWidth(180)
        self.mobile = QLineEdit()
        self.mobile.setPlaceholderText("手机")
        self.mobile.setMaximumWidth(180)
        for label, widget in (
            ("编号", self.patient_no),
            ("姓名", self.name),
            ("手机", self.mobile),
        ):
            filters.addWidget(QLabel(label))
            filters.addWidget(widget)
        search = QPushButton("查询")
        set_primary(search)
        search.clicked.connect(self._search)
        filters.addWidget(search)
        clear = QPushButton("清空")
        set_primary(clear)
        clear.clicked.connect(self._clear)
        filters.addWidget(clear)
        register = QPushButton("注册")
        set_primary(register)
        register.clicked.connect(self._create_user)
        filters.addWidget(register)
        import_button = QPushButton("批量导入")
        import_button.clicked.connect(self._open_import)
        filters.addWidget(import_button)
        export_button = QPushButton("导出检测数据")
        export_button.clicked.connect(self._open_export)
        filters.addWidget(export_button)
        filters.addStretch()
        root.addLayout(filters)

        self.table = QTableWidget(0, 12)
        self.table.setHorizontalHeaderLabels(
            [
                "",
                "编号",
                "姓名",
                "出生日期",
                "性别",
                "电话",
                "最近检测时间",
                "地址",
                "开始检测",
                "查看",
                "修改",
                "删除",
            ]
        )
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(7, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 32)
        root.addWidget(self.table, 1)

        footer = QHBoxLayout()
        self.total_label = QLabel()
        self.total_label.setObjectName("muted")
        footer.addWidget(self.total_label)
        footer.addStretch()
        self.prev_button = QPushButton("上一页")
        self.prev_button.clicked.connect(self._previous_page)
        footer.addWidget(self.prev_button)
        self.page_label = QLabel()
        footer.addWidget(self.page_label)
        self.next_button = QPushButton("下一页")
        self.next_button.clicked.connect(self._next_page)
        footer.addWidget(self.next_button)
        root.addLayout(footer)

    def refresh(self) -> None:
        result = self.user_service.search(
            patient_no=self.patient_no.text(),
            name=self.name.text(),
            mobile=self.mobile.text(),
            page=self.current_page,
            page_size=self.page_size,
        )
        if self.current_page > result.pages:
            self.current_page = result.pages
            return self.refresh()
        self.table.setRowCount(len(result.items))
        for row, user in enumerate(result.items):
            latest = self.user_service.list_assessments(user.id)
            latest_time = latest[0].started_at.strftime("%Y/%m/%d %H:%M:%S") if latest else "-"
            values = [
                ">",
                user.patient_no,
                user.name,
                user.birth_date.strftime("%Y/%m/%d"),
                user.sex,
                user.mobile or "-",
                latest_time,
                user.address or "待补全",
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if not user.profile_complete:
                    item.setToolTip("档案不完整，开始检测前需要补齐")
                self.table.setItem(row, col, item)
            self.table.setCellWidget(row, 8, self._action_button("开始", "开始检测", lambda _, u=user: self._start_user(u)))
            self.table.setCellWidget(row, 9, self._action_button("查看", "查看详情", lambda _, u=user: self._view_user(u)))
            self.table.setCellWidget(row, 10, self._action_button("修改", "修改资料", lambda _, u=user: self._edit_user(u)))
            self.table.setCellWidget(row, 11, self._action_button("删除", "归档患者", lambda _, u=user: self._archive_user(u)))
            self.table.setRowHeight(row, 48)
        self.total_label.setText(f"共 {result.total} 条记录")
        self.page_label.setText(f"第 {result.page} / {result.pages} 页")
        self.prev_button.setEnabled(result.page > 1)
        self.next_button.setEnabled(result.page < result.pages)

    @staticmethod
    def _action_button(text: str, tooltip: str, callback) -> QToolButton:
        button = QToolButton()
        button.setText(text)
        button.setToolTip(tooltip)
        button.setStyleSheet("font-size:13px;")
        button.clicked.connect(callback)
        return button

    def _search(self) -> None:
        self.current_page = 1
        self.refresh()

    def _clear(self) -> None:
        for widget in (self.patient_no, self.name, self.mobile):
            widget.clear()
        self.current_page = 1
        self.refresh()

    def _previous_page(self) -> None:
        self.current_page = max(1, self.current_page - 1)
        self.refresh()

    def _next_page(self) -> None:
        self.current_page += 1
        self.refresh()

    def _create_user(self) -> None:
        dialog = UserFormDialog(self.user_service, parent=self)
        if dialog.exec():
            self.current_page = 1
            self.refresh()
            show_info(self, "保存成功", "患者档案已创建。")

    def _edit_user(self, user: User) -> None:
        dialog = UserFormDialog(self.user_service, user=user, parent=self)
        if dialog.exec():
            self.refresh()

    def _view_user(self, user: User) -> None:
        UserDetailsDialog(self.user_service, user, self).exec()

    def _archive_user(self, user: User) -> None:
        if not confirm(
            self,
            "确认删除患者？",
            f"将归档患者“{user.name}”的档案。已生成报告不会自动删除。",
        ):
            return
        try:
            self.user_service.archive(user.id)
        except Exception as exc:
            show_error(self, "删除失败", str(exc))
            return
        self.refresh()

    def _start_user(self, user: User) -> None:
        if not user.profile_complete:
            show_error(
                self,
                "档案不完整",
                "开始检测前请先修改患者资料，补齐手机号码、身高、体重和地址。",
            )
            return
        if not confirm(
            self,
            "开始检测",
            f"检测对象：{user.name}（{user.patient_no}）\n\n本软件一次只能检测一名患者，请确认信息无误。",
        ):
            return
        try:
            assessment = self.user_service.start_assessment(user.id)
        except Exception as exc:
            show_error(self, "无法开始检测", str(exc))
            return
        self.refresh()
        self.start_detection.emit(user, assessment)

    def _open_import(self) -> None:
        dialog = BatchImportDialog(self.import_service, self)
        dialog.imported.connect(self.refresh)
        dialog.exec()
        self.refresh()

    def _open_export(self) -> None:
        ExportDialog(
            self.user_service,
            self.export_service,
            self.export_dir,
            self,
        ).exec()
