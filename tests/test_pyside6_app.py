from __future__ import annotations

from typing import Any

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from app_interfaces import (
    AppRuntimeStateViewModel,
    BrowserConnectionViewModel,
    ExecutionResultViewModel,
    ExecutionStateViewModel,
    InvitationTemplate,
    SettingsViewModel,
    TemplatePanelViewModel,
)
from pyside6_app import ExecuteInvitesWorker, MainWindow


class FakeApplicationService:
    """测试用应用服务。"""

    def __init__(self) -> None:
        self.connect_calls = 0
        self.add_calls = 0
        self.save_setting_calls = 0
        self.state = AppRuntimeStateViewModel(
            templates=TemplatePanelViewModel(
                templates=[InvitationTemplate(id=1, name="模板1", content="内容1")],
                selected_template_id=1,
                active_template_id=1,
            ),
            settings=SettingsViewModel(
                send_count=3,
                notify_channel="desktop",
                feishu_webhook_url="",
                sync_url="http://localhost:8080",
            ),
            connection=BrowserConnectionViewModel(
                browser_connected=False,
                clicked_records_count=0,
                status_text="未连接浏览器",
                last_error=None,
            ),
            execution=ExecutionStateViewModel(),
        )

    def bootstrap(self) -> AppRuntimeStateViewModel:
        """返回初始状态。"""
        return self.state

    def refresh_from_remote(self) -> AppRuntimeStateViewModel:
        """返回当前状态。"""
        return self.state

    def get_state(self) -> AppRuntimeStateViewModel:
        """返回当前状态。"""
        return self.state

    def select_template(self, template_id: int | None) -> AppRuntimeStateViewModel:
        """切换选中模板。"""
        self.state.templates.selected_template_id = template_id
        return self.state

    def add_template(
        self, name: str | None = None, content: str | None = None
    ) -> AppRuntimeStateViewModel:
        """新增模板。"""
        self.add_calls += 1
        new_template = InvitationTemplate(id=2, name="模板2", content="内容2")
        self.state.templates.templates.append(new_template)
        self.state.templates.selected_template_id = 2
        return self.state

    def save_template(
        self, template_id: int, name: str | None, content: str
    ) -> AppRuntimeStateViewModel:
        """保存模板。"""
        for template in self.state.templates.templates:
            if template.id == template_id:
                template.name = name or template.name
                template.content = content
        return self.state

    def delete_template(self, template_id: int) -> AppRuntimeStateViewModel:
        """删除模板。"""
        self.state.templates.templates = [
            template
            for template in self.state.templates.templates
            if template.id != template_id
        ]
        self.state.templates.selected_template_id = (
            self.state.templates.templates[0].id
            if self.state.templates.templates
            else None
        )
        return self.state

    def activate_template(self, template_id: int) -> AppRuntimeStateViewModel:
        """激活模板。"""
        self.state.templates.active_template_id = template_id
        return self.state

    def set_send_count(self, value: int) -> AppRuntimeStateViewModel:
        """保存发送数量。"""
        self.save_setting_calls += 1
        self.state.settings.send_count = value
        return self.state

    def set_notify_settings(
        self, channel: str, webhook_url: str
    ) -> AppRuntimeStateViewModel:
        """保存通知设置。"""
        self.state.settings.notify_channel = channel
        self.state.settings.feishu_webhook_url = webhook_url
        return self.state

    def connect_browser(self) -> AppRuntimeStateViewModel:
        """模拟浏览器连接。"""
        self.connect_calls += 1
        self.state.connection.browser_connected = True
        self.state.connection.status_text = "浏览器已连接，等待执行..."
        return self.state

    def reset_clicked_ids(self) -> tuple[int, AppRuntimeStateViewModel]:
        """模拟重置记录。"""
        self.state.connection.clicked_records_count = 0
        return 0, self.state

    def execute_invites(
        self, template_id: int | None, stop_requested=None, log_callback=None
    ) -> ExecutionResultViewModel:
        """返回固定执行结果。"""
        self.state.execution.is_running = False
        return ExecutionResultViewModel(
            sent_count=0,
            target_count=self.state.settings.send_count,
            stopped=False,
            completed=True,
            last_message="执行完成",
        )


@pytest.fixture(autouse=True)
def patch_message_box(monkeypatch: pytest.MonkeyPatch) -> None:
    """避免测试期间弹窗阻塞。"""
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda *args, **kwargs: QMessageBox.StandardButton.Ok,
    )
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *args, **kwargs: QMessageBox.StandardButton.Ok,
    )
    monkeypatch.setattr(
        QMessageBox,
        "critical",
        lambda *args, **kwargs: QMessageBox.StandardButton.Ok,
    )
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )


def test_main_window_initializes_with_service_state(qtbot: Any) -> None:
    """主窗口应能使用外部服务完成初始化。"""
    service = FakeApplicationService()
    window = MainWindow(service=service)
    qtbot.addWidget(window)

    assert window.template_list.count() == 1
    assert window.template_name_input.text() == "模板1"
    assert window.send_count_input.value() == 3


def test_connect_button_runs_worker(qtbot: Any) -> None:
    """连接按钮应触发后台连接并刷新状态。"""
    service = FakeApplicationService()
    window = MainWindow(service=service)
    qtbot.addWidget(window)

    qtbot.mouseClick(window.connect_button, Qt.LeftButton)
    qtbot.waitUntil(
        lambda: window.browser_status_label.text() == "浏览器已连接，等待执行...",
        timeout=3000,
    )

    assert window.browser_status_label.text() == "浏览器已连接，等待执行..."


def test_add_template_button_refreshes_list(qtbot: Any) -> None:
    """新增模板按钮应刷新模板列表。"""
    service = FakeApplicationService()
    window = MainWindow(service=service)
    qtbot.addWidget(window)

    qtbot.mouseClick(window.add_template_button, Qt.LeftButton)

    assert service.add_calls == 1
    assert window.template_list.count() == 2


def test_execute_click_disables_template_mutation_controls(
    qtbot: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """开始执行后应立即禁用模板变更控件。"""
    service = FakeApplicationService()
    window = MainWindow(service=service)
    qtbot.addWidget(window)

    monkeypatch.setattr(ExecuteInvitesWorker, "start", lambda self: None)

    qtbot.mouseClick(window.execute_button, Qt.LeftButton)

    assert window.add_template_button.isEnabled() is False
    assert window.save_template_button.isEnabled() is False
    assert window.delete_template_button.isEnabled() is False
    assert window.activate_template_button.isEnabled() is False
    assert window.sync_remote_button.isEnabled() is False
    assert window.template_list.isEnabled() is False
    assert window.template_name_input.isReadOnly() is True
    assert window.template_content_edit.isReadOnly() is True


def test_execute_is_blocked_when_template_has_unsaved_changes(
    qtbot: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """未保存模板改动时不应开始执行。"""
    service = FakeApplicationService()
    window = MainWindow(service=service)
    qtbot.addWidget(window)
    warning_messages: list[str] = []

    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _parent, _title, message: warning_messages.append(message)
        or QMessageBox.StandardButton.Ok,
    )

    window.template_content_edit.setPlainText("未保存的新内容")
    qtbot.mouseClick(window.execute_button, Qt.LeftButton)

    assert warning_messages == ["开始执行前请先保存当前模板修改。"]
    assert window._execute_worker is None
