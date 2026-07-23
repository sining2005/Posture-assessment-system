# 新增：关节活动度检测模块页面
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from posture_assessment.models import AssessmentSession, User
from posture_assessment.ui.theme import BORDER, MUTED, PINK, PINK_DARK, TEXT, set_primary


# 新增：18 个关节活动度检测项目
JOINT_TEST_ITEMS: list[str] = [
    "颈部前屈",
    "颈部后伸",
    "颈椎侧屈",
    "肩关节外展",
    "肩关节前屈",
    "胸腰椎前屈",
    "胸腰椎后伸",
    "躯干侧屈",
    "髋关节外展",
    "髋关节前屈",
    "膝关节屈伸",
    "颈椎旋转",
    "肩关节后伸",
    "肩关节内旋/外旋",
    "胸腰椎旋转",
    "髋关节后伸",
    "髋关节内收",
    "踝关节背屈/跖屈",
]


# 新增：每个项目的采集动作说明（后续可由后台配置替换）
JOINT_INSTRUCTIONS: dict[str, str] = {
    "颈部前屈": "受试者保持躯干直立，下颌缓慢靠近胸骨。达到最大活动度后保持 1 秒。",
    "颈部后伸": "受试者保持躯干直立，头部缓慢向后仰至最大活动度并保持 1 秒。",
    "颈椎侧屈": "受试者保持躯干与肩部固定，头部缓慢向一侧倾斜至最大活动度。",
    "肩关节外展": "受试者站立，手臂贴紧躯干，缓慢向外侧抬起至最大高度并保持。",
    "肩关节前屈": "受试者站立，手臂自然下垂，缓慢向前上方抬起至最大高度并保持。",
    "胸腰椎前屈": "受试者站立，双膝伸直，缓慢向前弯腰，指尖尽量接近地面。",
    "胸腰椎后伸": "受试者站立，双手叉腰，缓慢向后伸展躯干至最大活动度。",
    "躯干侧屈": "受试者站立，双脚并拢，身体缓慢向一侧侧屈，保持髋部不移动。",
    "髋关节外展": "受试者侧卧或站立扶稳，单腿缓慢向侧方抬起至最大活动度。",
    "髋关节前屈": "受试者站立或仰卧，单腿缓慢向前上方抬起，保持膝关节伸直。",
    "膝关节屈伸": "受试者坐位或仰卧，小腿缓慢做屈伸运动至最大活动度。",
    "颈椎旋转": "受试者保持躯干固定，头部缓慢向一侧转动至最大角度并保持。",
    "肩关节后伸": "受试者站立，手臂缓慢向后方伸展至最大活动度并保持。",
    "肩关节内旋/外旋": "受试者肘关节屈曲 90°，前臂缓慢做内旋和外旋至最大角度。",
    "胸腰椎旋转": "受试者坐位或站立，躯干缓慢向一侧旋转至最大角度并保持。",
    "髋关节后伸": "受试者站立扶稳或俯卧，单腿缓慢向后伸展至最大活动度。",
    "髋关节内收": "受试者侧卧或站立扶稳，单腿缓慢向对侧方向抬起至最大活动度。",
    "踝关节背屈/跖屈": "受试者坐位，膝关节屈曲，足部缓慢做背屈和跖屈至最大角度。",
}


