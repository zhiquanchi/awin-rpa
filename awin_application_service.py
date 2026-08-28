from __future__ import annotations

from pathlib import Path
from threading import RLock
from typing import Callable

from loguru import logger

from app_interfaces import (
    AppRuntimeStateViewModel,
    ApplicationServiceProtocol,
    AppSettings,
    BrowserConnectionViewModel,
    DailyStatsViewModel,
    ExecutionResultViewModel,
    ExecutionStateViewModel,
    InvitationTemplate,
    NotifyChannel,
    RecruitmentTaskViewModel,
    RpaRunnerProtocol,
    SettingsRepositoryProtocol,
    SettingsViewModel,
    TemplatePanelViewModel,
    TemplateRepositoryProtocol,
)
from config_manager import ConfigManager
from json_template_repository import JsonTemplateRepository
from logging_setup import setup_logging
from DrissionPage.errors import PageDisconnectedError

from main import (
    AwinRPA,
    CLICKED_IDS_PATH,
    _append_recruitment_task,
    _is_tcp_port_open,
    _load_id_set,
    _load_daily_stats,
    _load_recruitment_history,
    _reset_daily_stats,
    _save_daily_stats,
    _update_recruitment_task,
)

# 空转熔断阈值：连续这么多轮"没有任何新成功"就自动停止任务
MAX_EMPTY_ROUNDS = 5


