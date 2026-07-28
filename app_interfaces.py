from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Literal, Protocol, Sequence, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator

NotifyChannel = Literal["desktop", "feishu", "both", "none"]
LogLevel = Literal["info", "success", "warning", "error"]


class InvitationTemplate(BaseModel):
    """Shared invitation template model used across UI and persistence layers."""

    model_config = ConfigDict(validate_assignment=True)

    id: int = Field(ge=1)
    name: str = Field(min_length=1)
    content: str = ""

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, value: Any) -> str:
        """Normalize template names and provide a stable fallback."""
        normalized = str(value or "").strip()
        return normalized or "未命名模板"

    @field_validator("content", mode="before")
    @classmethod
    def normalize_content(cls, value: Any) -> str:
        """Normalize nullable content values to strings."""
        return str(value or "")

    def to_storage_record(self) -> dict[str, str]:
        """Convert the model into the JSON payload used by local persistence."""
        return {"id": str(self.id), "name": self.name, "content": self.content}

    @classmethod
    def from_storage_record(
        cls, data: dict[str, Any] | None, template_id: int
    ) -> "InvitationTemplate":
        """Create a validated template from a persisted JSON record."""
        payload = data or {}
        raw_id = payload.get("id")
        try:
            persisted_id = int(raw_id)
        except (TypeError, ValueError):
            persisted_id = template_id
        return cls(
            id=persisted_id if persisted_id >= 1 else template_id,
            name=payload.get("name", f"模板{template_id}"),
            content=payload.get("content", ""),
        )


class AppSettings(BaseModel):
    """Validated shared settings used by the CLI and TUI."""

    model_config = ConfigDict(validate_assignment=True)

    send_count: int = Field(default=10, ge=1)
    selected_template_index: int = Field(default=0, ge=0)
    active_template_index: int = Field(default=-1, ge=-1)
    notify_channel: NotifyChannel = "desktop"
    feishu_webhook_url: str = ""

    @field_validator("notify_channel", mode="before")
    @classmethod
    def normalize_notify_channel(cls, value: Any) -> NotifyChannel:
        """Normalize unsupported notification channels to the safe default."""
        normalized = str(value or "").strip().lower()
        if normalized in {"desktop", "feishu", "both", "none"}:
            return normalized  # type: ignore[return-value]
        return "desktop"

    @field_validator("feishu_webhook_url", mode="before")
    @classmethod
    def normalize_webhook_url(cls, value: Any) -> str:
        """Normalize nullable webhook values to trimmed strings."""
        return str(value or "").strip()

    def to_storage_payload(self) -> dict[str, Any]:
        """Return the JSON-serializable settings payload."""
        return self.model_dump(mode="json")


class TemplatePanelViewModel(BaseModel):
    """模板面板视图模型。"""

    model_config = ConfigDict(validate_assignment=True)

    templates: list[InvitationTemplate] = Field(default_factory=list)
    selected_template_id: int | None = None
    active_template_id: int | None = None


class SettingsViewModel(BaseModel):
    """设置面板视图模型。"""

    model_config = ConfigDict(validate_assignment=True)

    send_count: int = Field(default=10, ge=1)
    notify_channel: NotifyChannel = "desktop"
    feishu_webhook_url: str = ""


class BrowserConnectionViewModel(BaseModel):
    """浏览器连接状态视图模型。"""

    model_config = ConfigDict(validate_assignment=True)

    browser_connected: bool = False
    clicked_records_count: int = Field(default=0, ge=0)
    status_text: str = "未连接浏览器"
    last_error: str | None = None


class DailyStatsViewModel(BaseModel):
    """每日统计视图模型。"""

    model_config = ConfigDict(validate_assignment=True)

    date: str = ""  # YYYY-MM-DD
    success_count: int = Field(default=0, ge=0)
    failed_count: int = Field(default=0, ge=0)


class RecruitmentTaskViewModel(BaseModel):
    """招募任务历史记录视图模型。"""

    model_config = ConfigDict(validate_assignment=True)

    task_id: str
    start_time: str  # ISO format
    end_time: str | None = None
    template_id: int | None = None
    template_name: str = ""
    target_count: int = Field(default=0, ge=0)
    success_count: int = Field(default=0, ge=0)
    failed_count: int = Field(default=0, ge=0)
    status: str = "running"  # running / completed / stopped / error
    last_message: str = ""


class ExecutionStateViewModel(BaseModel):
    """执行状态视图模型。"""

    model_config = ConfigDict(validate_assignment=True)

    is_running: bool = False
    current_template_id: int | None = None
    current_template_name: str = ""
    sent_count: int = Field(default=0, ge=0)
    failed_count: int = Field(default=0, ge=0)
    target_count: int = Field(default=0, ge=0)
    last_message: str = ""
    last_error: str | None = None


class ExecutionLogEntryViewModel(BaseModel):
    """执行日志条目视图模型。"""

    model_config = ConfigDict(validate_assignment=True)

    timestamp: str
    level: LogLevel
    message: str


class ExecutionResultViewModel(BaseModel):
    """执行结果视图模型。"""

    model_config = ConfigDict(validate_assignment=True)

    sent_count: int = Field(default=0, ge=0)
    failed_count: int = Field(default=0, ge=0)
    target_count: int = Field(default=0, ge=0)
    stopped: bool = False
    completed: bool = False
    last_message: str = ""


