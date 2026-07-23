# 新增：脊柱评估 - 实时扫描/质量检查页面
from __future__ import annotations

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from posture_assessment.models import AssessmentSession, User
from posture_assessment.ui.theme import BORDER, MUTED, PINK, PINK_DARK, TEXT, set_primary


# 新增：扫描预览占位图（QPainter 绘制，后续接入真实相机帧）
class SpinePreviewWidget(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.coverage_text = "背部ROI 深度覆盖94%"
        self.setMinimumHeight(420)
        self.setStyleSheet("background: #0b1016; border-radius: 4px;")

    def paintEvent(self, event) -> None:  # noqa: ARG002
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()

        # 身体热力图占位（径向渐变椭圆）
        cx, cy = w // 2, h // 2 - 20
        gradient = QRadialGradient(cx, cy, 200)
        gradient.setColorAt(0.0, QColor("#6e8fa3"))
        gradient.setColorAt(0.5, QColor("#3a5a6e"))
        gradient.setColorAt(0.8, QColor("#d48a4a"))
        gradient.setColorAt(1.0, QColor("#1f3340"))
        painter.setBrush(gradient)
        painter.setPen(QPen(QColor("transparent")))
        painter.drawEllipse(cx - 120, cy - 220, 240, 440)

        # 脊柱基准线（红色）
        painter.setPen(QPen(QColor(PINK_DARK), 3))
        top_y = cy - 160
        bottom_y = cy + 160
        painter.drawLine(cx, top_y, cx, bottom_y)

        # 脊柱椎体标记点
        painter.setBrush(QColor(PINK))
        for i in range(8):
            y = top_y + i * 45
            painter.drawEllipse(cx - 5, y - 5, 10, 10)

        # 横向切片参考线
        painter.setPen(QPen(QColor(150, 180, 200, 120), 1))
        for i in range(7):
            y = top_y + 20 + i * 45
            painter.drawLine(cx - 90, y, cx + 90, y)

        # 右下横截面轮廓
        painter.setPen(QPen(QColor(120, 220, 255), 2))
        painter.setBrush(QColor(30, 60, 80, 120))
        cross_rect = QRect(w - 180, h - 150, 150, 100)
        painter.drawRoundedRect(cross_rect, 40, 40)
        painter.setPen(QPen(QColor(255, 120, 80), 2))
        painter.drawArc(cross_rect, 0, 180 * 16)

        # 左上角覆盖标签
        painter.setPen(QPen(QColor("white"), 1))
        painter.setBrush(QColor(40, 40, 40, 180))
        label_rect = QRect(16, 16, 160, 28)
        painter.drawRect(label_rect)
        painter.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, self.coverage_text)


