from __future__ import annotations

import pytest
import responses

from remote_sync_service import RemoteSyncService


@responses.activate
def test_pull_configs_returns_validated_bundle() -> None:
    """RemoteSyncService should parse synced settings and templates into models."""
    responses.add(
        responses.GET,
        "https://sync.example.com/api/sync/awin-rpa/test-uid",
        json={
            "configs": {
                "tui_config": {
                    "send_count": 5,
                    "selected_template_index": 0,
                    "active_template_index": 0,
                    "notify_channel": "both",
                    "feishu_webhook_url": "https://example.com/hook",
                    "sync_url": "https://sync.example.com",
                },
                "invitation_messages": [{"name": "模板A", "content": "内容A"}],
            }
        },
        status=200,
    )

    service = RemoteSyncService("https://sync.example.com", "test-uid")
    pulled_bundle = service.pull_configs()

    assert pulled_bundle is not None
    assert pulled_bundle.settings is not None
    assert pulled_bundle.settings.send_count == 5
    assert pulled_bundle.templates is not None
    assert pulled_bundle.templates[0].name == "模板A"


@responses.activate
def test_push_config_posts_expected_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    """push_config should send the expected sync payload."""
    responses.add(
        responses.POST,
        "https://sync.example.com/api/sync/awin-rpa/test-uid",
        json={},
        status=200,
    )

    service = RemoteSyncService("https://sync.example.com", "test-uid")

    class ImmediateThread:
        """Execute background work synchronously to simplify assertions."""

        def __init__(self, target, args, name, daemon) -> None:
            self._target = target
            self._args = args

        def start(self) -> None:
            self._target(*self._args)

    monkeypatch.setattr("remote_sync_service.threading.Thread", ImmediateThread)

    service.push_config("tui_config", {"send_count": 2})

    assert len(responses.calls) == 1
    assert responses.calls[0].request.body == b'{"configs": {"tui_config": {"send_count": 2}}}'
