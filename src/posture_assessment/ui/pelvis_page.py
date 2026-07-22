from __future__ import annotations

from enum import Enum

import numpy as np
from PySide6.QtCore import QPointF, Qt, QThreadPool, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from posture_assessment.models import AssessmentSession, User
from posture_assessment.pelvis.service import PelvisService
from posture_assessment.pelvis.types import (
    PELVIS_VIEW_SEQUENCE,
    PelvisAnalysisResult,
    PelvisMeasurement,
)
from posture_assessment.posture.camera import DeviceInfo
from posture_assessment.posture.quality import FAILURE_MESSAGES
from posture_assessment.posture.types import (
    JOINT_INDEX,
    FrameBundle,
    PoseKind,
    QualityAssessment,
)
from posture_assessment.ui.dialogs import show_error, show_info
from posture_assessment.ui.posture_page import CaptureDialog, PoseCard, point_cloud_pixmap
from posture_assessment.ui.theme import PINK, set_primary
from posture_assessment.ui.workers import TaskWorker


class PelvisUiState(str, Enum):
    IDLE = "待检测"
    OVERVIEW = "四方向采集"
    CAPTURE = "单方向采集"
    ANALYZING = "分析中"
    RETAKE = "需重拍"
    RESULT = "已完成"


def pelvis_overlay_pixmap(
    frame: FrameBundle, view: PoseKind, width: int = 620, height: int = 620
) -> QPixmap:
    """Render only observable body/skeleton proxies; never an anatomical bone model."""
    canvas = point_cloud_pixmap(frame, view, width, height)
    joints = np.asarray(frame.joints_mm, dtype=float)
    if joints.ndim == 3:
        joints = np.median(joints, axis=0)
    if view in (PoseKind.LEFT, PoseKind.RIGHT):
        horizontal = joints[:, 2] - np.median(joints[:, 2])
    else:
        horizontal = joints[:, 0]
    px = width / 2 + horizontal * (width / 1000.0)
    py = height - 22 - joints[:, 1] * (height / 1850.0)

    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    hip_left = JOINT_INDEX["hip_left"]
    hip_right = JOINT_INDEX["hip_right"]
    ankle_left = JOINT_INDEX["ankle_left"]
    ankle_right = JOINT_INDEX["ankle_right"]
    pelvis = JOINT_INDEX["pelvis"]
    painter.setPen(QPen(QColor(PINK), 4))
    painter.drawLine(
        QPointF(px[hip_left], py[hip_left]),
        QPointF(px[hip_right], py[hip_right]),
    )
    painter.setPen(QPen(QColor("#55d18a"), 3, Qt.PenStyle.DashLine))
    painter.drawLine(
        QPointF(px[ankle_left], py[ankle_left]),
        QPointF(px[ankle_right], py[ankle_right]),
    )
    painter.setPen(QPen(QColor("#f4c95d"), 2))
    support_x = (px[ankle_left] + px[ankle_right]) / 2.0
    painter.drawLine(QPointF(support_x, py[pelvis] - 120), QPointF(support_x, py[pelvis] + 220))
    painter.setBrush(QColor("#ffffff"))
    painter.drawEllipse(QPointF(px[pelvis], py[pelvis]), 6, 6)
    painter.setPen(QColor("#ffffff"))
    painter.drawText(18, 30, f"{view.label} · 髋中心/骨盆模型代理点")
    painter.drawText(18, height - 18, "红：髋轴　绿：支撑基底　黄：支撑中线")
    painter.end()
    return canvas


