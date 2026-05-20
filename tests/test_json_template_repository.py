from __future__ import annotations

from pathlib import Path

from app_interfaces import InvitationTemplate
from json_template_repository import JsonTemplateRepository


def test_ensure_default_templates_creates_default_entry(tmp_path: Path) -> None:
    """The repository should create a default template when storage is empty."""
    repository = JsonTemplateRepository(file_path=tmp_path / "invitation_messages.json")

    templates = repository.ensure_default_templates()

    assert len(templates) == 1
    assert templates[0].name == "默认模板"
    assert templates[0].content == "请输入模板内容..."


def test_save_templates_round_trip(tmp_path: Path) -> None:
    """Saved templates should round-trip through JSON storage unchanged."""
    repository = JsonTemplateRepository(file_path=tmp_path / "invitation_messages.json")
    expected_templates = [
        InvitationTemplate(id=1, name="模板一", content="内容一"),
        InvitationTemplate(id=2, name="模板二", content="内容二"),
    ]

    repository.save_templates(expected_templates)
    loaded_templates = repository.load_templates()

    assert [template.model_dump() for template in loaded_templates] == [
        template.model_dump() for template in expected_templates
    ]
