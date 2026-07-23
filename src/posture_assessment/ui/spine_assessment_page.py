# 新增：脊柱评估模块首个检测入口页面
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from posture_assessment.models import AssessmentSession, User
from posture_assessment.ui.theme import BORDER, MUTED, TEXT, set_primary


class SpineAssessmentPage(QWidget):
    """新增：脊柱评估 - 三维体表扫描入口页。"""

    # 新增：三个扫描类型的开始检测信号
    start_standing_scan = Signal()
    start_sitting_scan = Signal()
    start_adams_scan = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.current_user: User | None = None
        self.current_assessment: AssessmentSession | None = None
        self._build_ui()
        self._refresh_context_display()

    # 新增：构建页面主布局
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(34, 24, 34, 24)
        root.setSpacing(20)

        # 新增：顶部信息栏（当前对象 / 检测模块 / 进度）
        header = QHBoxLayout()
        header.setSpacing(18)
        self.info_label = QLabel()
        self.info_label.setStyleSheet(f"color: {TEXT}; font-size: 15px;")
        self.progress_label = QLabel("0 / 3")
        self.progress_label.setStyleSheet(
            f"color: {MUTED}; font-size: 15px; font-weight: 600;"
        )
        header.addWidget(self.info_label)
        header.addStretch()
        header.addWidget(self.progress_label)
        root.addLayout(header)

        # 新增：三个扫描卡片横向排列
        panels_layout = QHBoxLayout()
        panels_layout.setSpacing(24)

        self.standing_panel = self._create_scan_panel(
            title="站位脊柱体表扫描",
            overlay_text="背面+双侧深度",
            start_callback=self._start_standing_scan,
        )
        self.sitting_panel = self._create_scan_panel(
            title="坐位脊柱体表扫描",
            overlay_text="坐位背面深度",
            start_callback=self._start_sitting_scan,
        )
        self.adams_panel = self._create_scan_panel(
            title="Adams 前屈扫描",
            overlay_text="横断面高度差",
            start_callback=self._start_adams_scan,
        )

        panels_layout.addWidget(self.standing_panel, 1)
        panels_layout.addWidget(self.sitting_panel, 1)
        panels_layout.addWidget(self.adams_panel, 1)
        root.addLayout(panels_layout, 1)

        # 新增：采集要求说明
        self.requirements_label = QLabel(
            "采集要求：按规范暴露背部可见体表标志区域，长发、饰物和宽松衣物不得遮挡颈胸、胸腰及腰骶区域；"
            "单台 Azure Kinect DK 使用深度点云拟合背部体表，全程不贴物理标记点并完成隐私确认。"
            "矢状面体表代理量用复用本次会话的体态左/右侧 RGB-D（capture PT-L-003 / PT-R-004），配准 RMS 3.6mm；"
            "倒位缺失或配准失败时不发布矢状面指标。界面可叠加配准的半透明脊柱解剖参考层，"
            "但其不参与数值计算，也不是椎体影像或 X 光 Cobb 角。"
        )
        self.requirements_label.setObjectName("muted")
        self.requirements_label.setWordWrap(True)
        self.requirements_label.setStyleSheet(
            f"color: {MUTED}; font-size: 13px; line-height: 1.6;"
        )
        root.addWidget(self.requirements_label)

    # 新增：创建单个扫描卡片（图片占位区 + 标题 + 开始检测按钮）
    def _create_scan_panel(
        self,
        title: str,
        overlay_text: str,
        start_callback,
    ) -> QFrame:
        panel = QFrame()
        panel.setObjectName("card")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 16)
        layout.setSpacing(12)

        # 新增：扫描图像占位区（后续可替换为真实 QPixmap）
        image_area = QFrame()
        image_area.setMinimumHeight(380)
        image_area.setStyleSheet(
            f"""
            QFrame {{
                background: qradialgradient(
                    cx:0.5, cy:0.4, radius:0.8,
                    stop:0 #6e8fa3, stop:0.4 #3a5a6e, stop:1 #1f3340
                );
                border: 1px solid {BORDER};
                border-radius: 4px;
            }}
            """
        )
        image_layout = QVBoxLayout(image_area)
        image_layout.setContentsMargins(0, 0, 0, 0)
        image_layout.setAlignment(Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft)

        overlay = QLabel(f"  {overlay_text}")
        overlay.setStyleSheet(
            "color: white; background: rgba(30, 30, 30, 0.65); "
            "padding: 6px 10px; font-size: 13px; border-radius: 0 0 0 3px;"
        )
        image_layout.addWidget(overlay)

        title_label = QLabel(title)
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_label.setStyleSheet("font-size: 16px; font-weight: 600;")

        start_button = QPushButton("开始检测")
        start_button.setFixedSize(130, 40)
        set_primary(start_button)
        start_button.clicked.connect(start_callback)

        layout.addWidget(image_area, 1)
        layout.addWidget(title_label)
        layout.addWidget(start_button, alignment=Qt.AlignmentFlag.AlignCenter)

        return panel

    # 新增：接收主窗口传入的当前检测对象与会话
    def set_detection_context(
        self, user: User, assessment: AssessmentSession
    ) -> None:
        self.current_user = user
        self.current_assessment = assessment
        self._refresh_context_display()

    # 新增：刷新顶部信息栏文本
    def _refresh_context_display(self) -> None:
        if self.current_user is None or self.current_assessment is None:
            self.info_label.setText(
                "<b>当前检测对象：</b>-    <b>检测模块：</b>脊柱评估    站位 / 坐位 / Adams"
            )
            self.progress_label.setText("0 / 3")
            return

        self.info_label.setText(
            f"<b>当前检测对象：</b>{self.current_user.patient_no} / {self.current_user.name}    "
            f"<b>检测模块：</b>脊柱评估    站位 / 坐位 / Adams"
        )
        # 进度占位，后续接入真实检测状态后再更新
        self.progress_label.setText("0 / 3")

    # 修改：点击开始检测后发出信号，由主窗口接管页面跳转
    def _start_standing_scan(self) -> None:
        self.start_standing_scan.emit()

    def _start_sitting_scan(self) -> None:
        self.start_sitting_scan.emit()

    def _start_adams_scan(self) -> None:
        self.start_adams_scan.emit()