class AwinApplicationService(ApplicationServiceProtocol):
    """共享应用服务，负责聚合配置、模板与 RPA 执行逻辑。"""

    def __init__(
        self,
        template_repository: TemplateRepositoryProtocol | None = None,
        settings_factory: Callable[[], SettingsRepositoryProtocol] | None = None,
        rpa_factory: Callable[[str | None, str | None], RpaRunnerProtocol] | None = None,
        clicked_ids_path: Path = CLICKED_IDS_PATH,
        clicked_ids_loader: Callable[[Path], set[str]] = _load_id_set,
    ) -> None:
        self.template_repository = template_repository or JsonTemplateRepository()
        self._settings_factory = settings_factory or ConfigManager
        self._rpa_factory = rpa_factory or AwinRPA
        self._clicked_ids_path = clicked_ids_path
        self._clicked_ids_loader = clicked_ids_loader
        self._lock = RLock()
        self._bootstrapped = False
        self._selected_template_id: int | None = None
        self._rpa: RpaRunnerProtocol | None = None
        self._execution_state = ExecutionStateViewModel()
        self._connection_error: str | None = None
        self._status_text = "未连接浏览器"
        self.settings_repository = self._settings_factory()

    def bootstrap(self) -> AppRuntimeStateViewModel:
        """初始化仓储并返回首个运行态。"""
        with self._lock:
            self._initialize_runtime()
            self._bootstrapped = True
            return self._build_state()

    def get_state(self) -> AppRuntimeStateViewModel:
        """读取当前运行态。"""
        with self._lock:
            self._ensure_bootstrapped()
            return self._build_state()

    def select_template(self, template_id: int | None) -> AppRuntimeStateViewModel:
        """切换当前选中的模板。"""
        with self._lock:
            self._ensure_bootstrapped()
            if self._execution_state.is_running:
                raise ValueError("执行期间不允许切换模板。")
            templates = self._load_templates()
            if template_id is None:
                self._selected_template_id = None
                return self._build_state()

            if not any(template.id == template_id for template in templates):
                raise ValueError("模板不存在，无法切换。")

            self._selected_template_id = template_id
            return self._build_state()

    def add_template(
        self, name: str | None = None, content: str | None = None
    ) -> AppRuntimeStateViewModel:
        """新增模板并返回更新后的运行态。"""
        with self._lock:
            self._ensure_bootstrapped()
            self._ensure_template_mutation_allowed()
            templates = self._load_templates()
            new_id = max((template.id for template in templates), default=0) + 1
            new_template = InvitationTemplate(
                id=new_id,
                name=(name or f"新模板{new_id}").strip(),
                content=content or "请输入模板内容...",
            )
            templates.append(new_template)
            self._save_templates(templates)
            self._selected_template_id = new_template.id
            return self._build_state()

    def save_template(
        self, template_id: int, name: str | None, content: str
    ) -> AppRuntimeStateViewModel:
        """保存模板内容并返回更新后的运行态。"""
        with self._lock:
            self._ensure_bootstrapped()
            self._ensure_template_mutation_allowed()
            templates = self._load_templates()
            updated_templates: list[InvitationTemplate] = []
            updated = False

            for template in templates:
                if template.id != template_id:
                    updated_templates.append(template)
                    continue

                updated_templates.append(
                    InvitationTemplate(
                        id=template.id,
                        name=(name or template.name).strip() or template.name,
                        content=content,
                    )
                )
                updated = True

            if not updated:
                raise ValueError("模板不存在，无法保存。")

            self._save_templates(updated_templates)
            self._selected_template_id = template_id
            return self._build_state()

    def delete_template(self, template_id: int) -> AppRuntimeStateViewModel:
        """删除模板并同步修正激活索引。"""
        with self._lock:
            self._ensure_bootstrapped()
            self._ensure_template_mutation_allowed()
            templates = self._load_templates()
            active_template_id = self._get_active_template_id(templates)
            remaining_templates = [
                template for template in templates if template.id != template_id
            ]

            if len(remaining_templates) == len(templates):
                raise ValueError("模板不存在，无法删除。")

            self._save_templates(remaining_templates)
            self._repair_active_template_index(
                templates=remaining_templates,
                previous_active_template_id=active_template_id,
            )
            if self._selected_template_id == template_id:
                self._selected_template_id = (
                    remaining_templates[0].id if remaining_templates else None
                )
            return self._build_state()

    def activate_template(self, template_id: int) -> AppRuntimeStateViewModel:
        """激活指定模板。"""
        with self._lock:
            self._ensure_bootstrapped()
            self._ensure_template_mutation_allowed()
            templates = self._load_templates()
            for index, template in enumerate(templates):
                if template.id == template_id:
                    self.settings_repository.active_template_index = index
                    self._selected_template_id = template_id
                    return self._build_state()
            raise ValueError("模板不存在，无法激活。")

    def set_send_count(self, value: int) -> AppRuntimeStateViewModel:
        """更新发送数量。"""
        with self._lock:
            self._ensure_bootstrapped()
            try:
                send_count = int(value)
            except (TypeError, ValueError) as error:
                raise ValueError("发送数量必须是整数。") from error

            self.settings_repository.send_count = max(1, send_count)
            return self._build_state()

    def set_notify_settings(
        self, channel: NotifyChannel | str, webhook_url: str
    ) -> AppRuntimeStateViewModel:
        """更新通知设置。"""
        with self._lock:
            self._ensure_bootstrapped()
            normalized_channel = self._normalize_notify_channel(channel)
            normalized_webhook = str(webhook_url or "").strip()

            if normalized_channel in {"feishu", "both"} and not normalized_webhook:
                raise ValueError("开启飞书通知时必须填写 Webhook URL。")

            self.settings_repository.notify_channel = normalized_channel
            self.settings_repository.feishu_webhook_url = normalized_webhook
            return self._build_state()

    def connect_browser(self) -> AppRuntimeStateViewModel:
        """连接浏览器并返回更新后的运行态。"""
        with self._lock:
            self._ensure_bootstrapped()

            if self._rpa is not None and self._is_browser_session_alive():
                self._status_text = "浏览器已连接，等待执行..."
                self._connection_error = None
                return self._build_state()

            self._rpa = None

            try:
                self._rpa = self._rpa_factory(
                    self.settings_repository.notify_channel,
                    self.settings_repository.feishu_webhook_url,
                )
            except Exception as error:
                self._connection_error = str(error)
                self._status_text = "连接失败"
                raise RuntimeError(f"连接浏览器失败: {error}") from error

            self._connection_error = None
            self._status_text = "浏览器已连接，等待执行..."
            return self._build_state()

    def _is_browser_session_alive(self) -> bool:
        """判断当前 RPA 会话是否存活（端口可用且标签页未断开）。"""
        if self._rpa is None:
            return False
        try:
            if not _is_tcp_port_open(self._rpa.browser_host, self._rpa.browser_port):
                return False
            # 进一步验证标签页是否真的还连着
            is_alive = getattr(self._rpa, "is_alive", None)
            if callable(is_alive):
                return is_alive()
            return True
        except Exception:
            return False

    def reset_clicked_ids(self) -> tuple[int, AppRuntimeStateViewModel]:
        """重置已点击记录，并返回清理数量和运行态。同时重置今日统计。"""
        with self._lock:
            self._ensure_bootstrapped()
            if self._rpa is not None:
                cleared_count = self._rpa.reset_clicked_ids()
            else:
                clicked_ids = self._clicked_ids_loader(self._clicked_ids_path)
                cleared_count = len(clicked_ids)
                if self._clicked_ids_path.exists():
                    self._clicked_ids_path.write_text("", encoding="utf-8")

            # 同时重置今日统计，保持数字一致
            _reset_daily_stats()

            self._status_text = (
                f"已清除 {cleared_count} 条已点击记录"
                if cleared_count > 0
                else "当前没有已点击记录"
            )
            self._connection_error = None
            return cleared_count, self._build_state()

    def execute_invites(
        self,
        template_id: int | None,
        stop_requested: Callable[[], bool] | None = None,
        progress_callback: Callable[[], None] | None = None,
    ) -> ExecutionResultViewModel:
        """同步执行邀请流程，由调用方负责线程调度。

        执行日志通过 loguru 全局 logger 输出，UI 层可注册自定义 sink 实时获取。
        progress_callback 在每次进度更新时调用，用于 UI 实时刷新。
        """
        # 确保日志系统已初始化（service 可能在 setup_logging 之前被创建）
        setup_logging()

        with self._lock:
            self._ensure_bootstrapped()
            selected_template = self._resolve_template(template_id)
            if self._rpa is None:
                raise ValueError("请先连接浏览器。")

            target_count = self.settings_repository.send_count
            execution_template_id = selected_template.id
            execution_template_name = selected_template.name
            execution_message = selected_template.content
            self._execution_state = ExecutionStateViewModel(
                is_running=True,
                current_template_id=execution_template_id,
                current_template_name=execution_template_name,
                target_count=target_count,
                last_message="开始执行任务...",
            )
            self._status_text = "任务执行中..."
            self._connection_error = None

        stop_checker = stop_requested or (lambda: False)
        sent_count = 0
        failed_count = 0
        daily_stats = _load_daily_stats()
        # 空转熔断：连续多轮没有任何新成功（翻页无效、页面卡死等）时自动停止
        empty_rounds = 0
        stalled = False

        # 记录招募任务开始
        from datetime import datetime

        task_id = datetime.now().strftime("%Y%m%d-%H%M%S")
        task_record = {
            "task_id": task_id,
            "start_time": datetime.now().astimezone().isoformat(),
            "end_time": None,
            "template_id": execution_template_id,
            "template_name": execution_template_name,
            "target_count": target_count,
            "success_count": 0,
            "failed_count": 0,
            "status": "running",
            "last_message": "开始执行任务...",
        }
        _append_recruitment_task(task_record)

        self._emit_log("info", "开始执行任务...")
        self._emit_log("info", f"使用模板: {execution_template_name}")
        self._emit_log("info", f"计划发送数量: {target_count} 个")

        try:
            assert self._rpa is not None
            self._rpa.current_template_name = execution_template_name
            self._rpa.reset_failure_tracking()

            while sent_count < target_count and not stop_checker():
                self._emit_log("info", "正在获取 publisher 列表...")
                publisher_ids = self._rpa.get_publisher_ids()

                if not publisher_ids:
                    self._emit_log(
                        "info",
                        "当前页面没有可邀请的 publisher，尝试下一页",
                    )
                    self._rpa.click_next_page()
                    continue

                self._emit_log(
                    "info",
                    f"当前页面找到 {len(publisher_ids)} 个可邀请的 publisher",
                )

                found_new = False
                for publisher_id in publisher_ids:
                    if stop_checker():
                        break

                    if sent_count >= target_count:
                        break

                    if self._rpa.has_clicked_publisher(publisher_id):
                        continue

                    self._emit_log(
                        "info",
                        f"正在向 publisher {publisher_id} 发送邀请...",
                    )
                    success = self._rpa.send_invite_to_publisher(
                        publisher_id, execution_message
                    )
                    if success:
                        found_new = True
                        sent_count += 1
                        daily_stats["success_count"] += 1
                        self._update_execution_progress(
                            sent_count=sent_count,
                            failed_count=failed_count,
                            last_message=(
                                f"第 {sent_count}/{target_count} 条消息发送成功 "
                                f"(publisher: {publisher_id})"
                            ),
                        )
                        # 实时更新历史记录与每日统计
                        _save_daily_stats(daily_stats)
                        _update_recruitment_task(
                            task_id,
                            {
                                "success_count": sent_count,
                                "failed_count": failed_count,
                                "last_message": self._execution_state.last_message,
                            },
                        )
                        if progress_callback:
                            progress_callback()
                        self._emit_log(
                            "success",
                            self._execution_state.last_message,
                        )
                        break

                    failed_count += 1
                    daily_stats["failed_count"] += 1
                    self._update_execution_progress(
                        sent_count=sent_count,
                        failed_count=failed_count,
                        last_message=(
                            f"发送失败 (publisher: {publisher_id})"
                        ),
                    )
                    # 实时更新历史记录与每日统计
                    _save_daily_stats(daily_stats)
                    _update_recruitment_task(
                        task_id,
                        {
                            "success_count": sent_count,
                            "failed_count": failed_count,
                            "last_message": self._execution_state.last_message,
                        },
                    )
                    if progress_callback:
                        progress_callback()
                    self._emit_log(
                        "error",
                        self._execution_state.last_message,
                    )

                if found_new:
                    empty_rounds = 0
                else:
                    empty_rounds += 1
                    if empty_rounds >= MAX_EMPTY_ROUNDS:
                        stalled = True
                        self._emit_log(
                            "error",
                            f"连续 {MAX_EMPTY_ROUNDS} 轮没有新的成功邀请，已自动停止任务",
                        )
                        break

                if not found_new and not stop_checker():
                    self._emit_log(
                        "info",
                        "当前页所有 ID 都已处理，进入下一页",
                    )
                    self._rpa.click_next_page()

            _save_daily_stats(daily_stats)
            stopped = stop_checker() and sent_count < target_count
            completed = not stopped and not stalled
            if stopped:
                status = "stopped"
                last_message = "任务已手动停止"
            elif stalled:
                status = "error"
                last_message = (
                    f"连续 {MAX_EMPTY_ROUNDS} 轮没有新的成功邀请，已自动停止。"
                    f"共发送 {sent_count} 条邀请，失败 {failed_count} 条，"
                    "请检查浏览器页面状态后重新执行"
                )
            else:
                status = "completed"
                last_message = (
                    f"任务执行完成！共发送 {sent_count} 条邀请，失败 {failed_count} 条"
                )
            # 更新招募历史记录
            from datetime import datetime

            _update_recruitment_task(
                task_id,
                {
                    "end_time": datetime.now().astimezone().isoformat(),
                    "success_count": sent_count,
                    "failed_count": failed_count,
                    "status": status,
                    "last_message": last_message,
                },
            )
            self._finish_execution(
                sent_count=sent_count,
                failed_count=failed_count,
                target_count=target_count,
                last_message=last_message,
                stopped=stopped or stalled,
                error_message=None,
            )
            self._emit_log(
                "error" if (stopped or stalled) else "success",
                last_message,
            )
            return ExecutionResultViewModel(
                sent_count=sent_count,
                failed_count=failed_count,
                target_count=target_count,
                stopped=stopped,
                completed=completed,
                last_message=last_message,
            )
        except PageDisconnectedError as error:
            _save_daily_stats(daily_stats)
            # 更新招募历史记录
            from datetime import datetime

            _update_recruitment_task(
                task_id,
                {
                    "end_time": datetime.now().astimezone().isoformat(),
                    "success_count": sent_count,
                    "failed_count": failed_count,
                    "status": "error",
                    "last_message": "浏览器标签页已断开",
                },
            )
            # 标签页断开时，标记浏览器为已断开，让 UI 反映真实状态
            with self._lock:
                self._rpa = None
                self._connection_error = "浏览器标签页已断开"
                self._status_text = "浏览器已断开"
            friendly_msg = "浏览器标签页已断开，请重新连接浏览器后再执行"
            self._emit_log("error", friendly_msg)
            self._finish_execution(
                sent_count=sent_count,
                failed_count=failed_count,
                target_count=target_count,
                last_message=friendly_msg,
                stopped=False,
                error_message=friendly_msg,
            )
            logger.error(f"浏览器标签页断开: {error}")
            raise RuntimeError(friendly_msg) from error
        except Exception as error:
            _save_daily_stats(daily_stats)
            # 更新招募历史记录
            from datetime import datetime

            _update_recruitment_task(
                task_id,
                {
                    "end_time": datetime.now().astimezone().isoformat(),
                    "success_count": sent_count,
                    "failed_count": failed_count,
                    "status": "error",
                    "last_message": f"执行出错: {error}",
                },
            )
            self._finish_execution(
                sent_count=sent_count,
                failed_count=failed_count,
                target_count=target_count,
                last_message=f"执行出错: {error}",
                stopped=False,
                error_message=str(error),
            )
            logger.exception("执行出错")
            raise RuntimeError(f"执行出错: {error}") from error

    def _initialize_runtime(self) -> None:
        """初始化运行时依赖。"""
        self.settings_repository = self._settings_factory()
        templates = self._load_templates()
        active_id = self._get_active_template_id(templates)
        self._selected_template_id = active_id or (
            templates[0].id if templates else None
        )
        self._status_text = "未连接浏览器"
        self._connection_error = None
        self._execution_state = ExecutionStateViewModel()

    def _ensure_bootstrapped(self) -> None:
        """确保服务已经完成初始化。"""
        if not self._bootstrapped:
            self._initialize_runtime()
            self._bootstrapped = True

    def _ensure_template_mutation_allowed(self) -> None:
        """确保执行期间不会发生模板变更。"""
        if self._execution_state.is_running:
            raise ValueError("执行期间不允许修改模板。")

    def _load_templates(self) -> list[InvitationTemplate]:
        """读取当前模板列表。"""
        return self.template_repository.ensure_default_templates()

    def _save_templates(self, templates: list[InvitationTemplate]) -> None:
        """保存模板到本地存储。"""
        self.template_repository.save_templates(templates)

    def _normalize_notify_channel(self, channel: NotifyChannel | str) -> NotifyChannel:
        """规范化通知渠道。"""
        normalized = str(channel or "").strip().lower()
        if normalized in {"desktop", "feishu", "both", "none"}:
            return normalized  # type: ignore[return-value]
        return "desktop"

    def _get_active_template_id(
        self, templates: list[InvitationTemplate]
    ) -> int | None:
        """根据配置中的激活索引解析当前激活模板。"""
        active_index = self.settings_repository.active_template_index
        if active_index < 0 or active_index >= len(templates):
            return None
        return templates[active_index].id

    def _repair_active_template_index(
        self,
        templates: list[InvitationTemplate],
        previous_active_template_id: int | None,
    ) -> None:
        """在模板变更后修复激活索引。"""
        if not templates:
            self.settings_repository.active_template_index = -1
            return

        if previous_active_template_id is not None:
            for index, template in enumerate(templates):
                if template.id == previous_active_template_id:
                    self.settings_repository.active_template_index = index
                    return

        self.settings_repository.active_template_index = 0

    def _resolve_template(self, template_id: int | None) -> InvitationTemplate:
        """解析当前要执行的模板。"""
        templates = self._load_templates()
        candidate_id = template_id or self._selected_template_id or self._get_active_template_id(
            templates
        )

        if candidate_id is None:
            raise ValueError("请先选择一个模板。")

        for template in templates:
            if template.id == candidate_id:
                self._selected_template_id = candidate_id
                return template

        raise ValueError("选中的模板不存在。")

    def _build_state(self) -> AppRuntimeStateViewModel:
        """构建当前运行态视图模型。"""
        templates = self._load_templates()
        active_template_id = self._get_active_template_id(templates)
        available_template_ids = {template.id for template in templates}
        if self._selected_template_id not in available_template_ids:
            self._selected_template_id = None
        if self._selected_template_id is None and active_template_id is not None:
            self._selected_template_id = active_template_id
        if self._selected_template_id is None and templates:
            self._selected_template_id = templates[0].id

        settings_snapshot = self.settings_repository.snapshot()
        clicked_records_count = (
            self._rpa.clicked_publisher_count()
            if self._rpa is not None
            else len(self._clicked_ids_loader(self._clicked_ids_path))
        )

        daily_stats_raw = _load_daily_stats()
        history_raw = _load_recruitment_history()
        recruitment_history = [
            RecruitmentTaskViewModel(**record) for record in history_raw
        ]

        return AppRuntimeStateViewModel(
            templates=TemplatePanelViewModel(
                templates=templates,
                selected_template_id=self._selected_template_id,
                active_template_id=active_template_id,
            ),
            settings=SettingsViewModel(
                send_count=settings_snapshot.send_count,
                notify_channel=settings_snapshot.notify_channel,
                feishu_webhook_url=settings_snapshot.feishu_webhook_url,
            ),
            connection=BrowserConnectionViewModel(
                browser_connected=self._rpa is not None,
                clicked_records_count=clicked_records_count,
                status_text=self._status_text,
                last_error=self._connection_error,
            ),
            execution=self._execution_state.model_copy(deep=True),
            daily_stats=DailyStatsViewModel(
                date=daily_stats_raw.get("date", ""),
                success_count=daily_stats_raw.get("success_count", 0),
                failed_count=daily_stats_raw.get("failed_count", 0),
            ),
            recruitment_history=recruitment_history,
        )

    def _update_execution_progress(
        self, sent_count: int, failed_count: int, last_message: str
    ) -> None:
        """更新执行进度状态。"""
        with self._lock:
            self._execution_state.sent_count = sent_count
            self._execution_state.failed_count = failed_count
            self._execution_state.last_message = last_message
            self._status_text = last_message

    def _finish_execution(
        self,
        sent_count: int,
        failed_count: int,
        target_count: int,
        last_message: str,
        stopped: bool,
        error_message: str | None,
    ) -> None:
        """结束执行并落盘运行态。"""
        with self._lock:
            self._execution_state.is_running = False
            self._execution_state.sent_count = sent_count
            self._execution_state.failed_count = failed_count
            self._execution_state.target_count = target_count
            self._execution_state.last_message = last_message
            self._execution_state.last_error = error_message
            self._status_text = "已停止" if stopped else "执行完成，等待下一次执行..."
            self._connection_error = error_message

    def _emit_log(self, level: str, message: str) -> None:
        """通过 loguru 输出一条执行日志，UI 层通过 sink 实时接收。"""
        level_lower = level.lower()
        if level_lower == "success":
            logger.log("SUCCESS", message)
        elif level_lower == "error":
            logger.error(message)
        elif level_lower == "warning":
            logger.warning(message)
        else:
            logger.info(message)
