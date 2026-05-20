from __future__ import annotations

import json
from pathlib import Path

from app_interfaces import AppSettings
from config_manager import ConfigManager


def test_invalid_config_falls_back_to_defaults(tmp_path: Path) -> None:
    """ConfigManager should recover gracefully from invalid JSON files."""
    config_path = tmp_path / "tui_config.json"
    config_path.write_text("{invalid json", encoding="utf-8")

    manager = ConfigManager(config_path=config_path)

    assert manager.send_count == 10
    assert manager.notify_channel == "desktop"
    assert manager.sync_url == "http://localhost:8080"


def test_replace_persists_validated_settings(tmp_path: Path) -> None:
    """Replacing settings should write a validated snapshot to disk."""
    config_path = tmp_path / "tui_config.json"
    manager = ConfigManager(config_path=config_path)

    manager.replace(
        AppSettings(
            send_count=3,
            selected_template_index=1,
            active_template_index=0,
            notify_channel="both",
            feishu_webhook_url="https://example.com/hook",
            sync_url="https://sync.example.com",
        )
    )

    persisted = json.loads(config_path.read_text(encoding="utf-8"))
    reloaded = ConfigManager(config_path=config_path)

    assert persisted["send_count"] == 3
    assert reloaded.send_count == 3
    assert reloaded.notify_channel == "both"
    assert reloaded.feishu_webhook_url == "https://example.com/hook"
