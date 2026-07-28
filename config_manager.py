from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from loguru import logger
from platformdirs import user_config_dir

from app_interfaces import AppSettings, NotifyChannel

APP_NAME = "AwinRPA"


def get_user_config_dir() -> Path:
    """获取用户配置文件夹路径（跨平台）。

    - Windows: %APPDATA%\\AwinRPA
    - macOS: ~/Library/Application Support/AwinRPA
    - Linux: ~/.config/AwinRPA
    """
    config_dir = Path(user_config_dir(appname=APP_NAME, appauthor=False, roaming=True))
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


def _migrate_if_needed(new_path: Path, old_path: Path) -> None:
    """如果旧配置文件存在但新配置文件不存在，则迁移到新位置。"""
    if new_path.exists():
        return
    if not old_path.exists():
        return
    try:
        shutil.copy2(old_path, new_path)
        logger.info(f"已将配置从 {old_path} 迁移到 {new_path}")
    except OSError as error:
        logger.warning(f"迁移配置失败 ({old_path} -> {new_path}): {error}")


class ConfigManager:
    """共享配置管理器，负责持久化 CLI 与 TUI 共用的设置。"""

    def __init__(self, config_path: Path | None = None) -> None:
        if config_path is None:
            default_path = get_user_config_dir() / "settings.json"
            # 兼容旧文件名 tui_config.json
            legacy_in_config_dir = get_user_config_dir() / "tui_config.json"
            if legacy_in_config_dir.exists() and not default_path.exists():
                try:
                    legacy_in_config_dir.rename(default_path)
                    logger.info(f"已重命名配置文件为 {default_path.name}")
                except OSError as error:
                    logger.warning(f"重命名配置文件失败: {error}")
            # 从项目根目录迁移
            legacy_root_path = Path(__file__).parent / "tui_config.json"
            _migrate_if_needed(default_path, legacy_root_path)
            config_path = default_path
        self.config_path = config_path
        self._settings = self._load()

    def _load(self) -> AppSettings:
        """Load persisted settings and fall back to defaults on invalid files."""
        if not self.config_path.exists():
            return AppSettings()

        try:
            with open(self.config_path, "r", encoding="utf-8") as file:
                raw_config = json.load(file)
        except (json.JSONDecodeError, OSError) as error:
            logger.warning(f"加载配置文件失败，已回退到默认配置: {error}")
            return AppSettings()

        if not isinstance(raw_config, dict):
            logger.warning("配置文件格式无效，已回退到默认配置。")
            return AppSettings()

        # 移除已废弃的云端同步字段（如果存在）
        raw_config.pop("sync_url", None)

        return AppSettings.model_validate(raw_config)

    def _update(self, **changes: Any) -> None:
        """Validate and persist incremental settings updates."""
        payload = self.to_storage_payload()
        payload.update(changes)
        self.replace(AppSettings.model_validate(payload))

    def save(self) -> None:
        """Persist the current settings snapshot to disk."""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.config_path, "w", encoding="utf-8") as file:
            json.dump(self.to_storage_payload(), file, ensure_ascii=False, indent=2)

    def replace(self, settings: AppSettings) -> None:
        """Replace the current settings with a validated model snapshot."""
        self._settings = settings.model_copy(deep=True)
        self.save()

    def snapshot(self) -> AppSettings:
        """Return a defensive copy of the current settings model."""
        return self._settings.model_copy(deep=True)

    def to_storage_payload(self) -> dict[str, Any]:
        """Return the JSON payload used for local persistence."""
        return self._settings.to_storage_payload()

    @property
    def send_count(self) -> int:
        """Return the configured send count."""
        return self._settings.send_count

    @send_count.setter
    def send_count(self, value: int) -> None:
        """Persist a new send count."""
        self._update(send_count=value)

    @property
    def selected_template_index(self) -> int:
        """Return the selected template index."""
        return self._settings.selected_template_index

    @selected_template_index.setter
    def selected_template_index(self, value: int) -> None:
        """Persist a new selected template index."""
        self._update(selected_template_index=value)

    @property
    def active_template_index(self) -> int:
        """Return the active template index."""
        return self._settings.active_template_index

    @active_template_index.setter
    def active_template_index(self, value: int) -> None:
        """Persist a new active template index."""
        self._update(active_template_index=value)

    @property
    def notify_channel(self) -> NotifyChannel:
        """Return the normalized notification channel."""
        return self._settings.notify_channel

    @notify_channel.setter
    def notify_channel(self, value: str) -> None:
        """Persist a new notification channel."""
        self._update(notify_channel=value)

    @property
    def feishu_webhook_url(self) -> str:
        """Return the configured Feishu webhook URL."""
        return self._settings.feishu_webhook_url

    @feishu_webhook_url.setter
    def feishu_webhook_url(self, value: str) -> None:
        """Persist a new Feishu webhook URL."""
        self._update(feishu_webhook_url=value)

    @property
    def feishu_enabled(self) -> bool:
        """Return whether Feishu delivery is enabled."""
        return self.notify_channel in {"feishu", "both"}

    def set_feishu_enabled(self, enabled: bool) -> None:
        """Switch Feishu delivery on or off while preserving current semantics."""
        self.notify_channel = "both" if enabled else "desktop"
