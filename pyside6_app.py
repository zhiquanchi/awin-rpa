from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path
from threading import Event

from PySide6.QtCore import QTimer, QThread, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app_interfaces import (
    AppRuntimeStateViewModel,
    ApplicationServiceProtocol,
    ExecutionLogEntryViewModel,
    ExecutionResultViewModel,
    InvitationTemplate,
)
from awin_application_service import AwinApplicationService

LOG_FILE_PATH = Path(__file__).parent / "file.log"
LOG_LEVEL_MAP = {
    "INFO": "info",
    "ERROR": "error",
    "WARNING": "warning",
    "WARN": "warning",
    "SUCCESS": "success",
    "DEBUG": "info",
    "TRACE": "info",
    "CRITICAL": "error",
}


def _parse_loguru_line(line: str) -> tuple[str, str, str] | None:
    """解析 loguru 格式的日志行，返回 (timestamp_hhmmss, ui_level, message)。"""
    # 格式: 2026-06-02 09:46:25.545 | INFO | __main__:func:188 - message
    parts = line.split(" | ", 2)
    if len(parts) < 3:
        return None

    timestamp_raw = parts[0].strip()
    level_raw = parts[1].strip()
    rest = parts[2].strip()

    # 从 rest 中提取消息: module:function:line - message
    msg_match = re.match(r"^[\w.]+:[\w_<>]+:\d+\s+-\s+(.*)$", rest)
    if msg_match:
        message = msg_match.group(1)
    else:
        if " - " in rest:
            message = rest.split(" - ", 1)[1]
        else:
            message = rest

    try:
        dt = datetime.strptime(timestamp_raw, "%Y-%m-%d %H:%M:%S.%f")
        timestamp = dt.strftime("%H:%M:%S")
    except ValueError:
        timestamp = timestamp_raw

    ui_level = LOG_LEVEL_MAP.get(level_raw.upper(), "info")
    return timestamp, ui_level, message


