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
from pyside6_app import (
    ExecuteInvitesWorker,
    MainWindow,
    NotifyCard,
    TemplateManagerDialog,
)


class FakeApplicationService:
    """测试用应用服务。"""

    def __init__(self) -> None:
        self.connect_calls = 0
        self.add_calls = 0
        self.reset_calls = 0
        self.save_setting_calls = 0
        self.notify_setting_calls = 0
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
        return self.state

    def refresh_from_remote(self) -> AppRuntimeStateViewModel:
        return self.state

    def get_state(self) -> AppRuntimeStateViewModel:
        return self.state

    def select_template(self, template_id: int | None) -> AppRuntimeStateViewModel:
        self.state.templates.selected_template_id = template_id
        return self.state

    def add_template(
        self, name: str | None = None, content: str | None = None
    ) -> AppRuntimeStateViewModel:
        self.add_calls += 1
        new_template = InvitationTemplate(id=2, name="模板2", content="内容2")
        self.state.templates.templates.append(new_template)
        self.state.templates.selected_template_id = 2
        return self.state

    def save_template(
        self, template_id: int, name: str | None, content: str
    ) -> AppRuntimeStateViewModel:
        for template in self.state.templates.templates:
            if template.id == template_id:
                template.name = name or template.name
                template.content = content
        return self.state

    def delete_template(self, template_id: int) -> AppRuntimeStateViewModel:
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
        self.state.templates.active_template_id = template_id
        return self.state

    def set_send_count(self, value: int) -> AppRuntimeStateViewModel:
        self.save_setting_calls += 1
        self.state.settings.send_count = value
        return self.state

    def set_notify_settings(
        self, channel: str, webhook_url: str
    ) -> AppRuntimeStateViewModel:
        self.notify_setting_calls += 1
        self.state.settings.notify_channel = channel
        self.state.settings.feishu_webhook_url = webhook_url
        return self.state

    def connect_browser(self) -> AppRuntimeStateViewModel:
        self.connect_calls += 1
        self.state.connection.browser_connected = True
        self.state.connection.status_text = "浏览器已连接，等待执行..."
        return self.state

    def reset_clicked_ids(self) -> tuple[int, AppRuntimeStateViewModel]:
        self.reset_calls += 1
        cleared = self.state.connection.clicked_records_count
        self.state.connection.clicked_records_count = 0
        return cleared, self.state

    def execute_invites(
        self, template_id: int | None, stop_requested=None, log_callback=None
    ) -> ExecutionResultViewModel:
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


def _make_window(
    service: FakeApplicationService, qtbot: Any, *, auto_connect: bool = False
) -> MainWindow:
    window = MainWindow(service=service, auto_connect_browser=auto_connect)
    qtbot.addWidget(window)
    return window


def test_main_window_initializes_with_service_state(qtbot: Any) -> None:
    """主窗口应能使用外部服务完成初始化。"""
    service = FakeApplicationService()
    window = _make_window(service, qtbot)

    assert window.sent_card.value_label.text() == "0"
    assert window.template_card.value_label.text() == "模板1"
    assert window.send_count_input.value() == 3
    assert isinstance(window.notify_card, NotifyCard)


def test_notify_card_feishu_without_webhook_opens_config(qtbot: Any) -> None:
    """未配置 Webhook 时点击飞书 Chip 不应开启飞书。"""
    service = FakeApplicationService()
    service.state.settings.notify_channel = "desktop"
    service.state.settings.feishu_webhook_url = ""
    window = _make_window(service, qtbot)
    window._apply_state(service.state)

    opened: list[bool] = []
    window._on_open_notify_config = lambda focus_webhook=False: opened.append(  # type: ignore[method-assign]
        focus_webhook
    )

    window.notify_card.master_checkbox.setChecked(True)
    window.notify_card.feishu_chip.click()

    assert window.notify_card.feishu_chip.isChecked() is False
    assert opened == [True]


def test_notify_card_toggle_persists_channel(qtbot: Any) -> None:
    """卡片快捷开关应调用 set_notify_settings。"""
    service = FakeApplicationService()
    window = _make_window(service, qtbot)
    window._apply_state(service.state)
    calls_before = service.notify_setting_calls

    window.notify_card.master_checkbox.setChecked(False)

    assert service.notify_setting_calls == calls_before + 1
    assert service.state.settings.notify_channel == "none"


def test_connect_button_runs_worker(qtbot: Any) -> None:
    """浏览器按钮应触发后台连接并刷新状态。"""
    service = FakeApplicationService()
    window = _make_window(service, qtbot)

    qtbot.mouseClick(window.browser_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(
        lambda: window.browser_button.text() == "浏览器已连接",
        timeout=3000,
    )

    assert service.connect_calls == 1


def test_add_template_in_dialog_refreshes_state(qtbot: Any) -> None:
    """模板管理弹窗应能新增模板。"""
    service = FakeApplicationService()
    window = _make_window(service, qtbot)

    dialog = TemplateManagerDialog(service, service.state, parent=window)
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.mouseClick(dialog.new_template_button, Qt.MouseButton.LeftButton)
    dialog.close()
    window._apply_state(service.get_state())

    assert service.add_calls == 1
    assert len(service.state.templates.templates) == 2


def test_reset_sent_card_clears_clicked_records(qtbot: Any) -> None:
    """重置按钮应清空已发送记录。"""
    service = FakeApplicationService()
    service.state.connection.clicked_records_count = 5
    window = _make_window(service, qtbot)
    window._apply_state(service.state)

    qtbot.mouseClick(window.sent_card.action_button, Qt.MouseButton.LeftButton)

    assert service.reset_calls == 1
    assert window.sent_card.value_label.text() == "0"


def test_execute_click_disables_dashboard_controls(
    qtbot: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """开始执行后应禁用仪表盘上的变更控件。"""
    service = FakeApplicationService()
    service.state.connection.browser_connected = True
    window = _make_window(service, qtbot)
    window._apply_state(service.state)

    monkeypatch.setattr(ExecuteInvitesWorker, "start", lambda self: None)

    qtbot.mouseClick(window.execute_button, Qt.MouseButton.LeftButton)

    assert window.template_card.action_button.isEnabled() is False
    assert window.sent_card.action_button.isEnabled() is False
    assert window.notify_card.isEnabled() is False
    assert window.settings_button.isEnabled() is False
    assert window.send_count_input.isEnabled() is False


def test_execute_requires_browser_connection(
    qtbot: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """未连接浏览器时不应开始执行。"""
    service = FakeApplicationService()
    window = _make_window(service, qtbot)
    warning_messages: list[str] = []

    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _parent, _title, message: warning_messages.append(message)
        or QMessageBox.StandardButton.Ok,
    )

    qtbot.mouseClick(window.execute_button, Qt.MouseButton.LeftButton)

    assert warning_messages == ["请先连接浏览器。"]
    assert window._execute_worker is None
