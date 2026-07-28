from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

from loguru import logger

from app_interfaces import InvitationTemplate
from config_manager import _migrate_if_needed, get_user_config_dir

DEFAULT_TEMPLATE_NAME = "默认模板"
DEFAULT_TEMPLATE_CONTENT = "请输入模板内容..."


class JsonTemplateRepository:
    """Persist invitation templates in the repository's JSON storage file."""

    def __init__(self, file_path: Path | None = None) -> None:
        if file_path is None:
            default_path = get_user_config_dir() / "invitation_messages.json"
            legacy_path = Path(__file__).parent / "invitation_messages.json"
            _migrate_if_needed(default_path, legacy_path)
            file_path = default_path
        self.file_path = file_path

    def load_templates(self) -> list[InvitationTemplate]:
        """Load validated templates from the JSON storage file."""
        if not self.file_path.exists():
            return []

        try:
            with open(self.file_path, "r", encoding="utf-8") as file:
                raw_data = json.load(file)
        except (json.JSONDecodeError, OSError) as error:
            logger.warning(f"加载模板文件失败: {error}")
            return []

        if not isinstance(raw_data, list):
            logger.warning("模板文件格式无效，期望列表结构。")
            return []

        templates: list[InvitationTemplate] = []
        for index, item in enumerate(raw_data, start=1):
            if not isinstance(item, dict):
                logger.warning(f"模板文件第 {index} 项格式无效，已忽略。")
                continue
            templates.append(InvitationTemplate.from_storage_record(item, index))
        return templates

    def save_templates(self, templates: Sequence[InvitationTemplate]) -> None:
        """Save templates to disk using the repository JSON format."""
        payload = self.to_storage_payload(templates)
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.file_path, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)

    def ensure_default_templates(self) -> list[InvitationTemplate]:
        """Return templates, creating a default one when storage is empty."""
        templates = self.load_templates()
        if templates:
            return templates

        default_templates = [
            InvitationTemplate(
                id=1,
                name=DEFAULT_TEMPLATE_NAME,
                content=DEFAULT_TEMPLATE_CONTENT,
            )
        ]
        self.save_templates(default_templates)
        return default_templates

    def to_storage_payload(
        self, templates: Sequence[InvitationTemplate]
    ) -> list[dict[str, str]]:
        """Convert validated templates into the storage payload format."""
        return [template.to_storage_record() for template in templates]