def _load_file_log_lines(max_lines: int = 200) -> list[tuple[str, str, str]]:
    """读取 file.log 的最后 max_lines 行，返回解析后的日志条目列表。"""
    if not LOG_FILE_PATH.exists():
        return []

    try:
        text = LOG_FILE_PATH.read_text(encoding="utf-8")
    except Exception:
        return []

    lines = text.splitlines()
    lines = lines[-max_lines:]

    entries: list[tuple[str, str, str]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        parsed = _parse_loguru_line(line)
        if parsed:
            entries.append(parsed)

    return entries


APP_STYLESHEET = """
QMainWindow, QWidget#centralRoot {
    background-color: #f5f6f8;
}
QFrame#headerBar {
    background-color: #ffffff;
    border: 1px solid #e4e7ed;
    border-radius: 10px;
}
QLabel#appTitle {
    font-size: 22px;
    font-weight: 700;
    color: #303133;
}
QFrame#statCard {
    background-color: #ffffff;
    border: 1px solid #e4e7ed;
    border-radius: 10px;
}
QLabel#cardCaption {
    color: #909399;
    font-size: 12px;
}
QLabel#cardValue {
    color: #303133;
    font-size: 28px;
    font-weight: 700;
}
QLabel#cardSubValue {
    color: #606266;
    font-size: 13px;
}
QFrame#panelCard {
    background-color: #ffffff;
    border: 1px solid #e4e7ed;
    border-radius: 10px;
}
QLabel#panelTitle {
    font-size: 15px;
    font-weight: 600;
    color: #303133;
}
QLabel#hintLabel {
    color: #909399;
    font-size: 12px;
}
QPushButton#primaryButton {
    background-color: #409eff;
    color: #ffffff;
    border: none;
    border-radius: 8px;
    padding: 10px 16px;
    font-weight: 600;
}
QPushButton#primaryButton:hover {
    background-color: #66b1ff;
}
QPushButton#primaryButton:disabled {
    background-color: #a0cfff;
}
QPushButton#dangerButton {
    background-color: #ffffff;
    color: #f56c6c;
    border: 1px solid #fbc4c4;
    border-radius: 8px;
    padding: 8px 16px;
}
QPushButton#browserConnectedButton {
    background-color: #e1f3d8;
    color: #67c23a;
    border: 1px solid #b3e19d;
    border-radius: 16px;
    padding: 6px 14px;
    font-weight: 600;
}
QPushButton#browserDefaultButton {
    background-color: #ffffff;
    color: #606266;
    border: 1px solid #dcdfe6;
    border-radius: 16px;
    padding: 6px 14px;
}
QPushButton#cardActionButton {
    background-color: #f4f4f5;
    color: #606266;
    border: 1px solid #e4e7ed;
    border-radius: 6px;
    padding: 4px 10px;
    font-size: 12px;
}
QPlainTextEdit#logOutput {
    background-color: #1e1e2e;
    color: #e5e9f0;
    border: none;
    border-radius: 8px;
    font-family: Consolas, 'Courier New', monospace;
    font-size: 12px;
    padding: 8px;
}
QDialog {
    background-color: #f5f6f8;
}
QFrame#dialogCard {
    background-color: #ffffff;
    border: 1px solid #e4e7ed;
    border-radius: 10px;
}
QFrame#notifyCard {
    background-color: #ffffff;
    border: 1px solid #e4e7ed;
    border-radius: 10px;
}
QPushButton#notifyChip {
    background-color: #f4f4f5;
    color: #606266;
    border: 1px solid #dcdfe6;
    border-radius: 16px;
    padding: 6px 14px;
    font-size: 13px;
}
QPushButton#notifyChip:checked {
    background-color: #ecf5ff;
    color: #409eff;
    border: 1px solid #b3d8ff;
    font-weight: 600;
}
QPushButton#notifyChip:disabled {
    color: #c0c4cc;
    background-color: #f4f4f5;
}
QPushButton#notifyLinkButton {
    background: transparent;
    color: #409eff;
    border: none;
    font-size: 12px;
    padding: 0;
}
QLabel#notifyStatusWarn {
    color: #e6a23c;
    font-size: 12px;
}
QLabel#notifyStatusOk {
    color: #606266;
    font-size: 12px;
}
QLabel#webhookErrorLabel {
    color: #f56c6c;
    font-size: 12px;
}
QLineEdit#webhookErrorInput {
    border: 1px solid #f56c6c;
    background-color: #fef0f0;
}
"""


def parse_notify_channel(channel: str) -> tuple[bool, bool, bool]:
    """将 notify_channel 解析为 (启用, 本地, 飞书)。"""
    if channel == "none":
        return False, False, False
    if channel == "desktop":
        return True, True, False
    if channel == "feishu":
        return True, False, True
    if channel == "both":
        return True, True, True
    return True, True, False


def build_notify_channel(enabled: bool, desktop: bool, feishu: bool) -> str:
    """根据 UI 状态构建 notify_channel。"""
    if not enabled:
        return "none"
    if feishu and desktop:
        return "both"
    if feishu:
        return "feishu"
    if desktop:
        return "desktop"
    return "desktop"


def apply_app_styles(app: QApplication | None = None) -> None:
    """应用全局界面样式。"""
    instance = app or QApplication.instance()
    if isinstance(instance, QApplication):
        instance.setStyleSheet(APP_STYLESHEET)


class StatCard(QFrame):
    """顶部状态卡片。"""

    def __init__(
        self,
        caption: str,
        action_text: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("statCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        header = QHBoxLayout()
        self.caption_label = QLabel(caption, self)
        self.caption_label.setObjectName("cardCaption")
        header.addWidget(self.caption_label)
        header.addStretch()
        self.action_button: QPushButton | None = None
        if action_text:
            self.action_button = QPushButton(action_text, self)
            self.action_button.setObjectName("cardActionButton")
            header.addWidget(self.action_button)
        layout.addLayout(header)

        self.value_label = QLabel("0", self)
        self.value_label.setObjectName("cardValue")
        layout.addWidget(self.value_label)

        self.subtitle_label = QLabel("", self)
        self.subtitle_label.setObjectName("cardSubValue")
        self.subtitle_label.setWordWrap(True)
        layout.addWidget(self.subtitle_label)

    def set_value(self, value: str) -> None:
        self.value_label.setText(value)

    def set_subtitle(self, text: str) -> None:
        self.subtitle_label.setText(text)


class NotifyCard(QFrame):
    """通知卡片：快捷开关 + Webhook 拦截。"""

    open_config_requested = Signal(bool)
    settings_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("notifyCard")
        self._suppress = False
        self._webhook_url = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        header = QHBoxLayout()
        caption = QLabel("通知", self)
        caption.setObjectName("cardCaption")
        header.addWidget(caption)
        header.addStretch()
        self.edit_webhook_button = QPushButton("编辑 Webhook", self)
        self.edit_webhook_button.setObjectName("notifyLinkButton")
        header.addWidget(self.edit_webhook_button)
        layout.addLayout(header)

        self.master_checkbox = QCheckBox("启用通知", self)
        layout.addWidget(self.master_checkbox)

        chip_row = QHBoxLayout()
        self.desktop_chip = QPushButton("本地", self)
        self.desktop_chip.setObjectName("notifyChip")
        self.desktop_chip.setCheckable(True)
        self.feishu_chip = QPushButton("飞书", self)
        self.feishu_chip.setObjectName("notifyChip")
        self.feishu_chip.setCheckable(True)
        chip_row.addWidget(self.desktop_chip)
        chip_row.addWidget(self.feishu_chip)
        chip_row.addStretch()
        layout.addLayout(chip_row)

        self.status_label = QLabel("", self)
        self.status_label.setObjectName("notifyStatusOk")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.open_config_button = QPushButton("通知配置", self)
        self.open_config_button.setObjectName("cardActionButton")
        layout.addWidget(self.open_config_button)

        self.master_checkbox.toggled.connect(self._on_master_toggled)
        self.desktop_chip.clicked.connect(self._on_desktop_chip_clicked)
        self.feishu_chip.clicked.connect(self._on_feishu_chip_clicked)
        self.edit_webhook_button.clicked.connect(
            lambda: self.open_config_requested.emit(True)
        )
        self.open_config_button.clicked.connect(
            lambda: self.open_config_requested.emit(False)
        )

    def load_from_state(self, state: AppRuntimeStateViewModel) -> None:
        """从运行态刷新卡片 UI。"""
        self._suppress = True
        self._webhook_url = state.settings.feishu_webhook_url or ""
        enabled, desktop, feishu = parse_notify_channel(state.settings.notify_channel)
        if enabled and feishu and not self._webhook_url.strip():
            feishu = False
        self.master_checkbox.setChecked(enabled)
        self.desktop_chip.setChecked(enabled and desktop)
        self.feishu_chip.setChecked(enabled and feishu)
        self._update_controls_enabled()
        self._update_status()
        self._suppress = False

    def current_selection(self) -> tuple[bool, bool, bool]:
        """返回 (启用, 本地, 飞书)。"""
        enabled = self.master_checkbox.isChecked()
        if not enabled:
            return False, False, False
        desktop = self.desktop_chip.isChecked()
        feishu = self.feishu_chip.isChecked()
        if not desktop and not feishu:
            desktop = True
        return enabled, desktop, feishu

    def set_enabled_ui(self, enabled: bool) -> None:
        self.setDisabled(not enabled)

    def _update_controls_enabled(self) -> None:
        notify_on = self.master_checkbox.isChecked()
        self.desktop_chip.setEnabled(notify_on)
        self.feishu_chip.setEnabled(notify_on)
        show_webhook = notify_on and (
            self.feishu_chip.isChecked() or not self._webhook_url.strip()
        )
        self.edit_webhook_button.setVisible(show_webhook)

    def _update_status(self) -> None:
        enabled = self.master_checkbox.isChecked()
        desktop = self.desktop_chip.isChecked()
        feishu = self.feishu_chip.isChecked()
        webhook = self._webhook_url.strip()

        if not enabled:
            self.status_label.setObjectName("notifyStatusOk")
            self.status_label.setText("任务期间不发送通知")
            self._refresh_status_style()
            return
        if feishu and not webhook:
            self.status_label.setObjectName("notifyStatusWarn")
            self.status_label.setText("飞书已选但未配置 Webhook（无效）")
            self._refresh_status_style()
            return
        if feishu and desktop and webhook:
            self.status_label.setObjectName("notifyStatusOk")
            self.status_label.setText("本地 + 飞书 · Webhook 已配置")
            self._refresh_status_style()
            return
        if feishu and webhook:
            self.status_label.setObjectName("notifyStatusOk")
            self.status_label.setText("仅飞书 · Webhook 已配置")
            self._refresh_status_style()
            return
        if desktop:
            self.status_label.setObjectName("notifyStatusOk")
            self.status_label.setText("仅本地通知")
            self._refresh_status_style()
            return
        self.status_label.setObjectName("notifyStatusWarn")
        self.status_label.setText("请至少选择一种通知方式")
        self._refresh_status_style()

    def _refresh_status_style(self) -> None:
        style = self.status_label.style()
        style.unpolish(self.status_label)
        style.polish(self.status_label)

    def _on_master_toggled(self, checked: bool) -> None:
        if self._suppress:
            return
        if checked:
            if not self.desktop_chip.isChecked() and not self.feishu_chip.isChecked():
                self.desktop_chip.setChecked(True)
        else:
            self.desktop_chip.setChecked(False)
            self.feishu_chip.setChecked(False)
        self._update_controls_enabled()
        self._update_status()
        self._emit_settings_change()

    def _on_desktop_chip_clicked(self) -> None:
        if self._suppress or not self.master_checkbox.isChecked():
            return
        if not self.desktop_chip.isChecked():
            if not self.feishu_chip.isChecked():
                self.desktop_chip.setChecked(True)
                return
        self._update_status()
        self._emit_settings_change()

    def _on_feishu_chip_clicked(self) -> None:
        if self._suppress or not self.master_checkbox.isChecked():
            return
        if self.feishu_chip.isChecked():
            if not self._webhook_url.strip():
                self._suppress = True
                self.feishu_chip.setChecked(False)
                self._suppress = False
                self._update_controls_enabled()
                self._update_status()
                self.open_config_requested.emit(True)
                return
        else:
            if not self.desktop_chip.isChecked():
                self.desktop_chip.setChecked(True)
        self._update_controls_enabled()
        self._update_status()
        self._emit_settings_change()

    def _emit_settings_change(self) -> None:
        if self._suppress:
            return
        self.settings_changed.emit()


class ConnectBrowserWorker(QThread):
    """浏览器连接线程。"""

    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, service: ApplicationServiceProtocol) -> None:
        super().__init__()
        self._service = service

    def run(self) -> None:
        try:
            state = self._service.connect_browser()
        except Exception as error:
            self.failed.emit(str(error))
            return
        self.completed.emit(state)


class ExecuteInvitesWorker(QThread):
    """邀请执行线程。"""

    log_emitted = Signal(object)
    completed = Signal(object)
    failed = Signal(str)

    def __init__(
        self, service: ApplicationServiceProtocol, template_id: int | None
    ) -> None:
        super().__init__()
        self._service = service
        self._template_id = template_id
        self._stop_event = Event()

    def request_stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        try:
            result = self._service.execute_invites(
                template_id=self._template_id,
                stop_requested=self._stop_event.is_set,
                log_callback=self._emit_log,
            )
        except Exception as error:
            self.failed.emit(str(error))
            return
        self.completed.emit(result)

    def _emit_log(self, entry: ExecutionLogEntryViewModel) -> None:
        self.log_emitted.emit(entry)


class TemplateManagerDialog(QDialog):
    """模板管理弹窗。"""

    def __init__(
        self,
        service: ApplicationServiceProtocol,
        runtime_state: AppRuntimeStateViewModel,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self._runtime_state = runtime_state
        self._building_ui = False
        self.setWindowTitle("模板管理")
        self.resize(860, 560)
        self._build_ui()
        self._bind_events()
        self._refresh_from_state(runtime_state)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        body = QHBoxLayout()

        left = QVBoxLayout()
        self.new_template_button = QPushButton("+ 新建模板", self)
        self.new_template_button.setObjectName("primaryButton")
        left.addWidget(self.new_template_button)
        self.template_list = QListWidget(self)
        left.addWidget(self.template_list, stretch=1)
        body.addLayout(left, stretch=2)

        right = QVBoxLayout()
        right.addWidget(QLabel("模板名称", self))
        self.template_name_input = QLineEdit(self)
        self.template_name_input.setPlaceholderText("模板名称")
        right.addWidget(self.template_name_input)
        right.addWidget(QLabel("模板内容", self))
        self.template_content_edit = QPlainTextEdit(self)
        self.template_content_edit.setPlaceholderText("模板内容...")
        right.addWidget(self.template_content_edit, stretch=1)
        body.addLayout(right, stretch=5)
        root.addLayout(body, stretch=1)

        footer = QHBoxLayout()
        self.delete_button = QPushButton("删除", self)
        self.activate_button = QPushButton("设为当前激活模板", self)
        footer.addWidget(self.delete_button)
        footer.addWidget(self.activate_button)
        footer.addStretch()
        self.save_button = QPushButton("保存", self)
        self.save_button.setObjectName("primaryButton")
        footer.addWidget(self.save_button)
        root.addLayout(footer)

    def _bind_events(self) -> None:
        self.template_list.itemSelectionChanged.connect(self._on_selection_changed)
        self.new_template_button.clicked.connect(self._on_add_clicked)
        self.save_button.clicked.connect(self._on_save_clicked)
        self.delete_button.clicked.connect(self._on_delete_clicked)
        self.activate_button.clicked.connect(self._on_activate_clicked)

    def _refresh_from_state(self, state: AppRuntimeStateViewModel) -> None:
        self._runtime_state = state
        self._building_ui = True
        self.template_list.clear()
        selected_id = state.templates.selected_template_id
        active_id = state.templates.active_template_id
        for template in state.templates.templates:
            prefix = "[*] " if template.id == active_id else ""
            item = QListWidgetItem(f"{prefix}{template.name}")
            item.setData(Qt.ItemDataRole.UserRole, template.id)
            self.template_list.addItem(item)
            if template.id == selected_id:
                self.template_list.setCurrentItem(item)
        self._building_ui = False
        self._load_selected_template()

    def _current_template_id(self) -> int | None:
        item = self.template_list.currentItem()
        if item is None:
            return None
        data = item.data(Qt.ItemDataRole.UserRole)
        return int(data) if data is not None else None

    def _find_selected_template(self) -> InvitationTemplate | None:
        template_id = self._current_template_id()
        if template_id is None:
            return None
        for template in self._runtime_state.templates.templates:
            if template.id == template_id:
                return template
        return None

    def _load_selected_template(self) -> None:
        template = self._find_selected_template()
        if template is None:
            self.template_name_input.clear()
            self.template_content_edit.clear()
            return
        self.template_name_input.setText(template.name)
        self.template_content_edit.setPlainText(template.content)

    def _on_selection_changed(self) -> None:
        if self._building_ui:
            return
        template_id = self._current_template_id()
        try:
            self._runtime_state = self.service.select_template(template_id)
        except Exception as error:
            QMessageBox.critical(self, "错误", str(error))
            return
        self._load_selected_template()

    def _on_add_clicked(self) -> None:
        try:
            self._runtime_state = self.service.add_template()
        except Exception as error:
            QMessageBox.critical(self, "错误", str(error))
            return
        self._refresh_from_state(self._runtime_state)

    def _on_save_clicked(self) -> None:
        template_id = self._current_template_id()
        if template_id is None:
            QMessageBox.warning(self, "提示", "请先选择一个模板。")
            return
        try:
            self._runtime_state = self.service.save_template(
                template_id=template_id,
                name=self.template_name_input.text(),
                content=self.template_content_edit.toPlainText(),
            )
        except Exception as error:
            QMessageBox.critical(self, "错误", str(error))
            return
        self._refresh_from_state(self._runtime_state)

    def _on_delete_clicked(self) -> None:
        template = self._find_selected_template()
        if template is None:
            QMessageBox.warning(self, "提示", "请先选择要删除的模板。")
            return
        confirmed = QMessageBox.question(
            self,
            "确认删除",
            f"确定要删除模板“{template.name}”吗？",
        )
        if confirmed != QMessageBox.StandardButton.Yes:
            return
        try:
            self._runtime_state = self.service.delete_template(template.id)
        except Exception as error:
            QMessageBox.critical(self, "错误", str(error))
            return
        self._refresh_from_state(self._runtime_state)

    def _on_activate_clicked(self) -> None:
        template = self._find_selected_template()
        if template is None:
            QMessageBox.warning(self, "提示", "请先选择一个模板。")
            return
        try:
            self._runtime_state = self.service.activate_template(template.id)
        except Exception as error:
            QMessageBox.critical(self, "错误", str(error))
            return
        self._refresh_from_state(self._runtime_state)


class NotifyConfigDialog(QDialog):
    """通知配置弹窗。"""

    def __init__(
        self,
        service: ApplicationServiceProtocol,
        runtime_state: AppRuntimeStateViewModel,
        parent: QWidget | None = None,
        *,
        focus_webhook: bool = False,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self._runtime_state = runtime_state
        self._focus_webhook = focus_webhook
        self.setWindowTitle("通知配置")
        self.resize(520, 420)
        self._build_ui()
        self._load_from_state(runtime_state)
        if focus_webhook:
            QTimer.singleShot(0, self._focus_webhook_field)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        self.enable_notify_checkbox = QCheckBox("启用通知", self)
        root.addWidget(self.enable_notify_checkbox)

        card = QFrame(self)
        card.setObjectName("dialogCard")
        card_layout = QVBoxLayout(card)
        card_layout.addWidget(QLabel("通知渠道", card))

        desktop_row = QHBoxLayout()
        self.desktop_checkbox = QCheckBox("本地桌面通知", card)
        desktop_row.addWidget(self.desktop_checkbox)
        desktop_row.addStretch()
        card_layout.addLayout(desktop_row)

        feishu_row = QHBoxLayout()
        self.feishu_checkbox = QCheckBox("飞书", card)
        feishu_row.addWidget(self.feishu_checkbox)
        feishu_row.addStretch()
        card_layout.addLayout(feishu_row)

        card_layout.addWidget(QLabel("Webhook URL", card))
        self.webhook_input = QLineEdit(card)
        self.webhook_input.setPlaceholderText(
            "https://open.feishu.cn/open-apis/bot/v2/hook/..."
        )
        card_layout.addWidget(self.webhook_input)
        self.webhook_error_label = QLabel("开启飞书通知时必须填写 Webhook URL。", card)
        self.webhook_error_label.setObjectName("notifyStatusWarn")
        self.webhook_error_label.setWordWrap(True)
        self.webhook_error_label.hide()
        card_layout.addWidget(self.webhook_error_label)
        root.addWidget(card)

        root.addWidget(
            QLabel("任务完成或失败时发送通知。", self)
        )

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        self.save_button = self.button_box.button(
            QDialogButtonBox.StandardButton.Save
        )
        self.button_box.accepted.connect(self._on_save)
        self.button_box.rejected.connect(self.reject)
        root.addWidget(self.button_box)

        self.enable_notify_checkbox.toggled.connect(self._on_form_changed)
        self.desktop_checkbox.toggled.connect(self._on_form_changed)
        self.feishu_checkbox.toggled.connect(self._on_form_changed)
        self.webhook_input.textChanged.connect(self._on_form_changed)

    def _focus_webhook_field(self) -> None:
        self.webhook_input.setFocus(Qt.FocusReason.OtherFocusReason)
        self.webhook_input.selectAll()

    def _load_from_state(self, state: AppRuntimeStateViewModel) -> None:
        enabled, desktop, feishu = parse_notify_channel(state.settings.notify_channel)
        if enabled and feishu and not (state.settings.feishu_webhook_url or "").strip():
            feishu = False
        self.enable_notify_checkbox.setChecked(enabled)
        self.desktop_checkbox.setChecked(enabled and desktop)
        self.feishu_checkbox.setChecked(enabled and feishu)
        self.webhook_input.setText(state.settings.feishu_webhook_url or "")
        self._on_form_changed()

    def _on_form_changed(self) -> None:
        enabled = self.enable_notify_checkbox.isChecked()
        self.desktop_checkbox.setEnabled(enabled)
        self.feishu_checkbox.setEnabled(enabled)
        feishu_on = enabled and self.feishu_checkbox.isChecked()
        self.webhook_input.setEnabled(feishu_on)
        webhook = self.webhook_input.text().strip()
        show_error = feishu_on and not webhook
        self.webhook_error_label.setVisible(show_error)
        if self.save_button is not None:
            self.save_button.setEnabled(not show_error)

    def _on_save(self) -> None:
        enabled = self.enable_notify_checkbox.isChecked()
        desktop = self.desktop_checkbox.isChecked()
        feishu = self.feishu_checkbox.isChecked()
        webhook = self.webhook_input.text().strip()

        if enabled and not desktop and not feishu:
            desktop = True
        channel = build_notify_channel(enabled, desktop, feishu)

        if feishu and not webhook:
            QMessageBox.warning(self, "提示", "开启飞书通知时必须填写 Webhook URL。")
            return

        try:
            self._runtime_state = self.service.set_notify_settings(channel, webhook)
        except Exception as error:
            QMessageBox.critical(self, "错误", str(error))
            return
        self.accept()

    def runtime_state(self) -> AppRuntimeStateViewModel:
        return self._runtime_state


class MainWindow(QMainWindow):
    """PySide6 主窗口。"""

    def __init__(
        self,
        service: ApplicationServiceProtocol | None = None,
        *,
        auto_connect_browser: bool = True,
    ) -> None:
        super().__init__()
        self.service = service or AwinApplicationService()
        self._auto_connect_browser = auto_connect_browser
        self._runtime_state = self.service.bootstrap()
        self._connect_worker: ConnectBrowserWorker | None = None
        self._execute_worker: ExecuteInvitesWorker | None = None
        self._setup_window()
        self._build_ui()
        self._bind_events()
        self._apply_state(self._runtime_state)
        self._load_history_logs()
        if self._auto_connect_browser and not self._runtime_state.connection.browser_connected:
            QTimer.singleShot(0, self._start_auto_connect)

    def _setup_window(self) -> None:
        self.setWindowTitle("AWIN RPA - PySide6 桌面版")
        self.resize(1280, 820)

    def _build_ui(self) -> None:
        central = QWidget(self)
        central.setObjectName("centralRoot")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(16)

        header = QFrame(central)
        header.setObjectName("headerBar")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(16, 12, 16, 12)
        title = QLabel("AWIN RPA", header)
        title.setObjectName("appTitle")
        header_layout.addWidget(title)
        header_layout.addStretch()
        self.browser_button = QPushButton("连接浏览器", header)
        self.browser_button.setObjectName("browserDefaultButton")
        header_layout.addWidget(self.browser_button)
        self.settings_button = QPushButton("设置", header)
        self.settings_button.setObjectName("cardActionButton")
        header_layout.addWidget(self.settings_button)
        root.addWidget(header)

        cards_row = QHBoxLayout()
        cards_row.setSpacing(12)
        self.sent_card = StatCard("已发送记录", "重置", central)
        self.template_card = StatCard("当前激活模板", "管理", central)
        self.notify_card = NotifyCard(central)
        cards_row.addWidget(self.sent_card, stretch=1)
        cards_row.addWidget(self.template_card, stretch=1)
        cards_row.addWidget(self.notify_card, stretch=1)
        root.addLayout(cards_row)

        content_row = QHBoxLayout()
        content_row.setSpacing(12)

        exec_panel = QFrame(central)
        exec_panel.setObjectName("panelCard")
        exec_layout = QVBoxLayout(exec_panel)
        exec_layout.setContentsMargins(16, 16, 16, 16)
        exec_title = QLabel("执行任务 (Send Proposals)", exec_panel)
        exec_title.setObjectName("panelTitle")
        exec_layout.addWidget(exec_title)
        exec_layout.addWidget(QLabel("发送数量 (Max Count):", exec_panel))
        self.send_count_input = QSpinBox(exec_panel)
        self.send_count_input.setRange(1, 9999)
        exec_layout.addWidget(self.send_count_input)
        hint = QLabel(
            "提示：请先连接浏览器并激活模板，再点击开始执行。",
            exec_panel,
        )
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        exec_layout.addWidget(hint)
        exec_layout.addStretch()
        self.execute_button = QPushButton("开始执行", exec_panel)
        self.execute_button.setObjectName("primaryButton")
        exec_layout.addWidget(self.execute_button)
        content_row.addWidget(exec_panel, stretch=3)

        log_panel = QFrame(central)
        log_panel.setObjectName("panelCard")
        log_layout = QVBoxLayout(log_panel)
        log_layout.setContentsMargins(16, 16, 16, 16)
        log_title = QLabel("执行日志 (Console)", log_panel)
        log_title.setObjectName("panelTitle")
        log_layout.addWidget(log_title)
        self.log_output = QPlainTextEdit(log_panel)
        self.log_output.setObjectName("logOutput")
        self.log_output.setReadOnly(True)
        log_layout.addWidget(self.log_output, stretch=1)
        self.stop_button = QPushButton("强制停止", log_panel)
        self.stop_button.setObjectName("dangerButton")
        log_layout.addWidget(self.stop_button)
        content_row.addWidget(log_panel, stretch=2)
        root.addLayout(content_row, stretch=1)

    def _bind_events(self) -> None:
        self.browser_button.clicked.connect(self._on_browser_button_clicked)
        self.settings_button.clicked.connect(self._on_settings_menu)
        if self.sent_card.action_button:
            self.sent_card.action_button.clicked.connect(self._on_reset_sent_clicked)
        if self.template_card.action_button:
            self.template_card.action_button.clicked.connect(self._on_manage_templates)
        self.notify_card.open_config_requested.connect(self._on_open_notify_config)
        self.notify_card.settings_changed.connect(self._on_notify_card_changed)
        self.send_count_input.editingFinished.connect(self._on_send_count_changed)
        self.execute_button.clicked.connect(self._on_execute_clicked)
        self.stop_button.clicked.connect(self._on_stop_clicked)

    def _apply_state(self, state: AppRuntimeStateViewModel) -> None:
        self._runtime_state = state
        self.sent_card.set_value(str(state.connection.clicked_records_count))
        self.sent_card.set_subtitle("Send")
        self._update_template_card(state)
        self.notify_card.load_from_state(state)
        self.send_count_input.setValue(state.settings.send_count)

        is_connecting = (
            self._connect_worker is not None and self._connect_worker.isRunning()
        )
        connected = state.connection.browser_connected
        if is_connecting:
            self.browser_button.setText("连接中...")
            self.browser_button.setObjectName("browserDefaultButton")
            self.browser_button.setStyle(self.style())
            self.browser_button.setDisabled(True)
        elif connected:
            self.browser_button.setText("浏览器已连接")
            self.browser_button.setObjectName("browserConnectedButton")
            self.browser_button.setStyle(self.style())
            self.browser_button.setDisabled(False)
        else:
            self.browser_button.setText("连接浏览器")
            self.browser_button.setObjectName("browserDefaultButton")
            self.browser_button.setStyle(self.style())
            self.browser_button.setDisabled(False)

        running = state.execution.is_running
        self.execute_button.setText("停止执行" if running else "开始执行")
        self.execute_button.setDisabled(False)
        self.stop_button.setDisabled(not running)
        self._set_dashboard_enabled(not running)

    def _set_dashboard_enabled(self, enabled: bool) -> None:
        self.send_count_input.setDisabled(not enabled)
        if self.sent_card.action_button:
            self.sent_card.action_button.setDisabled(not enabled)
        if self.template_card.action_button:
            self.template_card.action_button.setDisabled(not enabled)
        self.notify_card.set_enabled_ui(enabled)
        self.settings_button.setDisabled(not enabled)

    def _update_template_card(self, state: AppRuntimeStateViewModel) -> None:
        active_id = state.templates.active_template_id
        if active_id is None:
            self.template_card.set_value("—")
            self.template_card.set_subtitle("未配置（空模板，点击管理）")
            return
        for template in state.templates.templates:
            if template.id == active_id:
                self.template_card.set_value(template.name)
                preview = template.content.strip().replace("\n", " ")
                if len(preview) > 48:
                    preview = preview[:48] + "..."
                self.template_card.set_subtitle(preview or "（空模板）")
                return
        self.template_card.set_value("—")
        self.template_card.set_subtitle("未配置（空模板，点击管理）")

    def _on_notify_card_changed(self) -> None:
        if self._runtime_state.execution.is_running:
            return
        enabled, desktop, feishu = self.notify_card.current_selection()
        channel = build_notify_channel(enabled, desktop, feishu)
        webhook = self._runtime_state.settings.feishu_webhook_url or ""
        try:
            self._runtime_state = self.service.set_notify_settings(channel, webhook)
        except Exception as error:
            self._show_error(str(error))
            self.notify_card.load_from_state(self.service.get_state())
            return
        self.notify_card.load_from_state(self._runtime_state)

    def _on_send_count_changed(self) -> None:
        try:
            self._runtime_state = self.service.set_send_count(
                self.send_count_input.value()
            )
        except Exception as error:
            self._show_error(str(error))

    def _on_settings_menu(self) -> None:
        self._on_open_notify_config()

    def _on_manage_templates(self) -> None:
        if self._runtime_state.execution.is_running:
            self._show_warning("执行期间不允许修改模板。")
            return
        dialog = TemplateManagerDialog(
            self.service, self._runtime_state, parent=self
        )
        dialog.exec()
        self._apply_state(self.service.get_state())

    def _on_open_notify_config(self, focus_webhook: bool = False) -> None:
        if self._runtime_state.execution.is_running:
            self._show_warning("执行期间不允许修改通知配置。")
            return
        dialog = NotifyConfigDialog(
            self.service,
            self._runtime_state,
            parent=self,
            focus_webhook=focus_webhook,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._apply_state(dialog.runtime_state())
        else:
            self._apply_state(self.service.get_state())

    def _on_reset_sent_clicked(self) -> None:
        if self._runtime_state.connection.clicked_records_count <= 0:
            self._show_warning("当前没有已发送记录。")
            return
        confirmed = QMessageBox.question(
            self,
            "确认重置",
            "确认清空已发送记录吗？",
        )
        if confirmed != QMessageBox.StandardButton.Yes:
            return
        try:
            cleared_count, state = self.service.reset_clicked_ids()
        except Exception as error:
            self._show_error(str(error))
            return
        self._apply_state(state)
        self._append_log("success", f"已清除 {cleared_count} 条已发送记录")

    def _start_auto_connect(self) -> None:
        self._append_log("info", "正在自动连接浏览器...")
        self._start_connect_worker()

    def _on_browser_button_clicked(self) -> None:
        if (
            self._runtime_state.connection.browser_connected
            and self.service.get_state().connection.browser_connected
        ):
            self._show_warning("浏览器已连接。")
            return
        self._start_connect_worker()

    def _start_connect_worker(self) -> None:
        if self._connect_worker is not None and self._connect_worker.isRunning():
            return
        self._connect_worker = ConnectBrowserWorker(self.service)
        self._connect_worker.completed.connect(self._on_connect_browser_completed)
        self._connect_worker.failed.connect(self._on_connect_browser_failed)
        self._connect_worker.finished.connect(self._on_connect_browser_finished)
        self._apply_state(self._runtime_state)
        self._append_log("info", "正在连接浏览器...")
        self._connect_worker.start()

    def _on_connect_browser_completed(self, state: AppRuntimeStateViewModel) -> None:
        self._apply_state(state)
        self._append_log("success", "浏览器连接成功")

    def _on_connect_browser_failed(self, message: str) -> None:
        self._apply_state(self.service.get_state())
        self._append_log("error", message)
        self._show_error(message)

    def _on_connect_browser_finished(self) -> None:
        self._apply_state(self.service.get_state())

    def _on_execute_clicked(self) -> None:
        if self._execute_worker is not None and self._execute_worker.isRunning():
            self._on_stop_clicked()
            return

        if not self._runtime_state.connection.browser_connected:
            self._show_warning("请先连接浏览器。")
            return

        active_id = self._runtime_state.templates.active_template_id
        if active_id is None:
            self._show_warning("请先在模板管理中激活一个模板。")
            return

        try:
            self._runtime_state = self.service.set_send_count(
                self.send_count_input.value()
            )
        except Exception as error:
            self._show_error(str(error))
            return

        self._runtime_state.execution.is_running = True
        self._apply_state(self._runtime_state)
        self._execute_worker = ExecuteInvitesWorker(self.service, active_id)
        self._execute_worker.log_emitted.connect(self._on_execution_log_emitted)
        self._execute_worker.completed.connect(self._on_execution_completed)
        self._execute_worker.failed.connect(self._on_execution_failed)
        self._execute_worker.start()

    def _on_stop_clicked(self) -> None:
        if self._execute_worker is not None and self._execute_worker.isRunning():
            self._execute_worker.request_stop()
            self.execute_button.setDisabled(True)
            self.execute_button.setText("正在停止...")
            self._append_log("warning", "正在请求停止任务...")

    def _on_execution_log_emitted(self, entry: ExecutionLogEntryViewModel) -> None:
        self._append_log(entry.level, entry.message, entry.timestamp)

    def _on_execution_completed(self, result: ExecutionResultViewModel) -> None:
        self._apply_state(self.service.get_state())
        if result.completed:
            self._show_info(result.last_message)
        elif result.stopped:
            self._show_warning(result.last_message)

    def _on_execution_failed(self, message: str) -> None:
        self._apply_state(self.service.get_state())
        self._show_error(message)

    def _append_log(
        self, level: str, message: str, timestamp: str | None = None
    ) -> None:
        final_timestamp = timestamp or datetime.now().strftime("%H:%M:%S")
        self.log_output.appendPlainText(
            f"[{final_timestamp}] {level.upper()}: {message}"
        )

    def _load_history_logs(self) -> None:
        """加载本地 file.log 历史日志到日志面板。"""
        entries = _load_file_log_lines(max_lines=200)
        for timestamp, level, message in entries:
            self._append_log(level, message, timestamp)

    def _show_info(self, message: str) -> None:
        QMessageBox.information(self, "提示", message)

    def _show_warning(self, message: str) -> None:
        QMessageBox.warning(self, "提示", message)

    def _show_error(self, message: str) -> None:
        QMessageBox.critical(self, "错误", message)


def main() -> int:
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        app = QApplication(sys.argv)
    apply_app_styles(app)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
