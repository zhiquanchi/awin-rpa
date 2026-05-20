from __future__ import annotations

import sys
from datetime import datetime
from threading import Event

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFormLayout,
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
    QSplitter,
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


class ConnectBrowserWorker(QThread):
    """浏览器连接线程。"""

    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, service: ApplicationServiceProtocol) -> None:
        super().__init__()
        self._service = service

    def run(self) -> None:
        """在线程中执行浏览器连接。"""
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
        """请求停止当前执行线程。"""
        self._stop_event.set()

    def run(self) -> None:
        """在线程中执行邀请流程。"""
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
        """向界面发出一条日志事件。"""
        self.log_emitted.emit(entry)


class MainWindow(QMainWindow):
    """PySide6 主窗口。"""

    def __init__(self, service: ApplicationServiceProtocol | None = None) -> None:
        super().__init__()
        self.service = service or AwinApplicationService()
        self._runtime_state = self.service.bootstrap()
        self._connect_worker: ConnectBrowserWorker | None = None
        self._execute_worker: ExecuteInvitesWorker | None = None
        self._building_ui = False
        self._setup_window()
        self._build_ui()
        self._bind_events()
        self._apply_state(self._runtime_state)

    def _setup_window(self) -> None:
        """初始化主窗口基础属性。"""
        self.setWindowTitle("AWIN RPA - PySide6")
        self.resize(1280, 820)

    def _build_ui(self) -> None:
        """构建主窗口布局。"""
        self._building_ui = True
        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        splitter = QSplitter(Qt.Horizontal, self)
        main_layout.addWidget(splitter)

        left_panel = QWidget(splitter)
        left_layout = QVBoxLayout(left_panel)
        left_layout.addWidget(QLabel("模板列表"))
        self.template_list = QListWidget(left_panel)
        left_layout.addWidget(self.template_list)

        self.template_name_input = QLineEdit(left_panel)
        self.template_content_edit = QPlainTextEdit(left_panel)
        left_layout.addWidget(QLabel("模板名称"))
        left_layout.addWidget(self.template_name_input)
        left_layout.addWidget(QLabel("模板内容"))
        left_layout.addWidget(self.template_content_edit)

        template_button_row = QHBoxLayout()
        self.add_template_button = QPushButton("新增模板", left_panel)
        self.save_template_button = QPushButton("保存模板", left_panel)
        self.delete_template_button = QPushButton("删除模板", left_panel)
        self.activate_template_button = QPushButton("激活模板", left_panel)
        template_button_row.addWidget(self.add_template_button)
        template_button_row.addWidget(self.save_template_button)
        template_button_row.addWidget(self.delete_template_button)
        template_button_row.addWidget(self.activate_template_button)
        left_layout.addLayout(template_button_row)

        right_panel = QWidget(splitter)
        right_layout = QVBoxLayout(right_panel)

        settings_form = QFormLayout()
        self.send_count_input = QSpinBox(right_panel)
        self.send_count_input.setRange(1, 9999)
        self.notify_channel_combo = QComboBox(right_panel)
        self.notify_channel_combo.addItem("仅本地通知", "desktop")
        self.notify_channel_combo.addItem("仅飞书通知", "feishu")
        self.notify_channel_combo.addItem("本地 + 飞书", "both")
        self.notify_channel_combo.addItem("不通知", "none")
        self.webhook_input = QLineEdit(right_panel)
        settings_form.addRow("发送数量", self.send_count_input)
        settings_form.addRow("通知渠道", self.notify_channel_combo)
        settings_form.addRow("飞书 Webhook", self.webhook_input)
        right_layout.addLayout(settings_form)

        settings_button_row = QHBoxLayout()
        self.save_settings_button = QPushButton("保存设置", right_panel)
        self.sync_remote_button = QPushButton("重新同步", right_panel)
        settings_button_row.addWidget(self.save_settings_button)
        settings_button_row.addWidget(self.sync_remote_button)
        right_layout.addLayout(settings_button_row)

        self.browser_status_label = QLabel("未连接浏览器", right_panel)
        self.clicked_count_label = QLabel("已点击记录：0", right_panel)
        right_layout.addWidget(self.browser_status_label)
        right_layout.addWidget(self.clicked_count_label)

        action_button_row = QHBoxLayout()
        self.connect_button = QPushButton("连接浏览器", right_panel)
        self.reset_button = QPushButton("重置记录", right_panel)
        self.execute_button = QPushButton("开始执行", right_panel)
        action_button_row.addWidget(self.connect_button)
        action_button_row.addWidget(self.reset_button)
        action_button_row.addWidget(self.execute_button)
        right_layout.addLayout(action_button_row)

        right_layout.addWidget(QLabel("执行日志"))
        self.log_output = QPlainTextEdit(right_panel)
        self.log_output.setReadOnly(True)
        right_layout.addWidget(self.log_output)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        self._building_ui = False

    def _bind_events(self) -> None:
        """绑定界面事件。"""
        self.template_list.itemSelectionChanged.connect(self._on_template_selection_changed)
        self.add_template_button.clicked.connect(self._on_add_template_clicked)
        self.save_template_button.clicked.connect(self._on_save_template_clicked)
        self.delete_template_button.clicked.connect(self._on_delete_template_clicked)
        self.activate_template_button.clicked.connect(self._on_activate_template_clicked)
        self.save_settings_button.clicked.connect(self._on_save_settings_clicked)
        self.sync_remote_button.clicked.connect(self._on_sync_remote_clicked)
        self.connect_button.clicked.connect(self._on_connect_browser_clicked)
        self.reset_button.clicked.connect(self._on_reset_clicked)
        self.execute_button.clicked.connect(self._on_execute_clicked)

    def _apply_state(self, state: AppRuntimeStateViewModel) -> None:
        """把运行态刷新到界面。"""
        self._runtime_state = state
        self._populate_template_list()
        self.send_count_input.setValue(state.settings.send_count)
        self._set_notify_channel(state.settings.notify_channel)
        self.webhook_input.setText(state.settings.feishu_webhook_url)
        self.browser_status_label.setText(state.connection.status_text)
        self.clicked_count_label.setText(
            f"已点击记录：{state.connection.clicked_records_count}"
        )
        self.connect_button.setDisabled(state.connection.browser_connected)
        self.execute_button.setText("停止执行" if state.execution.is_running else "开始执行")

    def _populate_template_list(self) -> None:
        """刷新模板列表。"""
        self.template_list.blockSignals(True)
        self.template_list.clear()
        selected_template_id = self._runtime_state.templates.selected_template_id
        active_template_id = self._runtime_state.templates.active_template_id

        for template in self._runtime_state.templates.templates:
            prefix = "[*] " if template.id == active_template_id else ""
            item = QListWidgetItem(f"{prefix}{template.name}")
            item.setData(Qt.UserRole, template.id)
            self.template_list.addItem(item)
            if template.id == selected_template_id:
                self.template_list.setCurrentItem(item)

        self.template_list.blockSignals(False)
        self._load_selected_template()

    def _load_selected_template(self) -> None:
        """把当前选中的模板加载到编辑区。"""
        template = self._find_selected_template()
        if template is None:
            self.template_name_input.clear()
            self.template_content_edit.clear()
            return

        self.template_name_input.setText(template.name)
        self.template_content_edit.setPlainText(template.content)

    def _find_selected_template(self) -> InvitationTemplate | None:
        """返回当前选中的模板模型。"""
        template_id = self._current_template_id()
        if template_id is None:
            return None

        for template in self._runtime_state.templates.templates:
            if template.id == template_id:
                return template
        return None

    def _current_template_id(self) -> int | None:
        """读取当前列表选中的模板 ID。"""
        current_item = self.template_list.currentItem()
        if current_item is None:
            return None
        data = current_item.data(Qt.UserRole)
        return int(data) if data is not None else None

    def _set_notify_channel(self, channel: str) -> None:
        """把通知渠道同步到下拉框。"""
        index = self.notify_channel_combo.findData(channel)
        self.notify_channel_combo.setCurrentIndex(index if index >= 0 else 0)

    def _on_template_selection_changed(self) -> None:
        """处理模板选择变化。"""
        if self._building_ui:
            return

        template_id = self._current_template_id()
        try:
            self._runtime_state = self.service.select_template(template_id)
        except Exception as error:
            self._show_error(str(error))
            return
        self._load_selected_template()

    def _on_add_template_clicked(self) -> None:
        """新增模板。"""
        try:
            state = self.service.add_template()
        except Exception as error:
            self._show_error(str(error))
            return

        self._apply_state(state)
        self._append_log("success", "已新增模板")

    def _on_save_template_clicked(self) -> None:
        """保存当前模板。"""
        template_id = self._current_template_id()
        if template_id is None:
            self._show_warning("请先选择一个模板。")
            return

        try:
            state = self.service.save_template(
                template_id=template_id,
                name=self.template_name_input.text(),
                content=self.template_content_edit.toPlainText(),
            )
        except Exception as error:
            self._show_error(str(error))
            return

        self._apply_state(state)
        self._append_log("success", "模板保存成功")

    def _on_delete_template_clicked(self) -> None:
        """删除当前模板。"""
        template = self._find_selected_template()
        if template is None:
            self._show_warning("请先选择要删除的模板。")
            return

        confirmed = QMessageBox.question(
            self,
            "确认删除",
            f"确定要删除模板“{template.name}”吗？",
        )
        if confirmed != QMessageBox.StandardButton.Yes:
            return

        try:
            state = self.service.delete_template(template.id)
        except Exception as error:
            self._show_error(str(error))
            return

        self._apply_state(state)
        self._append_log("info", f"已删除模板：{template.name}")

    def _on_activate_template_clicked(self) -> None:
        """激活当前模板。"""
        template = self._find_selected_template()
        if template is None:
            self._show_warning("请先选择一个模板。")
            return

        try:
            state = self.service.activate_template(template.id)
        except Exception as error:
            self._show_error(str(error))
            return

        self._apply_state(state)
        self._append_log("success", f"已激活模板：{template.name}")

    def _on_save_settings_clicked(self) -> None:
        """保存设置。"""
        notify_channel = self.notify_channel_combo.currentData()
        webhook = self.webhook_input.text().strip()

        try:
            state = self.service.set_send_count(self.send_count_input.value())
            state = self.service.set_notify_settings(notify_channel, webhook)
        except Exception as error:
            self._show_error(str(error))
            return

        self._apply_state(state)
        self._append_log("success", "设置保存成功")

    def _on_sync_remote_clicked(self) -> None:
        """重新从远端拉取配置。"""
        try:
            state = self.service.refresh_from_remote()
        except Exception as error:
            self._show_error(str(error))
            return

        self._apply_state(state)
        self._append_log("info", "已重新同步远端配置")

    def _on_connect_browser_clicked(self) -> None:
        """启动浏览器连接线程。"""
        if self._connect_worker is not None and self._connect_worker.isRunning():
            return

        self._connect_worker = ConnectBrowserWorker(self.service)
        self._connect_worker.completed.connect(self._on_connect_browser_completed)
        self._connect_worker.failed.connect(self._on_connect_browser_failed)
        self._append_log("info", "正在连接浏览器...")
        self._connect_worker.start()

    def _on_connect_browser_completed(self, state: AppRuntimeStateViewModel) -> None:
        """处理浏览器连接成功。"""
        self._apply_state(state)
        self._append_log("success", "浏览器连接成功")

    def _on_connect_browser_failed(self, message: str) -> None:
        """处理浏览器连接失败。"""
        self._apply_state(self.service.get_state())
        self._show_error(message)
        self._append_log("error", message)

    def _on_reset_clicked(self) -> None:
        """重置已点击记录。"""
        if self._runtime_state.connection.clicked_records_count <= 0:
            self._show_warning("当前没有已点击记录。")
            return

        confirmed = QMessageBox.question(
            self,
            "确认重置",
            "确认清空已点击记录吗？",
        )
        if confirmed != QMessageBox.StandardButton.Yes:
            return

        try:
            cleared_count, state = self.service.reset_clicked_ids()
        except Exception as error:
            self._show_error(str(error))
            return

        self._apply_state(state)
        self._append_log("success", f"已清除 {cleared_count} 条已点击记录")

    def _on_execute_clicked(self) -> None:
        """开始或停止执行。"""
        if self._execute_worker is not None and self._execute_worker.isRunning():
            self._execute_worker.request_stop()
            self.execute_button.setDisabled(True)
            self.execute_button.setText("正在停止...")
            return

        template_id = self._current_template_id()
        try:
            self._runtime_state = self.service.select_template(template_id)
        except Exception as error:
            self._show_error(str(error))
            return

        self._apply_state(self.service.get_state())
        self._execute_worker = ExecuteInvitesWorker(self.service, template_id)
        self._execute_worker.log_emitted.connect(self._on_execution_log_emitted)
        self._execute_worker.completed.connect(self._on_execution_completed)
        self._execute_worker.failed.connect(self._on_execution_failed)
        self.execute_button.setText("停止执行")
        self._execute_worker.start()

    def _on_execution_log_emitted(self, entry: ExecutionLogEntryViewModel) -> None:
        """处理执行线程产生的日志。"""
        self._append_log(entry.level, entry.message, entry.timestamp)

    def _on_execution_completed(self, result: ExecutionResultViewModel) -> None:
        """处理执行完成。"""
        self.execute_button.setDisabled(False)
        self._apply_state(self.service.get_state())
        if result.completed:
            self._show_info(result.last_message)
        elif result.stopped:
            self._show_warning(result.last_message)

    def _on_execution_failed(self, message: str) -> None:
        """处理执行失败。"""
        self.execute_button.setDisabled(False)
        self._apply_state(self.service.get_state())
        self._show_error(message)

    def _append_log(
        self, level: str, message: str, timestamp: str | None = None
    ) -> None:
        """向日志面板追加一条消息。"""
        final_timestamp = timestamp or datetime.now().strftime("%H:%M:%S")
        prefix = f"[{final_timestamp}] " if final_timestamp else ""
        self.log_output.appendPlainText(f"{prefix}{level.upper()}: {message}")

    def _show_info(self, message: str) -> None:
        """显示信息提示框。"""
        QMessageBox.information(self, "提示", message)

    def _show_warning(self, message: str) -> None:
        """显示警告提示框。"""
        QMessageBox.warning(self, "提示", message)

    def _show_error(self, message: str) -> None:
        """显示错误提示框。"""
        QMessageBox.critical(self, "错误", message)


def main() -> int:
    """启动 PySide6 应用。"""
    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