class AppRuntimeStateViewModel(BaseModel):
    """应用运行态视图模型。"""

    model_config = ConfigDict(validate_assignment=True)

    templates: TemplatePanelViewModel
    settings: SettingsViewModel
    connection: BrowserConnectionViewModel
    execution: ExecutionStateViewModel
    daily_stats: DailyStatsViewModel = Field(default_factory=DailyStatsViewModel)
    recruitment_history: list[RecruitmentTaskViewModel] = Field(default_factory=list)


@runtime_checkable
class SettingsRepositoryProtocol(Protocol):
    """Contract for settings storage used by user interfaces."""

    @property
    def config_path(self) -> Path:
        """Return the on-disk settings path."""

    @property
    def send_count(self) -> int:
        """Return the configured send count."""

    @send_count.setter
    def send_count(self, value: int) -> None:
        """Persist a new send count."""

    @property
    def selected_template_index(self) -> int:
        """Return the selected template index."""

    @selected_template_index.setter
    def selected_template_index(self, value: int) -> None:
        """Persist a new selected template index."""

    @property
    def active_template_index(self) -> int:
        """Return the active template index."""

    @active_template_index.setter
    def active_template_index(self, value: int) -> None:
        """Persist a new active template index."""

    @property
    def notify_channel(self) -> NotifyChannel:
        """Return the normalized notification channel."""

    @notify_channel.setter
    def notify_channel(self, value: str) -> None:
        """Persist a new notification channel."""

    @property
    def feishu_webhook_url(self) -> str:
        """Return the configured Feishu webhook URL."""

    @feishu_webhook_url.setter
    def feishu_webhook_url(self, value: str) -> None:
        """Persist a new Feishu webhook URL."""

    @property
    def feishu_enabled(self) -> bool:
        """Return whether Feishu delivery is enabled."""

    def set_feishu_enabled(self, enabled: bool) -> None:
        """Toggle Feishu delivery using the repository's persistence rules."""

    def replace(self, settings: AppSettings) -> None:
        """Replace the current settings with a validated snapshot."""

    def snapshot(self) -> AppSettings:
        """Return a defensive copy of the current settings."""

    def to_storage_payload(self) -> dict[str, Any]:
        """Return the payload used by local persistence."""


@runtime_checkable
class TemplateRepositoryProtocol(Protocol):
    """Contract for invitation template persistence."""

    @property
    def file_path(self) -> Path:
        """Return the template file path."""

    def load_templates(self) -> list[InvitationTemplate]:
        """Load all persisted invitation templates."""

    def save_templates(self, templates: Sequence[InvitationTemplate]) -> None:
        """Persist the full invitation template list."""

    def ensure_default_templates(self) -> list[InvitationTemplate]:
        """Return existing templates or create the default template set."""

    def to_storage_payload(
        self, templates: Sequence[InvitationTemplate]
    ) -> list[dict[str, str]]:
        """Convert validated templates to the storage payload format."""


@runtime_checkable
class RpaRunnerProtocol(Protocol):
    """RPA 执行器契约。"""

    current_template_name: str

    def get_publisher_ids(self) -> list[str]:
        """获取当前页面可邀请的 publisher 列表。"""

    def click_next_page(self) -> None:
        """翻到下一页。"""

    def send_invite_to_publisher(self, publisher_id: str, msg: str) -> bool:
        """向单个 publisher 发送邀请。"""

    def reset_clicked_ids(self) -> int:
        """重置已点击记录并返回清理数量。"""

    def clicked_publisher_count(self) -> int:
        """返回当前已点击记录数量。"""

    def has_clicked_publisher(self, publisher_id: str) -> bool:
        """判断某个 publisher 是否已处理。"""


@runtime_checkable
class ApplicationServiceProtocol(Protocol):
    """应用服务契约。"""

    def bootstrap(self) -> AppRuntimeStateViewModel:
        """初始化应用依赖并返回首个运行态。"""

    def get_state(self) -> AppRuntimeStateViewModel:
        """读取当前运行态。"""

    def select_template(self, template_id: int | None) -> AppRuntimeStateViewModel:
        """切换当前选中的模板。"""

    def add_template(
        self, name: str | None = None, content: str | None = None
    ) -> AppRuntimeStateViewModel:
        """新增模板。"""

    def save_template(
        self, template_id: int, name: str | None, content: str
    ) -> AppRuntimeStateViewModel:
        """保存模板内容。"""

    def delete_template(self, template_id: int) -> AppRuntimeStateViewModel:
        """删除模板。"""

    def activate_template(self, template_id: int) -> AppRuntimeStateViewModel:
        """激活模板。"""

    def set_send_count(self, value: int) -> AppRuntimeStateViewModel:
        """更新发送数量。"""

    def set_notify_settings(
        self, channel: NotifyChannel | str, webhook_url: str
    ) -> AppRuntimeStateViewModel:
        """更新通知设置。"""

    def connect_browser(self) -> AppRuntimeStateViewModel:
        """连接浏览器。"""

    def reset_clicked_ids(self) -> tuple[int, AppRuntimeStateViewModel]:
        """重置已点击记录。"""

    def execute_invites(
        self,
        template_id: int | None,
        stop_requested: Callable[[], bool] | None = None,
        progress_callback: Callable[[], None] | None = None,
    ) -> ExecutionResultViewModel:
        """同步执行邀请流程。"""
