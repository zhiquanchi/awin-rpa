from __future__ import annotations

from pathlib import Path

import pytest

from app_interfaces import AppSettings, InvitationTemplate
from awin_application_service import AwinApplicationService
from config_manager import ConfigManager
from json_template_repository import JsonTemplateRepository


class FakeRpaRunner:
    """测试用 RPA 执行器。"""

    def __init__(self, notify_channel: str | None, feishu_webhook_url: str | None) -> None:
        self.notify_channel = notify_channel
        self.feishu_webhook_url = feishu_webhook_url
        self.current_template_name = ""
        self._clicked_ids: set[str] = set()
        self._publisher_batches: list[list[str]] = [["1001", "1002"]]
        self.sent_messages: list[tuple[str, str]] = []

    def get_publisher_ids(self) -> list[str]:
        """返回当前批次的 publisher。"""
        if self._publisher_batches:
            return self._publisher_batches.pop(0)
        return []

    def click_next_page(self) -> None:
        """翻页占位实现。"""

    def send_invite_to_publisher(self, publisher_id: str, msg: str) -> bool:
        """模拟成功发送并记录已处理 publisher。"""
        self.sent_messages.append((publisher_id, msg))
        self._clicked_ids.add(publisher_id)
        return True

    def reset_clicked_ids(self) -> int:
        """清空已点击记录。"""
        count = len(self._clicked_ids)
        self._clicked_ids.clear()
        return count

    def clicked_publisher_count(self) -> int:
        """返回已点击记录数量。"""
        return len(self._clicked_ids)

    def has_clicked_publisher(self, publisher_id: str) -> bool:
        """判断某个 publisher 是否已处理。"""
        return publisher_id in self._clicked_ids


@pytest.fixture
def local_paths(tmp_path: Path) -> tuple[Path, Path]:
    """返回测试配置文件与模板文件路径。"""
    return tmp_path / "tui_config.json", tmp_path / "invitation_messages.json"


def test_bootstrap_creates_default_templates(local_paths: tuple[Path, Path]) -> None:
    """bootstrap 应创建默认模板。"""
    config_path, template_path = local_paths
    service = AwinApplicationService(
        template_repository=JsonTemplateRepository(file_path=template_path),
        settings_factory=lambda: ConfigManager(config_path=config_path),
    )

    state = service.bootstrap()

    assert len(state.templates.templates) == 1
    assert state.templates.templates[0].name == "默认模板"


def test_delete_template_repairs_active_index(local_paths: tuple[Path, Path]) -> None:
    """删除模板后应修正激活索引。"""
    config_path, template_path = local_paths
    repository = JsonTemplateRepository(file_path=template_path)
    repository.save_templates(
        [
            InvitationTemplate(id=1, name="模板1", content="内容1"),
            InvitationTemplate(id=2, name="模板2", content="内容2"),
            InvitationTemplate(id=3, name="模板3", content="内容3"),
        ]
    )
    settings = ConfigManager(config_path=config_path)
    settings.active_template_index = 2
    service = AwinApplicationService(
        template_repository=repository,
        settings_factory=lambda: ConfigManager(config_path=config_path),
    )
    service.bootstrap()

    state = service.delete_template(1)

    assert [template.id for template in state.templates.templates] == [2, 3]
    assert state.templates.active_template_id == 3


def test_connect_execute_and_reset_flow(local_paths: tuple[Path, Path]) -> None:
    """服务应完成连接、执行与重置流程。"""
    config_path, template_path = local_paths
    repository = JsonTemplateRepository(file_path=template_path)
    repository.save_templates([InvitationTemplate(id=1, name="模板1", content="内容1")])
    settings = ConfigManager(config_path=config_path)
    settings.active_template_index = 0
    settings.send_count = 1
    fake_rpa = FakeRpaRunner(None, None)
    log_messages: list[str] = []
    service = AwinApplicationService(
        template_repository=repository,
        settings_factory=lambda: ConfigManager(config_path=config_path),
        rpa_factory=lambda notify_channel, webhook: fake_rpa,
    )
    service.bootstrap()

    connected_state = service.connect_browser()
    result = service.execute_invites(
        template_id=1,
        log_callback=lambda entry: log_messages.append(entry.message),
    )
    cleared_count, reset_state = service.reset_clicked_ids()

    assert connected_state.connection.browser_connected is True
    assert result.completed is True
    assert result.sent_count == 1
    assert "开始执行任务..." in log_messages
    assert "第 1/1 条消息发送成功 (publisher: 1001)" in log_messages
    assert cleared_count == 1
    assert reset_state.connection.clicked_records_count == 0


def test_connect_browser_does_not_require_active_template(
    local_paths: tuple[Path, Path],
) -> None:
    """连接浏览器不应依赖已激活模板。"""
    config_path, template_path = local_paths
    repository = JsonTemplateRepository(file_path=template_path)
    repository.save_templates([InvitationTemplate(id=1, name="模板1", content="内容1")])
    settings = ConfigManager(config_path=config_path)
    settings.active_template_index = -1
    fake_rpa = FakeRpaRunner(None, None)
    service = AwinApplicationService(
        template_repository=repository,
        settings_factory=lambda: ConfigManager(config_path=config_path),
        rpa_factory=lambda notify_channel, webhook: fake_rpa,
    )
    service.bootstrap()

    connected_state = service.connect_browser()

    assert connected_state.connection.browser_connected is True


@pytest.mark.parametrize(
    "operation",
    [
        lambda service: service.add_template(),
        lambda service: service.save_template(1, "模板1", "新内容"),
        lambda service: service.delete_template(1),
        lambda service: service.activate_template(1),
        lambda service: service.select_template(1),
    ],
)
def test_template_mutation_is_blocked_while_running(
    local_paths: tuple[Path, Path], operation
) -> None:
    """执行期间不允许修改模板。"""
    config_path, template_path = local_paths
    repository = JsonTemplateRepository(file_path=template_path)
    repository.save_templates([InvitationTemplate(id=1, name="模板1", content="内容1")])
    settings = ConfigManager(config_path=config_path)
    settings.active_template_index = 0
    service = AwinApplicationService(
        template_repository=repository,
        settings_factory=lambda: ConfigManager(config_path=config_path),
    )
    service.bootstrap()
    service._execution_state.is_running = True

    with pytest.raises(ValueError, match="执行期间不允许(修改|切换)模板"):
        operation(service)