# 新增：关节活动度中心示意图（QPainter 绘制，后续可替换为真实素材）
class JointDiagram(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.title_text = "颈部前屈"
        self.setMinimumHeight(360)
        self.setStyleSheet("background: white;")

    def set_title(self, title: str) -> None:
        self.title_text = title
        self.update()

    def paintEvent(self, event) -> None:  # noqa: ARG002
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        cx, cy = w // 2, h // 2 - 10

        # 标题
        painter.setPen(QPen(QColor(TEXT), 1))
        painter.drawText(QRect(0, 10, w, 30), Qt.AlignmentFlag.AlignCenter, self.title_text)

        pen = QPen(QColor(MUTED), 3)
        painter.setPen(pen)

        # 头部（椭圆）
        head_w, head_h = 60, 70
        head_rect = QRect(cx - head_w // 2, cy - 120, head_w, head_h)
        painter.drawEllipse(head_rect)

        # 躯干（圆角矩形）
        torso_rect = QRect(cx - 45, cy - 40, 90, 180)
        painter.drawRoundedRect(torso_rect, 20, 20)

        # 基准竖线（红色）
        painter.setPen(QPen(QColor(PINK), 2))
        painter.drawLine(cx, cy - 130, cx, cy + 20)

        # 活动角度弧线（示意）
        painter.setPen(QPen(QColor(PINK_DARK), 2))
        arc_rect = QRect(cx, cy - 130, 100, 120)
        painter.drawArc(arc_rect, 90 * 16, -55 * 16)

        # 角度标注文字
        painter.setPen(QPen(QColor(PINK_DARK), 1))
        painter.drawText(QPoint(cx + 55, cy - 90), "0° → 最大主动角度")


class JointMobilityPage(QWidget):
    """新增：关节活动度 - 分项检测入口页。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.current_user: User | None = None
        self.current_assessment: AssessmentSession | None = None
        self._build_ui()
        self._refresh_context_display()
        self._select_item(0)

    # 新增：构建页面主布局
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(34, 24, 34, 24)
        root.setSpacing(20)

        # 新增：顶部信息栏
        header = QHBoxLayout()
        header.setSpacing(18)
        self.info_label = QLabel()
        self.info_label.setStyleSheet(f"color: {TEXT}; font-size: 15px;")
        self.progress_label = QLabel("已完成 0 / 18")
        self.progress_label.setStyleSheet(
            f"color: {MUTED}; font-size: 15px; font-weight: 600;"
        )
        self.save_status_label = QLabel("未保存")
        self.save_status_label.setStyleSheet(f"color: {PINK}; font-size: 14px;")
        header.addWidget(self.info_label)
        header.addStretch()
        header.addWidget(self.progress_label)
        header.addWidget(self.save_status_label)
        root.addLayout(header)

        # 新增：三栏主内容区
        content = QHBoxLayout()
        content.setSpacing(24)
        content.addWidget(self._build_items_panel(), 1)
        content.addWidget(self._build_center_panel(), 2)
        content.addWidget(self._build_settings_panel(), 1)
        root.addLayout(content, 1)

    # 新增：左侧检测项目列表
    def _build_items_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("card")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel("检测项目")
        title.setStyleSheet("font-size: 17px; font-weight: 700;")

        subtitle = QLabel("共18项 · 向下滚动查看全部项目")
        subtitle.setStyleSheet(f"color: {MUTED}; font-size: 12px;")

        self.items_list = QListWidget()
        self.items_list.setSpacing(2)
        self.items_list.setFrameShape(QFrame.Shape.NoFrame)
        self.items_list.setStyleSheet(
            f"""
            QListWidget {{
                background: white;
                outline: none;
                border: none;
            }}
            QListWidget::item {{
                color: {TEXT};
                padding: 10px 12px;
                border-radius: 3px;
                border-bottom: 1px solid {BORDER};
            }}
            QListWidget::item:selected {{
                background: {PINK};
                color: white;
                border: none;
            }}
            QListWidget::item:hover {{
                background: #fbebed;
            }}
            """
        )
        for name in JOINT_TEST_ITEMS:
            item = QListWidgetItem(name)
            self.items_list.addItem(item)
        self.items_list.currentRowChanged.connect(self._on_item_changed)

        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addWidget(self.items_list, 1)
        return panel

    # 新增：中间示意图与操作区
    def _build_center_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("card")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(20, 20, 20, 24)
        layout.setSpacing(16)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.diagram = JointDiagram()

        self.instruction_label = QLabel()
        self.instruction_label.setWordWrap(True)
        self.instruction_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.instruction_label.setStyleSheet(
            f"color: {MUTED}; font-size: 14px; padding: 0 40px;"
        )

        self.start_button = QPushButton("开始检测")
        set_primary(self.start_button)
        self.start_button.setFixedSize(130, 40)
        self.start_button.clicked.connect(self._start_current_test)

        layout.addWidget(self.diagram, 1)
        layout.addWidget(self.instruction_label)
        layout.addWidget(self.start_button, alignment=Qt.AlignmentFlag.AlignCenter)
        return panel

    # 新增：右侧设置面板
    def _build_settings_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("card")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        title = QLabel("本次设置")
        title.setStyleSheet("font-size: 17px; font-weight: 700;")
        layout.addWidget(title)

        # 新增：设置下拉框
        self.side_layout, self.side_combo = self._create_labeled_combo(
            "侧别", ["不分侧", "左侧", "右侧"]
        )
        self.repeat_layout, self.repeat_combo = self._create_labeled_combo(
            "重复次数", ["1次", "2次", "3次", "5次"]
        )
        self.repeat_combo.setCurrentText("3次")
        self.value_layout, self.value_combo = self._create_labeled_combo(
            "取值方式", ["最大值", "最小值", "平均值"]
        )
        self.value_combo.setCurrentText("最大值")

        # 新增：相机/采集状态
        status_label = QLabel("● 相机已连接")
        status_label.setStyleSheet("color: #27ae60; font-size: 14px;")
        not_captured_label = QLabel("○ 尚未采集")
        not_captured_label.setStyleSheet(f"color: {MUTED}; font-size: 14px;")

        # 新增：说明文本
        hint = QLabel(
            "角度范围 0°–180°，显示值按四舍五入取整；原始值需保留。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(
            f"color: {MUTED}; font-size: 12px; line-height: 1.5; "
            f"border-left: 3px solid {BORDER}; padding-left: 10px; margin-top: 10px;"
        )

        layout.addLayout(self.side_layout)
        layout.addLayout(self.repeat_layout)
        layout.addLayout(self.value_layout)
        layout.addSpacing(10)
        layout.addWidget(status_label)
        layout.addWidget(not_captured_label)
        layout.addStretch()
        layout.addWidget(hint)
        return panel

    # 新增：带标签的下拉框辅助方法，返回 (布局, 下拉框)
    def _create_labeled_combo(
        self, label_text: str, items: list[str]
    ) -> tuple[QHBoxLayout, QComboBox]:
        row = QHBoxLayout()
        row.setSpacing(12)
        label = QLabel(label_text)
        label.setFixedWidth(70)
        label.setStyleSheet(f"color: {TEXT}; font-size: 14px;")
        combo = QComboBox()
        combo.addItems(items)
        combo.setFixedHeight(34)
        combo.setStyleSheet(
            f"""
            QComboBox {{
                border: 1px solid {BORDER};
                border-radius: 3px;
                padding: 4px 8px;
                background: white;
            }}
            QComboBox::drop-down {{ border: none; }}
            """
        )
        row.addWidget(label)
        row.addWidget(combo, 1)
        return row, combo

    # 新增：切换检测项目时更新中间显示
    def _on_item_changed(self, row: int) -> None:
        self._select_item(row)

    def _select_item(self, row: int) -> None:
        if row < 0 or row >= len(JOINT_TEST_ITEMS):
            return
        self.items_list.setCurrentRow(row)
        item_name = JOINT_TEST_ITEMS[row]
        self.diagram.set_title(item_name)
        self.instruction_label.setText(
            JOINT_INSTRUCTIONS.get(
                item_name, "按规范完成该项目的最大主动活动度测试。"
            )
        )

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
                "<b>当前检测对象：</b>-    <b>检测模块：</b>关节活动度"
            )
            self.progress_label.setText("已完成 0 / 18")
            return

        self.info_label.setText(
            f"<b>当前检测对象：</b>{self.current_user.patient_no} / {self.current_user.name}    "
            f"<b>检测模块：</b>关节活动度"
        )
        self.progress_label.setText("已完成 0 / 18")

    # 新增：开始当前选中项目的检测（占位回调）
    def _start_current_test(self) -> None:
        row = self.items_list.currentRow()
        if row < 0 or row >= len(JOINT_TEST_ITEMS):
            return
        item_name = JOINT_TEST_ITEMS[row]
        # TODO：接入真实采集流程
        print(f"开始检测：{item_name}")
