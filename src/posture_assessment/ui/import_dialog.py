from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtCore import Qt, QThreadPool, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from posture_assessment.import_export import (
    CANONICAL_FIELDS,
    ImportPreview,
    ImportResult,
    UserImportService,
)
from posture_assessment.ui.dialogs import show_error, show_info
from posture_assessment.ui.theme import set_primary
from posture_assessment.ui.workers import TaskWorker


class BatchImportDialog(QDialog):
    imported = Signal()

    def __init__(self, service: UserImportService, parent: QWidget | None = None):
        super().__init__(parent)
        self.service = service
        self.source_path: Path | None = None
        self.preview_data: ImportPreview | None = None
        self.mapping_combos: dict[str, QComboBox] = {}
        self.result_data: ImportResult | None = None
        self._worker: TaskWorker | None = None
        self.setWindowTitle("批量导入用户注册数据")
        self.resize(1180, 760)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        title = QLabel("批量导入用户注册数据")
        title.setObjectName("pageTitle")
        root.addWidget(title)
        subtitle = QLabel("使用 XLSX 或 CSV 模板一次建立多个用户档案。")
        subtitle.setObjectName("muted")
        root.addWidget(subtitle)
        self.steps = QLabel("● 1 选择文件   ─────   ○ 2 校验与映射   ─────   ○ 3 导入结果")
        self.steps.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.steps.setStyleSheet("font-size:16px; font-weight:700; color:#d9475a; padding:16px;")
        root.addWidget(self.steps)
        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_choose_page())
        self.pages.addWidget(self._build_mapping_page())
        self.pages.addWidget(self._build_result_page())
        root.addWidget(self.pages, 1)

    def _build_choose_page(self) -> QWidget:
        page = QWidget()
        layout = QHBoxLayout(page)
        drop = QFrame()
        drop.setObjectName("card")
        drop.setStyleSheet("QFrame#card { border:2px dashed #b9bec1; }")
        drop_layout = QVBoxLayout(drop)
        drop_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon = QLabel("导入")
        icon.setStyleSheet("font-size:54px; color:#d9475a;")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        drop_layout.addWidget(icon)
        label = QLabel("选择导入文件")
        label.setObjectName("sectionTitle")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        drop_layout.addWidget(label)
        note = QLabel("支持 .xlsx / .csv，单次最多 5,000 条，文件不超过 20 MB。")
        note.setObjectName("muted")
        drop_layout.addWidget(note)
        choose = QPushButton("选择文件")
        set_primary(choose)
        choose.clicked.connect(self._choose_file)
        drop_layout.addWidget(choose, alignment=Qt.AlignmentFlag.AlignCenter)
        self.file_label = QLabel("尚未选择文件")
        self.file_label.setObjectName("muted")
        drop_layout.addWidget(self.file_label, alignment=Qt.AlignmentFlag.AlignCenter)
        template = QPushButton("下载标准导入模板.xlsx")
        template.clicked.connect(self._download_template)
        drop_layout.addWidget(template, alignment=Qt.AlignmentFlag.AlignCenter)

        instructions = QFrame()
        instructions.setObjectName("card")
        inst_layout = QVBoxLayout(instructions)
        heading = QLabel("模板字段说明")
        heading.setObjectName("sectionTitle")
        inst_layout.addWidget(heading)
        table = QTableWidget(5, 3)
        table.setHorizontalHeaderLabels(["字段", "必填", "格式 / 示例"])
        rows = [
            ("用户编号", "否", "留空自动生成"),
            ("姓名", "是", "测试用户"),
            ("出生日期", "是", "YYYY-MM-DD"),
            ("性别", "是", "男 / 女 / 未说明"),
            ("手机、地址、身高、体重", "否", "开始检测前补齐"),
        ]
        for row, values in enumerate(rows):
            for col, value in enumerate(values):
                table.setItem(row, col, QTableWidgetItem(value))
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        inst_layout.addWidget(table)
        warning = QLabel("导入前请确认：日期格式正确；不导入 BMI 或任何检测结论。")
        warning.setStyleSheet("background:#fff7e8; border-left:3px solid #e0a02b; padding:14px;")
        inst_layout.addWidget(warning)
        self.validate_button = QPushButton("上传并开始校验")
        set_primary(self.validate_button)
        self.validate_button.setEnabled(False)
        self.validate_button.clicked.connect(self._load_preview)
        inst_layout.addStretch()
        inst_layout.addWidget(self.validate_button, alignment=Qt.AlignmentFlag.AlignRight)
        layout.addWidget(drop, 3)
        layout.addWidget(instructions, 2)
        return page

    def _build_mapping_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        top = QHBoxLayout()
        self.mapping_summary = QLabel()
        self.mapping_summary.setObjectName("sectionTitle")
        top.addWidget(self.mapping_summary)
        top.addStretch()
        change = QPushButton("重新选择文件")
        change.clicked.connect(lambda: self.pages.setCurrentIndex(0))
        top.addWidget(change)
        layout.addLayout(top)
        body = QHBoxLayout()
        self.mapping_table = QTableWidget(0, 3)
        self.mapping_table.setHorizontalHeaderLabels(["模板列", "映射到系统字段", "校验"])
        self.mapping_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        body.addWidget(self.mapping_table, 3)
        strategy = QFrame()
        strategy.setObjectName("card")
        strategy_layout = QVBoxLayout(strategy)
        strategy_layout.addWidget(QLabel("重复用户处理"))
        self.strategy_group = QButtonGroup(self)
        choices = [
            ("skip", "跳过重复记录", "保留系统现有档案"),
            ("fill_blank", "仅补全空白字段", "不覆盖已有数据"),
            ("overwrite", "覆盖基本信息", "不修改历史检测数据"),
        ]
        for index, (key, label, hint) in enumerate(choices):
            radio = QRadioButton(label)
            radio.setProperty("strategy", key)
            self.strategy_group.addButton(radio)
            strategy_layout.addWidget(radio)
            note = QLabel(hint)
            note.setObjectName("muted")
            note.setContentsMargins(24, 0, 0, 8)
            strategy_layout.addWidget(note)
            if index == 0:
                radio.setChecked(True)
        strategy_layout.addStretch()
        body.addWidget(strategy, 2)
        layout.addLayout(body, 1)
        self.preview_table = QTableWidget()
        self.preview_table.setMaximumHeight(190)
        layout.addWidget(self.preview_table)
        footer = QHBoxLayout()
        self.import_progress = QProgressBar()
        self.import_progress.setVisible(False)
        footer.addWidget(self.import_progress, 1)
        footer.addStretch()
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        footer.addWidget(cancel)
        self.import_button = QPushButton("导入有效记录")
        set_primary(self.import_button)
        self.import_button.clicked.connect(self._start_import)
        footer.addWidget(self.import_button)
        layout.addLayout(footer)
        return page

    def _build_result_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card = QFrame()
        card.setObjectName("card")
        card.setMaximumWidth(760)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(48, 36, 48, 36)
        check = QLabel("成功")
        check.setAlignment(Qt.AlignmentFlag.AlignCenter)
        check.setStyleSheet("font-size:64px; color:#45a069; font-weight:700;")
        card_layout.addWidget(check)
        title = QLabel("用户数据导入完成")
        title.setObjectName("pageTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(title)
        self.result_summary = QLabel()
        self.result_summary.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.result_summary.setStyleSheet("font-size:18px; padding:20px;")
        card_layout.addWidget(self.result_summary)
        self.batch_info = QLabel()
        self.batch_info.setStyleSheet("background:#f5f5f5; padding:14px;")
        card_layout.addWidget(self.batch_info)
        buttons = QHBoxLayout()
        self.download_errors = QPushButton("下载错误明细.csv")
        self.download_errors.clicked.connect(self._save_error_log)
        buttons.addWidget(self.download_errors)
        done = QPushButton("返回用户列表")
        set_primary(done)
        done.clicked.connect(self.accept)
        buttons.addWidget(done)
        card_layout.addLayout(buttons)
        layout.addWidget(card)
        return page

    def _choose_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择导入文件", "", "用户数据 (*.xlsx *.csv)"
        )
        if path:
            self.source_path = Path(path)
            self.file_label.setText(self.source_path.name)
            self.validate_button.setEnabled(True)

    def _download_template(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "保存导入模板", "用户导入模板.xlsx", "Excel (*.xlsx)"
        )
        if not path:
            return
        try:
            self.service.create_template(Path(path))
        except Exception as exc:
            show_error(self, "模板生成失败", str(exc))
            return
        show_info(self, "模板已保存", path)

    def _load_preview(self) -> None:
        if not self.source_path:
            return
        try:
            self.preview_data = self.service.preview(self.source_path)
        except Exception as exc:
            show_error(self, "校验失败", str(exc))
            return
        self._populate_mapping(self.preview_data)
        self.steps.setText("✓ 1 选择文件   ─────   ● 2 校验与映射   ─────   ○ 3 导入结果")
        self.pages.setCurrentIndex(1)

    def _populate_mapping(self, preview: ImportPreview) -> None:
        self.mapping_summary.setText(
            f"校验导入数据与字段映射 · {self.source_path.name} · {len(preview.rows)} 行"
        )
        self.mapping_table.setRowCount(len(preview.headers))
        self.mapping_combos.clear()
        for row, header in enumerate(preview.headers):
            self.mapping_table.setItem(row, 0, QTableWidgetItem(header))
            combo = QComboBox()
            combo.addItem("忽略此列", "")
            for key, label in CANONICAL_FIELDS.items():
                combo.addItem(label, key)
            detected = preview.detected_mapping.get(header, "")
            index = combo.findData(detected)
            combo.setCurrentIndex(max(0, index))
            combo.currentIndexChanged.connect(self._update_mapping_status)
            self.mapping_table.setCellWidget(row, 1, combo)
            self.mapping_combos[header] = combo
            self.mapping_table.setItem(row, 2, QTableWidgetItem("待校验"))
        self.preview_table.setRowCount(min(5, len(preview.rows)))
        self.preview_table.setColumnCount(len(preview.headers))
        self.preview_table.setHorizontalHeaderLabels(preview.headers)
        for row, values in enumerate(preview.rows[:5]):
            for col, header in enumerate(preview.headers):
                self.preview_table.setItem(row, col, QTableWidgetItem(str(values.get(header) or "")))
        self._update_mapping_status()

    def _update_mapping_status(self) -> None:
        targets = [combo.currentData() for combo in self.mapping_combos.values() if combo.currentData()]
        duplicates = {target for target in targets if targets.count(target) > 1}
        for row, (header, combo) in enumerate(self.mapping_combos.items()):
            target = combo.currentData()
            status = "忽略" if not target else ("重复映射" if target in duplicates else "通过")
            item = QTableWidgetItem(status)
            item.setForeground(Qt.GlobalColor.darkGreen if status == "通过" else Qt.GlobalColor.darkYellow)
            self.mapping_table.setItem(row, 2, item)
        required = {"name", "birth_date", "sex"}
        self.import_button.setEnabled(required.issubset(set(targets)) and not duplicates)

    def _start_import(self) -> None:
        if not self.source_path:
            return
        mapping = {header: combo.currentData() for header, combo in self.mapping_combos.items()}
        selected = self.strategy_group.checkedButton()
        strategy = selected.property("strategy") if selected else "skip"
        self.import_button.setEnabled(False)
        self.import_progress.setVisible(True)
        self.import_progress.setRange(0, 0)
        worker = TaskWorker(
            self.service.import_file,
            self.source_path,
            mapping,
            strategy,
            "admin",
        )
        worker.signals.result.connect(self._import_finished)
        worker.signals.error.connect(lambda message: show_error(self, "导入失败", message))
        worker.signals.finished.connect(self._task_finished)
        self._worker = worker
        QThreadPool.globalInstance().start(worker)

    def _task_finished(self) -> None:
        self.import_progress.setVisible(False)
        self.import_button.setEnabled(True)
        self._worker = None

    def _import_finished(self, result: ImportResult) -> None:
        self.result_data = result
        self.result_summary.setText(
            f"<b style='color:#d9475a'>{result.success_count}</b> 导入成功　"
            f"<b>{result.duplicate_count}</b> 条重复　"
            f"<b>{result.error_count}</b> 条错误"
        )
        self.batch_info.setText(
            f"批次号：{result.batch_no}<br>源文件 SHA-256：{result.source_sha256[:20]}…"
        )
        self.download_errors.setVisible(bool(result.error_log_path))
        self.steps.setText("✓ 1 选择文件   ─────   ✓ 2 校验与映射   ─────   ● 3 导入结果")
        self.pages.setCurrentIndex(2)
        self.imported.emit()

    def _save_error_log(self) -> None:
        if not self.result_data or not self.result_data.error_log_path:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "保存错误明细", self.result_data.error_log_path.name, "CSV (*.csv)"
        )
        if path:
            shutil.copy2(self.result_data.error_log_path, path)