class SpineScanPage(QWidget):
    """新增：脊柱评估实时扫描与质量检查页。"""

    # 新增：扫描流程控制信号
    scan_finished = Signal()
    scan_cancelled = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.current_user: User | None = None
        self.current_assessment: AssessmentSession | None = None
        self.scan_type: str = "站位"
        self.task_index: int = 1
        self.total_tasks: int = 3
        self._build_ui()
        self._refresh_display()

    # 新增：构建页面主布局
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(34, 24, 34, 24)
        root.setSpacing(20)

        # 新增：顶部信息栏（与脊柱评估首页一致）
        header = QHBoxLayout()
        header.setSpacing(18)
        self.info_label = QLabel()
        self.info_label.setStyleSheet(f"color: {TEXT}; font-size: 15px;")
        self.progress_label = QLabel("1 / 3")
        self.progress_label.setStyleSheet(
            f"color: {MUTED}; font-size: 15px; font-weight: 600;"
        )
        header.addWidget(self.info_label)
        header.addStretch()
        header.addWidget(self.progress_label)
        root.addLayout(header)

        # 新增：标题行
        title_layout = QHBoxLayout()
        self.title_label = QLabel()
        self.title_label.setStyleSheet("font-size: 18px; font-weight: 700;")
        self.task_label = QLabel("任务 1 / 3")
        self.task_label.setStyleSheet(f"color: {MUTED}; font-size: 14px;")
        title_layout.addWidget(self.title_label)
        title_layout.addSpacing(12)
        title_layout.addWidget(self.task_label)
        title_layout.addStretch()
        root.addLayout(title_layout)

        # 新增：中间主体区（左侧扫描预览 + 右侧质量检查）
        content = QHBoxLayout()
        content.setSpacing(24)
        content.addWidget(self._build_preview_panel(), 3)
        content.addWidget(self._build_quality_panel(), 1)
        root.addLayout(content, 1)

    # 新增：左侧扫描预览面板
    def _build_preview_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("card")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        self.preview = SpinePreviewWidget()
        layout.addWidget(self.preview, 1)
        return panel

    # 新增：右侧实时质量检查面板
    def _build_quality_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("card")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(18)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        title = QLabel("实时质量检查")
        title.setStyleSheet("font-size: 17px; font-weight: 700;")
        layout.addWidget(title)

        # 新增：质量检查项
        self.quality_items: list[tuple[str, str, str]] = [
            ("身体朝向", "1.8° · 通过", "green"),
            ("背部有效深度", "94% · 通过", "green"),
            ("稳定窗", "1.7 / 2.0s", "green"),
            ("局部对称线一致性", "计算中", "orange"),
            ("多人/遮挡", "未发现", "green"),
        ]
        for label, value, color in self.quality_items:
            layout.addLayout(self._create_quality_row(label, value, color))

        # 新增：底部说明
        note = QLabel(
            "颈胸、胸腰、腰骶屈位均为自动体表屈位代理，不要求人工贴点或点选。"
        )
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {MUTED}; font-size: 12px; line-height: 1.5;")
        layout.addSpacing(10)
        layout.addWidget(note)
        layout.addStretch()

        # 新增：动作指引
        guide_layout = QHBoxLayout()
        guide_layout.setSpacing(8)
        guide_number = QLabel("2")
        guide_number.setStyleSheet(
            f"color: {PINK}; font-size: 42px; font-weight: 700;"
        )
        guide_text = QLabel("保持自然站立")
        guide_text.setStyleSheet(f"color: {TEXT}; font-size: 16px;")
        guide_layout.addWidget(guide_number)
        guide_layout.addWidget(guide_text)
        guide_layout.addStretch()
        layout.addLayout(guide_layout)

        # 新增：底部按钮
        buttons = QHBoxLayout()
        buttons.setSpacing(12)
        self.cancel_button = QPushButton("取消")
        self.cancel_button.setFixedHeight(38)
        self.cancel_button.setStyleSheet(
            f"""
            QPushButton {{
                background: white;
                color: {TEXT};
                border: 1px solid {BORDER};
                border-radius: 4px;
                padding: 0 20px;
            }}
            QPushButton:hover {{ background: #f5f5f5; }}
            """
        )
        self.cancel_button.clicked.connect(self._on_cancel)

        self.next_button = QPushButton("完成并进入下一任务")
        set_primary(self.next_button)
        self.next_button.setFixedHeight(38)
        self.next_button.clicked.connect(self._on_finish)

        buttons.addStretch()
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.next_button)
        layout.addLayout(buttons)

        return panel

    # 新增：单行质量检查项布局
    def _create_quality_row(self, label: str, value: str, color: str) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(12)

        # 左侧彩色竖条
        bar = QFrame()
        bar.setFixedSize(4, 28)
        bar.setStyleSheet(
            f"background: {'#27ae60' if color == 'green' else '#f39c12'}; border-radius: 2px;"
        )

        name_label = QLabel(label)
        name_label.setStyleSheet(f"color: {TEXT}; font-size: 14px;")
        value_label = QLabel(value)
        value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        value_label.setStyleSheet(f"color: {MUTED}; font-size: 13px;")

        row.addWidget(bar)
        row.addWidget(name_label, 1)
        row.addWidget(value_label, 1)
        return row

    # 新增：接收主窗口传入的上下文并启动扫描
    def start_scan(
        self,
        user: User,
        assessment: AssessmentSession,
        scan_type: str,
        task_index: int = 1,
        total_tasks: int = 3,
    ) -> None:
        self.current_user = user
        self.current_assessment = assessment
        self.scan_type = scan_type
        self.task_index = task_index
        self.total_tasks = total_tasks
        self._refresh_display()

    # 新增：刷新页面所有显示文本
    def _refresh_display(self) -> None:
        if self.current_user is not None and self.current_assessment is not None:
            self.info_label.setText(
                f"<b>当前检测对象：</b>{self.current_user.patient_no} / {self.current_user.name}    "
                f"<b>检测模块：</b>脊柱评估    站位 / 坐位 / Adams"
            )
        else:
            self.info_label.setText(
                "<b>当前检测对象：</b>-    <b>检测模块：</b>脊柱评估    站位 / 坐位 / Adams"
            )

        self.progress_label.setText(f"{self.task_index} / {self.total_tasks}")
        self.title_label.setText(f"脊柱评估 - {self.scan_type}背面深度扫描")
        self.task_label.setText(f"任务 {self.task_index} / {self.total_tasks}")

    # 新增：按钮回调
    def _on_cancel(self) -> None:
        self.scan_cancelled.emit()

    def _on_finish(self) -> None:
        self.scan_finished.emit()
