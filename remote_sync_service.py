from __future__ import annotations

import hashlib
import json
import threading
import uuid
from pathlib import Path
from typing import Any

import requests
from loguru import logger

from app_interfaces import (
    AppSettings,
    InvitationTemplate,
    PulledConfigBundle,
    SyncConfigKind,
)

def get_machine_uid() -> str:
    """
    获取机器唯一标识并哈希化。
    结合网卡 MAC 地址生成哈希，确保在同一台机器上结果一致且不可逆。
    """
    node = uuid.getnode()
    return hashlib.sha256(str(node).encode()).hexdigest()


class RemoteSyncService:
    """远程同步服务，负责配置拉取与异步推送。"""

    APP_ID = "awin-rpa"

    def __init__(self, api_base_url: str, uid: str) -> None:
        self.api_base_url = api_base_url.rstrip("/")
        self.uid = uid

    def pull_configs(self) -> PulledConfigBundle | None:
        """Pull the latest synced settings and templates from the remote server."""
        url = f"{self.api_base_url}/api/sync/{self.APP_ID}/{self.uid}"
        try:
            logger.info(f"正在从云端找回配置 (App: {self.APP_ID}, UID: {self.uid[:8]}...)...")
            resp = requests.get(url, timeout=5)
        except requests.RequestException as error:
            logger.error(f"云端找回发生网络异常: {error}")
            return None

        if resp.status_code == 404:
            logger.info("云端暂无此机器的备份记录")
            return None

        if resp.status_code != 200:
            logger.warning(f"云端找回失败: HTTP {resp.status_code}")
            return None

        try:
            data = resp.json()
        except ValueError as error:
            logger.error(f"云端找回返回了无效 JSON: {error}")
            return None

        configs = data.get("configs", {})
        if not isinstance(configs, dict):
            logger.warning("云端找回结果缺少有效的 configs 字段。")
            return None

        pulled_bundle = PulledConfigBundle()

        raw_settings = configs.get("tui_config")
        if isinstance(raw_settings, dict) and raw_settings:
            pulled_bundle.settings = AppSettings.model_validate(raw_settings)
            logger.info("已找回云端 TUI 设置")

        raw_templates = configs.get("invitation_messages")
        if isinstance(raw_templates, list) and raw_templates:
            templates: list[InvitationTemplate] = []
            for index, item in enumerate(raw_templates, start=1):
                if not isinstance(item, dict):
                    logger.warning(f"云端模板第 {index} 项格式无效，已忽略。")
                    continue
                templates.append(InvitationTemplate.from_storage_record(item, index))
            if templates:
                pulled_bundle.templates = templates
                logger.info("已找回云端邀请模板")

        if pulled_bundle.settings is None and pulled_bundle.templates is None:
            return None
        return pulled_bundle

    def pull_and_apply(
        self, tui_config_path: Path, invitation_messages_path: Path
    ) -> None:
        """Pull remote data and apply it to the legacy on-disk files."""
        pulled_bundle = self.pull_configs()
        if pulled_bundle is None:
            return

        if pulled_bundle.settings is not None:
            with open(tui_config_path, "w", encoding="utf-8") as file:
                json.dump(
                    pulled_bundle.settings.to_storage_payload(),
                    file,
                    indent=2,
                    ensure_ascii=False,
                )
            logger.info("已找回并应用云端 TUI 设置")

        if pulled_bundle.templates is not None:
            with open(invitation_messages_path, "w", encoding="utf-8") as file:
                json.dump(
                    [template.to_storage_record() for template in pulled_bundle.templates],
                    file,
                    indent=2,
                    ensure_ascii=False,
                )
            logger.info("已找回并应用云端邀请模板")

    def push_config(
        self,
        kind: SyncConfigKind,
        data: dict[str, Any] | list[dict[str, Any]],
    ) -> None:
        """Push a single configuration payload asynchronously."""
        threading.Thread(
            target=self._push_worker,
            args=(kind, data),
            name=f"SyncWorker-{kind}",
            daemon=True,
        ).start()

    def _push_worker(
        self,
        kind: SyncConfigKind,
        data: dict[str, Any] | list[dict[str, Any]],
    ) -> None:
        """Perform the remote sync POST request for a single payload."""
        url = f"{self.api_base_url}/api/sync/{self.APP_ID}/{self.uid}"
        payload = {"configs": {kind: data}}

        try:
            resp = requests.post(url, json=payload, timeout=10)
            if resp.status_code == 200:
                logger.debug(f"云端同步成功 ({kind})")
            else:
                logger.warning(f"云端同步失败 ({kind}): HTTP {resp.status_code}")
        except (requests.RequestException, TypeError) as error:
            logger.error(f"云端同步异常 ({kind}): {error}")
