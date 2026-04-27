from pathlib import Path
import json


class ConfigManager:
    """共享配置管理器 - 保存终端 UI 与 TUI 的公共配置"""

    def __init__(self, config_path: Path | None = None):
        self.config_path = config_path or Path(__file__).parent / "tui_config.json"
        self._config = self._load()

    def _load(self) -> dict:
        """加载配置"""
        if self.config_path.exists():
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                pass
        return self._default_config()

    @staticmethod
    def _default_config() -> dict:
        """默认配置"""
        return {
            "send_count": 10,
            "selected_template_index": 0,
            "active_template_index": -1,  # -1 表示未激活任何模板
            "notify_channel": "desktop",  # desktop | feishu | both | none
            "feishu_webhook_url": "",
        }

    def save(self):
        """保存配置到文件"""
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(self._config, f, ensure_ascii=False, indent=2)

    @property
    def send_count(self) -> int:
        return self._config.get("send_count", 10)

    @send_count.setter
    def send_count(self, value: int):
        self._config["send_count"] = value
        self.save()

    @property
    def selected_template_index(self) -> int:
        return self._config.get("selected_template_index", 0)

    @selected_template_index.setter
    def selected_template_index(self, value: int):
        self._config["selected_template_index"] = value
        self.save()

    @property
    def active_template_index(self) -> int:
        """激活的模板索引，-1 表示未激活"""
        return self._config.get("active_template_index", -1)

    @active_template_index.setter
    def active_template_index(self, value: int):
        self._config["active_template_index"] = value
        self.save()

    @property
    def notify_channel(self) -> str:
        value = str(self._config.get("notify_channel", "desktop")).strip().lower()
        if value in {"desktop", "feishu", "both", "none"}:
            return value
        return "desktop"

    @notify_channel.setter
    def notify_channel(self, value: str):
        normalized = str(value).strip().lower()
        if normalized not in {"desktop", "feishu", "both", "none"}:
            normalized = "desktop"
        self._config["notify_channel"] = normalized
        self.save()

    @property
    def feishu_webhook_url(self) -> str:
        return str(self._config.get("feishu_webhook_url", "")).strip()

    @feishu_webhook_url.setter
    def feishu_webhook_url(self, value: str):
        self._config["feishu_webhook_url"] = str(value or "").strip()
        self.save()

    @property
    def feishu_enabled(self) -> bool:
        return self.notify_channel in {"feishu", "both"}

    def set_feishu_enabled(self, enabled: bool):
        self.notify_channel = "both" if enabled else "desktop"