def pelvis_capture_pixmap(
    frame: FrameBundle, view: PoseKind, width: int = 900, height: int = 440
) -> QPixmap:
    """Three honest capture views: RGB, depth pseudo-colour, and proxy overlay."""
    canvas = QPixmap(width, height)
    canvas.fill(QColor("#06172d"))
    pane_width = width // 3
    rgb_array = np.ascontiguousarray(frame.color[..., :3].astype(np.uint8))
    rgb_image = QImage(
        rgb_array.data,
        rgb_array.shape[1],
        rgb_array.shape[0],
        rgb_array.strides[0],
        QImage.Format.Format_RGB888,
    ).copy()
    rgb = QPixmap.fromImage(rgb_image).scaled(
        pane_width - 12,
        height - 52,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    depth = point_cloud_pixmap(frame, view, pane_width - 12, height - 52)
    overlay = pelvis_overlay_pixmap(frame, view, pane_width - 12, height - 52)
    painter = QPainter(canvas)
    painter.setPen(QColor("#ffffff"))
    for index, (label, pixmap) in enumerate(
        (("RGB 人体轮廓", rgb), ("深度伪彩 + 骨架", depth), ("髋部 ROI + 代理点", overlay))
    ):
        x = index * pane_width + (pane_width - pixmap.width()) // 2
        painter.drawText(index * pane_width + 12, 24, label)
        painter.drawPixmap(x, 36, pixmap)
    pelvis = frame.joints_mm[JOINT_INDEX["pelvis"]]
    valid = frame.body_mask > 0
    coverage = np.count_nonzero(frame.depth_mm[valid]) / max(1, int(valid.sum()))
    confidence = np.count_nonzero(frame.joint_confidence >= 2)
    painter.drawText(
        12,
        height - 10,
        f"距离 {pelvis[2] / 1000.0:.2f} m　深度覆盖 {coverage:.1%}　Medium+ 关节 {confidence}/32",
    )
    painter.end()
    return canvas


class PelvisCaptureDialog(CaptureDialog):
    def _preview_ready(self, frame: FrameBundle) -> None:
        self._preview_busy = False
        self.preview.setPixmap(
            pelvis_capture_pixmap(frame, self.pose, 900, 440).scaled(
                self.preview.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        self.status.setText("● RGB / 深度 / 髋部代理点预览已就绪；采集后执行稳定度质量门")
        self.capture_button.setEnabled(True)
        if self._capture_pending:
            self._start_capture_worker()
        elif not self._closing and not self.preview_timer.isActive():
            self.preview_timer.start()


class PelvisPage(QWidget):
    analysis_ready = Signal(int)

    def __init__(
        self,
        service: PelvisService,
        operator_name: str,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.service = service
        self.operator_name = operator_name
        self.user: User | None = None
        self.assessment: AssessmentSession | None = None
        self.state = PelvisUiState.IDLE
        self.device_info: DeviceInfo | None = None
        self.qualities: dict[PoseKind, QualityAssessment] = {}
        self.frames: dict[PoseKind, FrameBundle] = {}
        self.result: PelvisAnalysisResult | None = None
        self.thread_pool = QThreadPool.globalInstance()
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.patient_bar = QLabel("请先从用户信息页选择检测对象")
        self.patient_bar.setStyleSheet(
            "background:white; border-bottom:1px solid #d5d7d9; padding:12px 28px;"
        )
        root.addWidget(self.patient_bar)
        self.stack = QStackedWidget()
        self.overview_page = self._build_overview()
        self.analysis_page = self._build_analysis()
        self.result_page = self._build_result()
        for page in (self.overview_page, self.analysis_page, self.result_page):
            self.stack.addWidget(page)
        root.addWidget(self.stack, 1)

    def _build_overview(self) -> QWidget:
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(36, 24, 36, 20)
        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("单台 Azure Kinect 骨盆体态筛查")
        title.setObjectName("pageTitle")
        title_box.addWidget(title)
        self.device_label = QLabel("先进行设备、地面标定和 Body Tracking 自检")
        self.device_label.setObjectName("muted")
        title_box.addWidget(self.device_label)
        header.addLayout(title_box)
        header.addStretch()
        self.self_check_button = QPushButton("设备与环境自检")
        self.self_check_button.clicked.connect(self._device_check)
        header.addWidget(self.self_check_button)
        root.addLayout(header)

        cards = QHBoxLayout()
        cards.setSpacing(18)
        self.view_cards: dict[PoseKind, PoseCard] = {}
        for view in PELVIS_VIEW_SEQUENCE:
            card = PoseCard(view)
            card.capture_requested.connect(self._open_capture)
            card.button.setEnabled(False)
            cards.addWidget(card, 1)
            self.view_cards[view] = card
        root.addLayout(cards, 1)

        note = QLabel(
            "采集顺序：正面 → 左侧 → 背面 → 右侧。请穿贴身、无遮挡骨盆轮廓的衣物；"
            "本模块输出体态筛查代理值，不恢复真实 ASIS/PSIS，也不进行骨骼重建。"
        )
        note.setWordWrap(True)
        note.setObjectName("muted")
        root.addWidget(note)
        footer = QHBoxLayout()
        self.summary_label = QLabel("四方向：0 / 4　深度覆盖：-　关节置信度：-")
        self.summary_label.setObjectName("muted")
        footer.addWidget(self.summary_label)
        footer.addStretch()
        self.analyze_button = QPushButton("生成骨盆筛查结果")
        set_primary(self.analyze_button)
        self.analyze_button.setEnabled(False)
        self.analyze_button.clicked.connect(self._start_analysis)
        footer.addWidget(self.analyze_button)
        root.addLayout(footer)
        return page

    def _build_analysis(self) -> QWidget:
        page = QWidget()
        root = QVBoxLayout(page)
        root.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title = QLabel("骨盆体态代理指标计算")
        title.setObjectName("pageTitle")
        root.addWidget(title, alignment=Qt.AlignmentFlag.AlignCenter)
        self.analysis_status = QLabel("正在做重力对齐、跨视图一致性检查和置信度融合")
        root.addWidget(self.analysis_status, alignment=Qt.AlignmentFlag.AlignCenter)
        progress = QProgressBar()
        progress.setRange(0, 0)
        progress.setFixedWidth(720)
        root.addWidget(progress, alignment=Qt.AlignmentFlag.AlignCenter)
        return page

    def _build_result(self) -> QWidget:
        page = QWidget()
        root = QHBoxLayout(page)
        root.setContentsMargins(24, 18, 24, 20)
        left = QVBoxLayout()
        title = QLabel("骨盆筛查结果 · 二维代理点叠加")
        title.setObjectName("pageTitle")
        left.addWidget(title)
        self.result_visual = QLabel("等待结果")
        self.result_visual.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.result_visual.setStyleSheet("background:#06172d; color:white;")
        self.result_visual.setMinimumSize(500, 520)
        left.addWidget(self.result_visual, 1)
        root.addLayout(left, 3)

        panel = QFrame()
        panel.setObjectName("card")
        right = QVBoxLayout(panel)
        right.addWidget(QLabel("结构化实验性指标"))
        self.result_table = QTableWidget(0, 4)
        self.result_table.setHorizontalHeaderLabels(["指标", "实际值", "来源", "置信度"])
        self.result_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.result_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        right.addWidget(self.result_table, 1)
        self.review_label = QLabel()
        self.review_label.setWordWrap(True)
        self.review_label.setObjectName("muted")
        right.addWidget(self.review_label)
        disclaimer = QLabel(
            "免责声明：所有指标均为体态筛查代理值／实验性结果；SDK 模型骨盆姿态不等同于"
            " ASIS–PSIS 临床骨盆角，未经本地验证不得用于诊断或医学分级。"
        )
        disclaimer.setWordWrap(True)
        disclaimer.setStyleSheet(f"color:{PINK}; font-weight:700;")
        right.addWidget(disclaimer)
        buttons = QHBoxLayout()
        back = QPushButton("返回采集/重拍")
        back.clicked.connect(lambda: self.stack.setCurrentWidget(self.overview_page))
        buttons.addWidget(back)
        self.report_button = QPushButton("提交结构化结果")
        set_primary(self.report_button)
        self.report_button.clicked.connect(self._submit_result)
        buttons.addWidget(self.report_button)
        right.addLayout(buttons)
        root.addWidget(panel, 2)
        return page

    def set_detection_context(self, user: User, assessment: AssessmentSession) -> None:
        self.user = user
        self.assessment = assessment
        self.state = PelvisUiState.OVERVIEW
        self.device_info = None
        self.qualities.clear()
        self.frames.clear()
        self.result = None
        self.patient_bar.setText(
            f"当前检测对象　{user.patient_no} / {user.name}　检测模块：骨盆体态筛查　"
            f"会话：{assessment.session_no}"
        )
        for view, card in self.view_cards.items():
            card.preview.clear()
            card.preview.setText("人体定位\n等待采集")
            card.button.setText(view.label)
            card.button.setEnabled(False)
            card.status.setText("待采集")
            card.status.setStyleSheet("")
        self.analyze_button.setEnabled(False)
        self.summary_label.setText("四方向：0 / 4　深度覆盖：-　关节置信度：-")
        self.stack.setCurrentWidget(self.overview_page)
        # Restore an interrupted four-view session from the latest DB attempts.
        for view, capture in self.service.latest_captures(assessment.id).items():
            quality = self.service._quality_from_json(capture.quality_json)
            frame = self.service.representative_frame(assessment.id, view)
            if frame is None:
                try:
                    frame = self.service._frames_for(assessment.id, view, capture)[0]
                except Exception:
                    frame = None
            self.qualities[view] = quality
            if frame is not None:
                self.frames[view] = frame
                self.view_cards[view].update_capture(frame, quality)
        self._update_summary()

    def _device_check(self) -> None:
        self.self_check_button.setEnabled(False)
        self.device_label.setText("正在检查相机、Body Tracking 与地面标定…")
        worker = TaskWorker(self.service.device_self_check)
        worker.signals.result.connect(self._device_ready)
        worker.signals.error.connect(self._device_failed)
        self.thread_pool.start(worker)

    def _device_ready(self, info: DeviceInfo) -> None:
        self.device_info = info
        self.self_check_button.setEnabled(True)
        mode = info.tracking_mode or "未报告"
        self.device_label.setText(
            f"● 已连接：{info.model} / {info.serial_number}　处理模式：{mode}"
        )
        for card in self.view_cards.values():
            card.button.setEnabled(self.assessment is not None)

    def _device_failed(self, message: str) -> None:
        self.self_check_button.setEnabled(True)
        self.device_label.setText("设备或环境自检失败")
        show_error(self, "骨盆检测自检失败", message)

    def _open_capture(self, view: PoseKind) -> None:
        if self.assessment is None:
            show_info(self, "尚未选择检测对象", "请先从用户信息页开始检测。")
            return
        self.state = PelvisUiState.CAPTURE
        dialog = PelvisCaptureDialog(self.service, self.assessment.id, view, self)
        dialog.setWindowTitle(f"骨盆检测 - {view.label}")
        dialog.instruction.setText(
            f"请按{view.label}自然站立，骨盆区域无遮挡；稳定后保存 2 秒 RGB-D 窗口"
        )
        dialog.capture_finished.connect(lambda capture, quality, v=view: self._capture_ready(v, quality))
        dialog.exec()
        self.state = PelvisUiState.OVERVIEW

    def _capture_ready(self, view: PoseKind, quality: QualityAssessment) -> None:
        if self.assessment is None:
            return
        frame = self.service.representative_frame(self.assessment.id, view)
        if frame is not None:
            self.frames[view] = frame
            self.view_cards[view].update_capture(frame, quality)
        self.qualities[view] = quality
        self._update_summary()

    def _update_summary(self) -> None:
        passed = [quality for quality in self.qualities.values() if quality.passed]
        depth = np.mean([quality.depth_coverage for quality in passed]) if passed else 0.0
        joints = np.mean([quality.joint_valid_count for quality in passed]) if passed else 0.0
        self.summary_label.setText(
            f"四方向：{len(passed)} / 4　平均深度覆盖：{depth:.1%}　有效关节：{joints:.0f}/32"
        )
        self.analyze_button.setEnabled(len(passed) == len(PELVIS_VIEW_SEQUENCE))

    def _start_analysis(self) -> None:
        if self.assessment is None:
            return
        self.state = PelvisUiState.ANALYZING
        self.stack.setCurrentWidget(self.analysis_page)
        worker = TaskWorker(self.service.analyze, self.assessment.id)
        worker.signals.result.connect(self._analysis_ready)
        worker.signals.error.connect(self._analysis_failed)
        self.thread_pool.start(worker)

    def _analysis_ready(self, result: PelvisAnalysisResult) -> None:
        self.result = result
        if result.retake_views:
            self.state = PelvisUiState.RETAKE
            labels = "、".join(view.label for view in result.retake_views)
            show_info(self, "需要重拍", f"以下方向未通过质量门：{labels}")
            self.stack.setCurrentWidget(self.overview_page)
            return
        self.state = PelvisUiState.RESULT
        self._populate_result(result)
        self.stack.setCurrentWidget(self.result_page)

    def _analysis_failed(self, message: str) -> None:
        self.state = PelvisUiState.OVERVIEW
        self.stack.setCurrentWidget(self.overview_page)
        show_error(self, "骨盆筛查分析失败", message)

    @staticmethod
    def _display_measurements(result: PelvisAnalysisResult) -> list[PelvisMeasurement]:
        fused_codes = {item.code for item in result.measurements if "+" in item.source_view}
        return [
            item
            for item in result.measurements
            if "+" in item.source_view or item.code not in fused_codes
        ]

    def _populate_result(self, result: PelvisAnalysisResult) -> None:
        frame = self.frames.get(PoseKind.FRONT) or self.frames.get(PoseKind.BACK)
        view = PoseKind.FRONT if PoseKind.FRONT in self.frames else PoseKind.BACK
        if frame is not None:
            self.result_visual.setPixmap(
                pelvis_overlay_pixmap(frame, view, 620, 620).scaled(
                    self.result_visual.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        rows = self._display_measurements(result)
        self.result_table.setRowCount(len(rows))
        labels = {view.value: view.label for view in PELVIS_VIEW_SEQUENCE}
        for row, item in enumerate(rows):
            source = "+".join(labels.get(value, value) for value in item.source_view.split("+"))
            value = f"{item.direction + ' ' if item.direction else ''}{item.value:g}{item.unit}"
            for column, text in enumerate((item.name, value, source, f"{item.confidence:.1f}%")):
                self.result_table.setItem(row, column, QTableWidgetItem(text))
        if result.review_reasons:
            self.review_label.setText("需要人工复核/重拍建议：\n" + "\n".join(result.review_reasons))
            self.report_button.setText("提交待复核结果")
        else:
            self.review_label.setText(
                f"算法版本：{result.algorithm_version}　跨视图一致性检查通过"
            )
            self.report_button.setText("提交结构化结果")

    def _submit_result(self) -> None:
        if self.assessment is None or self.result is None:
            return
        if self.result.review_reasons:
            response = QMessageBox.question(
                self,
                "提交待复核结果",
                "结果存在跨视图不一致，仅以待复核状态提交。是否继续？",
            )
            if response != QMessageBox.StandardButton.Yes:
                return
        self.analysis_ready.emit(self.assessment.id)
        show_info(self, "结构化结果已就绪", "报告模块可读取骨盆指标、质量数据、算法版本和免责声明。")

    def close_camera(self) -> None:
        self.service.close()
