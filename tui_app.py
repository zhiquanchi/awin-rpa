"""
AWIN RPA - 模板管理和预览 TUI 界面
基于 Textual 框架
"""

from collections.abc import Callable
from datetime import datetime

import pyperclip
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import (
    Header,
    Static,
    Button,
    ListView,
    ListItem,
    TextArea,
    Input,
    Label,
    RichLog,
)
from textual.reactive import reactive
from textual.binding import Binding
from textual.screen import ModalScreen
from textual import work

from app_interfaces import (
    AppRuntimeStateViewModel,
    InvitationTemplate,
    SettingsRepositoryProtocol,
    SyncServiceProtocol,
    TemplateRepositoryProtocol,
)
from awin_application_service import AwinApplicationService
from main import Updater
from config_manager import ConfigManager
from json_template_repository import JsonTemplateRepository
from remote_sync_service import RemoteSyncService, get_machine_uid
from loguru import logger


class PasteableTextArea(TextArea):
    """支持 Ctrl+V 粘贴的 TextArea"""
    
    BINDINGS = [
        Binding("ctrl+v", "paste", "粘贴", show=False),
    ]
    
    def action_paste(self) -> None:
        """粘贴剪贴板内容"""
        if self.read_only:
            return
        try:
            text = pyperclip.paste()
            if text:
                self.insert(text)
        except Exception:
            pass


class ConfirmDialog(ModalScreen[bool]):
    """通用确认弹窗"""

    CSS = """
    ConfirmDialog {
        background: rgba(0, 0, 0, 0.5);
    }
    
    #confirm-container {
        width: 100%;
        height: 100%;
        align: center middle;
    }
    
    #confirm-dialog {
        width: 50;
        height: 12;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }
    
    #confirm-title {
        width: 100%;
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
    }
    
    #confirm-message {
        width: 100%;
        text-align: center;
        margin-bottom: 1;
    }
    
    #confirm-buttons {
        width: 100%;
        align: center middle;
        height: auto;
        margin-top: 1;
    }
    
    #confirm-buttons Button {
        margin: 0 2;
    }
    """

    def __init__(self, title: str = "确认", message: str = "确定吗？"):
        super().__init__()
        self.title_text = title
        self.message_text = message

    def compose(self) -> ComposeResult:
        with Container(id="confirm-container"):
            with Vertical(id="confirm-dialog"):
                yield Static(self.title_text, id="confirm-title")
                yield Static(self.message_text, id="confirm-message")
                with Horizontal(id="confirm-buttons"):
                    yield Button("确定", id="btn-confirm-yes", variant="error")
                    yield Button("取消", id="btn-confirm-no", variant="default")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-confirm-yes":
            self.dismiss(True)
        else:
            self.dismiss(False)


class ConfirmQuitScreen(ConfirmDialog):
    """确认退出弹窗"""
    
    def __init__(self):
        super().__init__(title="确认退出", message="确定要退出应用吗？")


class TemplateListItem(ListItem):
    """模板列表项"""

    def __init__(
        self, template: InvitationTemplate, is_active: bool = False
    ) -> None:
        super().__init__()
        self.template = template
        self.is_active = is_active

    def compose(self) -> ComposeResult:
        # 激活的模板显示 [*] 标记
        prefix = "[*] " if self.is_active else "    "
        yield Label(f"{prefix}{self.template.name}")


class TemplateManagerApp(App):
    """模板管理和预览 TUI 应用"""

    CSS_PATH = "tui_app.tcss"
    TITLE = "AWIN RPA - 模板管理和预览"

    BINDINGS = [
        Binding("ctrl+q", "request_quit", "退出", priority=True),
    ]

    # 响应式状态
    selected_template: reactive[InvitationTemplate | None] = reactive(None)
    is_template_editing: reactive[bool] = reactive(False)
    is_count_editing: reactive[bool] = reactive(False)
    is_notify_editing: reactive[bool] = reactive(False)
    send_count: reactive[int] = reactive(10)
    is_running: reactive[bool] = reactive(False)

    def __init__(
        self,
        template_repository: TemplateRepositoryProtocol | None = None,
        config_manager_factory: Callable[[], SettingsRepositoryProtocol] | None = None,
        sync_service_factory: (
            Callable[[str, str], SyncServiceProtocol] | None
        ) = None,
        uid_provider: Callable[[], str] | None = None,
    ) -> None:
        super().__init__()

        config_factory = config_manager_factory or ConfigManager
        sync_factory = sync_service_factory or RemoteSyncService
        uid_factory = uid_provider or get_machine_uid
        self.template_repository = template_repository or JsonTemplateRepository()
        self.app_service = AwinApplicationService(
            template_repository=self.template_repository,
            settings_factory=config_factory,
            sync_service_factory=sync_factory,
            uid_provider=uid_factory,
        )
        runtime_state = self.app_service.bootstrap()
        self.config_manager = self.app_service.settings_repository
        self.sync_service = self.app_service.sync_service
        self.uid = self.app_service.uid
        self.templates: list[InvitationTemplate] = runtime_state.templates.templates
        self.send_count = runtime_state.settings.send_count
        self.notify_channel: str = runtime_state.settings.notify_channel
        self.feishu_webhook_url: str = runtime_state.settings.feishu_webhook_url

        self.editing_content: str = ""
        self.editing_count: int = self.send_count
        self.editing_notify_channel: str = self.notify_channel
        self.editing_feishu_webhook_url: str = self.feishu_webhook_url
        self.execution_task = None

    def _load_templates(self) -> list[InvitationTemplate]:
        """从配置文件加载模板"""
        return self.template_repository.ensure_default_templates()

    def _save_templates(self) -> None:
        """保存模板到配置文件"""
        self.template_repository.save_templates(self.templates)
        self.sync_service.push_config(
            "invitation_messages",
            self.template_repository.to_sync_payload(self.templates),
        )

    def _sync_settings(self) -> None:
        """同步当前配置到远端。"""
        self.sync_service.push_config("tui_config", self.config_manager.to_sync_payload())

    def _apply_service_state(self, state: AppRuntimeStateViewModel) -> None:
        """把应用服务状态同步回当前 TUI。"""
        self.templates = state.templates.templates
        self.send_count = state.settings.send_count
        self.notify_channel = state.settings.notify_channel
        self.feishu_webhook_url = state.settings.feishu_webhook_url

    def compose(self) -> ComposeResult:
        """创建 UI 组件"""
        yield Header()

        with Container(id="main-container"):
            # 第一行：模板管理区域
            with Horizontal(id="template-section"):
                # 左侧：模板列表
                with Vertical(id="template-list-wrapper"):
                    yield Static("模板列表", classes="section-label")
                    yield ListView(
                        *[TemplateListItem(t, i == self.config_manager.active_template_index) 
                          for i, t in enumerate(self.templates)],
                        id="template-list",
                    )
                    with Horizontal(id="template-list-buttons"):
                        yield Button("增加", id="btn-add", variant="primary")
                        yield Button("删除", id="btn-delete", variant="error")
                        yield Button("激活", id="btn-activate", variant="success")

                # 中间：模板预览
                with Vertical(id="template-preview-wrapper"):
                    yield Static("模板内容预览", classes="section-label")
                    yield PasteableTextArea(
                        "",
                        id="template-preview",
                        read_only=True,
                    )

                # 右侧：按钮列
                with Vertical(id="template-action-column"):
                    yield Button("修改", id="btn-edit-template", variant="default")
                    yield Button(
                        "保存",
                        id="btn-save-template",
                        variant="success",
                        classes="hidden",
                    )
                    yield Button(
                        "取消",
                        id="btn-cancel-template",
                        variant="default",
                        classes="hidden",
                    )

            # 第二行：发送数量区域（占 10%）
            with Horizontal(id="send-count-section"):
                yield Static("发送数量", id="send-count-label")
                yield Static(str(self.send_count), id="send-count-display")
                yield Input(
                    str(self.send_count),
                    id="send-count-input",
                    type="integer",
                    classes="hidden",
                )
                yield Static("个", id="send-count-unit")

                # 右侧：按钮列
                with Vertical(id="count-action-column"):
                    yield Button("修改", id="btn-edit-count", variant="default")
                    yield Button(
                        "保存",
                        id="btn-save-count",
                        variant="success",
                        classes="hidden",
                    )
                    yield Button(
                        "取消",
                        id="btn-cancel-count",
                        variant="default",
                        classes="hidden",
                    )

                yield Static("通知渠道", id="notify-channel-label")
                yield Static(self._channel_to_text(self.notify_channel), id="notify-channel-display")
                yield Button(
                    "切换",
                    id="btn-cycle-channel",
                    variant="default",
                    classes="hidden",
                )

                yield Static("飞书 Webhook", id="feishu-webhook-label")
                yield Static(
                    self.feishu_webhook_url if self.feishu_webhook_url else "(未配置)",
                    id="feishu-webhook-display",
                )
                yield Input(
                    self.feishu_webhook_url,
                    id="feishu-webhook-input",
                    placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/...",
                    classes="hidden",
                )

                with Vertical(id="notify-action-column"):
                    yield Button("修改", id="btn-edit-notify", variant="default")
                    yield Button(
                        "保存",
                        id="btn-save-notify",
                        variant="success",
                        classes="hidden",
                    )
                    yield Button(
                        "取消",
                        id="btn-cancel-notify",
                        variant="default",
                        classes="hidden",
                    )

            # 第三行：执行区域（占 10%）
            with Horizontal(id="execution-section"):
                # 左侧：按钮区域（水平排列）
                with Horizontal(id="execute-button-wrapper"):
                    yield Button(
                        "开始执行", id="btn-execute", variant="success", classes="btn-large"
                    )
                    yield Button(
                        "连接浏览器", id="btn-connect", variant="primary", classes="btn-connect"
                    )
                    yield Button(
                        "重置记录", id="btn-reset-clicked", variant="warning", classes="btn-reset"
                    )
                    yield Button(
                        "检查更新", id="btn-check-update", variant="primary", classes="btn-update"
                    )

                # 中间：日志区域
                with Vertical(id="log-wrapper"):
                    yield Static("执行日志", id="log-header")
                    yield Static("未连接浏览器", id="log-status")
                    yield RichLog(id="log-content", highlight=True, markup=True)

                # 右侧：退出按钮（占 10%）
                with Vertical(id="quit-button-wrapper"):
                    yield Button(
                        "退出", id="btn-quit", variant="error", classes="btn-quit"
                    )
                    yield Static("Ctrl+Q", id="quit-hint")

    def on_mount(self) -> None:
        """挂载时初始化"""
        if self.templates:
            list_view = self.query_one("#template-list", ListView)
            # 优先选中激活的模板
            active_index = self.config_manager.active_template_index
            if 0 <= active_index < len(self.templates):
                list_view.index = active_index
                self.selected_template = self.templates[active_index]
            else:
                list_view.index = 0
                self.selected_template = self.templates[0]
            self._update_preview()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """处理列表选择事件"""
        if isinstance(event.item, TemplateListItem):
            self.selected_template = event.item.template
            self._update_preview()

    def _update_preview(self) -> None:
        """更新预览内容"""
        preview = self.query_one("#template-preview", PasteableTextArea)
        if self.selected_template:
            preview.load_text(self.selected_template.content)
        else:
            preview.load_text("")

    def _refresh_template_list(self) -> None:
        """刷新模板列表"""
        list_view = self.query_one("#template-list", ListView)
        list_view.clear()
        active_index = self.config_manager.active_template_index
        for i, template in enumerate(self.templates):
            list_view.append(TemplateListItem(template, is_active=(i == active_index)))

    def _add_log(self, log_type: str, message: str) -> None:
        """添加日志"""
        log = self.query_one("#log-content", RichLog)
        time_str = datetime.now().strftime("%H:%M:%S")

        if log_type == "success":
            log.write(f"[green][{time_str}] {message}[/green]")
        elif log_type == "error":
            log.write(f"[red][{time_str}] {message}[/red]")
        else:  # info
            log.write(f"[blue][{time_str}] {message}[/blue]")
        
        # 更新状态文本
        self._update_status(message)

    def _update_status(self, message: str) -> None:
        """更新状态文本"""
        try:
            status = self.query_one("#log-status", Static)
            # 截取消息前50个字符作为状态
            status_text = message[:50] + "..." if len(message) > 50 else message
            status.update(status_text)
        except Exception:
            pass

    # ===== 模板操作 =====

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """处理按钮点击事件"""
        button_id = event.button.id

        if button_id == "btn-add":
            self.action_add_template()
        elif button_id == "btn-delete":
            self.action_confirm_delete()
        elif button_id == "btn-activate":
            self.action_activate_template()
        elif button_id == "btn-edit-template":
            self.action_edit_template()
        elif button_id == "btn-save-template":
            self.action_save_template()
        elif button_id == "btn-cancel-template":
            self.action_cancel_edit()
        elif button_id == "btn-edit-count":
            self._start_count_edit()
        elif button_id == "btn-save-count":
            self._save_count_edit()
        elif button_id == "btn-cancel-count":
            self._cancel_count_edit()
        elif button_id == "btn-edit-notify":
            self._start_notify_edit()
        elif button_id == "btn-save-notify":
            self._save_notify_edit()
        elif button_id == "btn-cancel-notify":
            self._cancel_notify_edit()
        elif button_id == "btn-cycle-channel":
            self._cycle_notify_channel()
        elif button_id == "btn-connect":
            self.action_connect_browser()
        elif button_id == "btn-execute":
            self.action_start_execution()
        elif button_id == "btn-reset-clicked":
            self.action_reset_clicked()
        elif button_id == "btn-check-update":
            self.action_check_update()
        elif button_id == "btn-quit":
            self.action_request_quit()

    def action_check_update(self) -> None:
        """检查更新"""
        self._add_log("info", "正在检查更新...")
        self._check_update_worker()

    @work(exclusive=True, thread=True)
    def _check_update_worker(self) -> None:
        """后台检查更新"""
        try:
            updater = Updater()
            result = updater.check_for_updates()

            if result["error"]:
                self.call_from_thread(self._add_log, "error", f"检查失败: {result['error']}")
                return

            if not result["has_update"]:
                self.call_from_thread(
                    self._add_log, "success",
                    f"已是最新版本 ({result['local_version']})"
                )
                self.call_from_thread(
                    self.notify,
                    f"当前已是最新版本 ({result['local_version']})"
                )
                return

            # 有更新，弹出确认框
            self.call_from_thread(
                self._add_log, "info",
                f"发现新版本: {result['local_version']} → {result['remote_version']}"
            )
            self.call_from_thread(
                self.push_screen,
                ConfirmDialog(
                    title="发现新版本",
                    message=f"{result['local_version']} → {result['remote_version']}，是否更新？"
                ),
                self._handle_update_confirm
            )
        except Exception as e:
            logger.error(f"检查更新出错: {e}")
            self.call_from_thread(self._add_log, "error", f"检查更新出错: {e}")

    def _handle_update_confirm(self, confirmed: bool) -> None:
        """处理更新确认结果"""
        if not confirmed:
            self._add_log("info", "已取消更新")
            return
        self._add_log("info", "正在下载更新...")
        self._download_update_worker()

    @work(exclusive=True, thread=True)
    def _download_update_worker(self) -> None:
        """后台下载更新文件"""
        try:
            updater = Updater()

            def on_progress(filename, index, total):
                self.call_from_thread(
                    self._add_log, "info",
                    f"[{index}/{total}] 正在更新 {filename}..."
                )

            result = updater.download_updates(on_progress=on_progress)

            if result["success"]:
                self.call_from_thread(self._add_log, "success", result["message"])
                self.call_from_thread(
                    self.notify,
                    "更新完成，请重新启动程序以生效",
                    severity="information"
                )
            else:
                self.call_from_thread(self._add_log, "error", result["message"])
                self.call_from_thread(
                    self.notify,
                    "更新失败，已回滚",
                    severity="error"
                )
        except Exception as e:
            logger.error(f"下载更新出错: {e}")
            self.call_from_thread(self._add_log, "error", f"下载更新出错: {e}")

    def action_reset_clicked(self) -> None:
        """重置已点击记录（显示确认弹窗）"""
        count = self.app_service.get_state().connection.clicked_records_count

        if count == 0:
            self.notify("当前没有已点击记录，无需重置", severity="warning")
            return

        self.push_screen(
            ConfirmDialog(
                title="重置已点击记录",
                message=f"共 {count} 条记录，清空后可重新发送邀请。确认？"
            ),
            self._handle_reset_confirm
        )

    def _handle_reset_confirm(self, confirmed: bool) -> None:
        """处理重置确认结果"""
        if not confirmed:
            return

        try:
            cleared, service_state = self.app_service.reset_clicked_ids()
        except Exception as error:
            logger.error(f"重置已点击记录失败: {error}")
            self.notify("重置失败", severity="error")
            return

        self._apply_service_state(service_state)
        self._add_log("success", f"已清除 {cleared} 条已点击记录")
        self.notify(f"已成功清除 {cleared} 条已点击记录")

    def action_connect_browser(self) -> None:
        """连接浏览器"""
        # 检查是否有激活的模板
        active_index = self.config_manager.active_template_index
        if active_index < 0 or active_index >= len(self.templates):
            self.notify("请先激活一个模板", severity="warning")
            return

        if self.app_service.get_state().connection.browser_connected:
            self.notify("浏览器已连接", severity="warning")
            return

        self._add_log("info", "正在连接浏览器...")
        self._connect_browser()

    @work(exclusive=True, thread=True)
    def _connect_browser(self) -> None:
        """后台连接浏览器"""
        try:
            service_state = self.app_service.connect_browser()
            self.call_from_thread(self._on_browser_connected, service_state)
        except Exception as error:
            logger.error(f"连接浏览器失败: {error}")
            self.call_from_thread(self._add_log, "error", f"连接浏览器失败: {error}")
            self.call_from_thread(self._update_status, "连接失败")

    def _on_browser_connected(self, state: AppRuntimeStateViewModel) -> None:
        """浏览器连接成功回调"""
        self._apply_service_state(state)
        self._add_log("success", "浏览器连接成功")
        self._update_status("浏览器已连接，等待执行...")

        # 更新连接按钮状态
        btn = self.query_one("#btn-connect", Button)
        btn.label = "已连接"
        btn.variant = "success"
        btn.disabled = True

    def action_request_quit(self) -> None:
        """请求退出（显示确认弹窗）"""
        self.push_screen(ConfirmQuitScreen(), self._handle_quit_confirm)

    def _handle_quit_confirm(self, confirmed: bool) -> None:
        """处理退出确认结果"""
        if confirmed:
            self.exit()

    def action_confirm_delete(self) -> None:
        """确认删除模板（显示确认弹窗）"""
        if not self.selected_template:
            self.notify("请先选择要删除的模板", severity="warning")
            return
        
        template_name = self.selected_template.name
        self.push_screen(
            ConfirmDialog(
                title="确认删除",
                message=f"确定要删除模板 '{template_name}' 吗？"
            ),
            self._handle_delete_confirm
        )

    def _handle_delete_confirm(self, confirmed: bool) -> None:
        """处理删除确认结果"""
        if confirmed:
            self.action_delete_template()

    def action_activate_template(self) -> None:
        """激活当前选中的模板"""
        if not self.selected_template:
            self.notify("请先选择要激活的模板", severity="warning")
            return
        
        # 获取当前选中模板的索引
        try:
            index = next(i for i, t in enumerate(self.templates) if t.id == self.selected_template.id)
        except StopIteration:
            self.notify("无法找到模板", severity="error")
            return
        
        # 保存到配置
        self.config_manager.active_template_index = index
        # 同步到云端
        self._sync_settings()
        
        # 刷新列表以显示激活标记
        self._refresh_template_list()
        
        self.notify(f"模板 '{self.selected_template.name}' 已激活")
        self._add_log("success", f"激活模板: {self.selected_template.name}")

    def action_add_template(self) -> None:
        """增加模板"""
        new_id = max((t.id for t in self.templates), default=0) + 1
        new_template = InvitationTemplate(
            id=new_id,
            name=f"新模板{new_id}",
            content="请输入模板内容...",
        )
        self.templates.append(new_template)
        self._save_templates()  # 保存到配置文件
        self._refresh_template_list()
        self.selected_template = new_template
        self._update_preview()
        self.notify("模板已添加并保存")
        self._add_log("success", f"新增模板: {new_template.name}")

    def action_delete_template(self) -> None:
        """删除模板"""
        if not self.selected_template:
            self.notify("请先选择要删除的模板", severity="warning")
            return

        template_name = self.selected_template.name
        self.templates = [t for t in self.templates if t.id != self.selected_template.id]
        self._save_templates()  # 保存到配置文件
        self._refresh_template_list()

        if self.templates:
            self.selected_template = self.templates[0]
            list_view = self.query_one("#template-list", ListView)
            list_view.index = 0
        else:
            self.selected_template = None

        self._update_preview()
        self.notify(f"模板 '{template_name}' 已删除")
        self._add_log("info", f"删除模板: {template_name}")

    def action_edit_template(self) -> None:
        """开始编辑模板"""
        if not self.selected_template:
            self.notify("请先选择要修改的模板", severity="warning")
            return

        self.is_template_editing = True
        self.editing_content = self.selected_template.content

        # 切换预览区域为可编辑
        preview = self.query_one("#template-preview", PasteableTextArea)
        preview.read_only = False
        preview.focus()

        # 切换按钮显示
        self.query_one("#btn-edit-template").add_class("hidden")
        self.query_one("#btn-save-template").remove_class("hidden")
        self.query_one("#btn-cancel-template").remove_class("hidden")

    def action_save_template(self) -> None:
        """保存模板编辑"""
        if not self.is_template_editing or not self.selected_template:
            return

        preview = self.query_one("#template-preview", PasteableTextArea)
        self.selected_template.content = preview.text
        self._save_templates()  # 保存到配置文件
        preview.read_only = True

        self.is_template_editing = False

        # 切换按钮显示
        self.query_one("#btn-edit-template").remove_class("hidden")
        self.query_one("#btn-save-template").add_class("hidden")
        self.query_one("#btn-cancel-template").add_class("hidden")

        self._add_log("success", f"模板 '{self.selected_template.name}' 保存成功")

    def action_cancel_edit(self) -> None:
        """取消模板编辑"""
        if not self.is_template_editing:
            return

        preview = self.query_one("#template-preview", PasteableTextArea)
        preview.load_text(self.selected_template.content if self.selected_template else "")
        preview.read_only = True

        self.is_template_editing = False

        # 切换按钮显示
        self.query_one("#btn-edit-template").remove_class("hidden")
        self.query_one("#btn-save-template").add_class("hidden")
        self.query_one("#btn-cancel-template").add_class("hidden")

    # ===== 发送数量操作 =====

    def _start_count_edit(self) -> None:
        """开始编辑发送数量"""
        self.is_count_editing = True
        self.editing_count = self.send_count

        # 切换显示
        self.query_one("#send-count-display").add_class("hidden")
        count_input = self.query_one("#send-count-input", Input)
        count_input.remove_class("hidden")
        count_input.value = str(self.send_count)
        count_input.focus()

        # 切换按钮显示
        self.query_one("#btn-edit-count").add_class("hidden")
        self.query_one("#btn-save-count").remove_class("hidden")
        self.query_one("#btn-cancel-count").remove_class("hidden")

    def _save_count_edit(self) -> None:
        """保存发送数量"""
        if not self.is_count_editing:
            return

        count_input = self.query_one("#send-count-input", Input)
        try:
            new_count = int(count_input.value)
            if new_count < 1:
                new_count = 1
            self.send_count = new_count
            # 保存到配置文件
            self.config_manager.send_count = new_count
        except ValueError:
            pass

        # 更新显示
        self.query_one("#send-count-display", Static).update(str(self.send_count))
        self.query_one("#send-count-display").remove_class("hidden")
        self.query_one("#send-count-input").add_class("hidden")

        self.is_count_editing = False

        # 切换按钮显示
        self.query_one("#btn-edit-count").remove_class("hidden")
        self.query_one("#btn-save-count").add_class("hidden")
        self.query_one("#btn-cancel-count").add_class("hidden")

        # 同步到云端
        self._sync_settings()

        self._add_log("success", f"发送数量已更新为 {self.send_count} 个（已保存）")

    def _cancel_count_edit(self) -> None:
        """取消编辑发送数量"""
        if not self.is_count_editing:
            return

        # 恢复显示
        self.query_one("#send-count-display").remove_class("hidden")
        self.query_one("#send-count-input").add_class("hidden")

        self.is_count_editing = False

        # 切换按钮显示
        self.query_one("#btn-edit-count").remove_class("hidden")
        self.query_one("#btn-save-count").add_class("hidden")
        self.query_one("#btn-cancel-count").add_class("hidden")

    # ===== 通知配置操作 =====

    @staticmethod
    def _channel_to_text(channel: str) -> str:
        mapping = {
            "desktop": "仅本地通知",
            "feishu": "仅飞书通知",
            "both": "本地 + 飞书",
            "none": "不通知",
        }
        return mapping.get(channel, "仅本地通知")

    def _cycle_notify_channel(self) -> None:
        """循环切换通知渠道（仅在编辑状态可用）"""
        if not self.is_notify_editing:
            return
        order = ["desktop", "feishu", "both", "none"]
        try:
            idx = order.index(self.editing_notify_channel)
        except ValueError:
            idx = 0
        self.editing_notify_channel = order[(idx + 1) % len(order)]
        self.query_one("#notify-channel-display", Static).update(
            self._channel_to_text(self.editing_notify_channel)
        )

    def _start_notify_edit(self) -> None:
        """开始编辑通知配置"""
        self.is_notify_editing = True
        self.editing_notify_channel = self.notify_channel
        self.editing_feishu_webhook_url = self.feishu_webhook_url

        self.query_one("#btn-edit-notify").add_class("hidden")
        self.query_one("#btn-save-notify").remove_class("hidden")
        self.query_one("#btn-cancel-notify").remove_class("hidden")
        self.query_one("#btn-cycle-channel").remove_class("hidden")
        self.query_one("#feishu-webhook-display").add_class("hidden")

        webhook_input = self.query_one("#feishu-webhook-input", Input)
        webhook_input.remove_class("hidden")
        webhook_input.value = self.feishu_webhook_url
        webhook_input.focus()

    def _save_notify_edit(self) -> None:
        """保存通知配置"""
        if not self.is_notify_editing:
            return

        webhook_input = self.query_one("#feishu-webhook-input", Input)
        webhook_url = webhook_input.value.strip()
        if self.editing_notify_channel in {"feishu", "both"} and not webhook_url:
            self.notify("开启飞书通知时必须填写 Webhook URL", severity="error")
            webhook_input.focus()
            return

        self.notify_channel = self.editing_notify_channel
        self.feishu_webhook_url = webhook_url
        self.config_manager.notify_channel = self.notify_channel
        self.config_manager.feishu_webhook_url = self.feishu_webhook_url

        self.query_one("#notify-channel-display", Static).update(
            self._channel_to_text(self.notify_channel)
        )
        self.query_one("#feishu-webhook-display", Static).update(
            self.feishu_webhook_url if self.feishu_webhook_url else "(未配置)"
        )

        self.query_one("#btn-edit-notify").remove_class("hidden")
        self.query_one("#btn-save-notify").add_class("hidden")
        self.query_one("#btn-cancel-notify").add_class("hidden")
        self.query_one("#btn-cycle-channel").add_class("hidden")
        self.query_one("#feishu-webhook-display").remove_class("hidden")
        self.query_one("#feishu-webhook-input").add_class("hidden")
        self.is_notify_editing = False

        # 同步到云端
        self._sync_settings()

        self._add_log(
            "success",
            f"通知配置已保存：{self._channel_to_text(self.notify_channel)}",
        )

    def _cancel_notify_edit(self) -> None:
        """取消编辑通知配置"""
        if not self.is_notify_editing:
            return

        self.query_one("#notify-channel-display", Static).update(
            self._channel_to_text(self.notify_channel)
        )
        self.query_one("#feishu-webhook-display", Static).update(
            self.feishu_webhook_url if self.feishu_webhook_url else "(未配置)"
        )

        self.query_one("#btn-edit-notify").remove_class("hidden")
        self.query_one("#btn-save-notify").add_class("hidden")
        self.query_one("#btn-cancel-notify").add_class("hidden")
        self.query_one("#btn-cycle-channel").add_class("hidden")
        self.query_one("#feishu-webhook-display").remove_class("hidden")
        self.query_one("#feishu-webhook-input").add_class("hidden")
        self.is_notify_editing = False

    # ===== 执行操作 =====

    def action_start_execution(self) -> None:
        """开始/停止执行"""
        if self.is_running:
            self._stop_execution(manual=True)
        else:
            self._start_execution()

    def _start_execution(self) -> None:
        """开始执行"""
        if not self.selected_template:
            self.notify("请先选择一个模板", severity="warning")
            return

        if not self.app_service.get_state().connection.browser_connected:
            self.notify("请先连接浏览器", severity="warning")
            return

        self.is_running = True
        btn = self.query_one("#btn-execute", Button)
        btn.label = "停止执行"
        btn.variant = "error"

        # 切换布局：日志区域扩大到 60%
        self.query_one("#template-section").add_class("running")
        self.query_one("#send-count-section").add_class("running")
        self.query_one("#execution-section").add_class("running")

        self._add_log("info", "开始执行任务...")
        self._add_log("info", f"使用模板: {self.selected_template.name}")
        self._add_log("info", f"计划发送数量: {self.send_count} 个")
        
        # 启动后台任务
        self._run_execution()

    @work(exclusive=True, thread=True)
    def _run_execution(self) -> None:
        """后台执行 RPA 任务（在独立线程中运行）"""
        try:
            selected_template_id = self.selected_template.id if self.selected_template else None
            self.app_service.select_template(selected_template_id)
            self.app_service.execute_invites(
                template_id=selected_template_id,
                stop_requested=lambda: not self.is_running,
                log_callback=lambda entry: self.call_from_thread(
                    self._add_log, entry.level, entry.message
                ),
            )
            self.call_from_thread(
                self._apply_service_state, self.app_service.get_state()
            )
            self.call_from_thread(self._stop_execution)
        except Exception as error:
            logger.error(f"RPA 执行出错: {error}")
            self.call_from_thread(self._add_log, "error", f"执行出错: {error}")
            self.call_from_thread(
                self._apply_service_state, self.app_service.get_state()
            )
            self.call_from_thread(self._stop_execution)

    def _stop_execution(self, manual: bool = False) -> None:
        """停止执行"""
        was_running = self.is_running
        self.is_running = False

        btn = self.query_one("#btn-execute", Button)
        btn.label = "开始执行"
        btn.variant = "success"

        # 恢复布局
        self.query_one("#template-section").remove_class("running")
        self.query_one("#send-count-section").remove_class("running")
        self.query_one("#execution-section").remove_class("running")

        if manual and was_running:
            self._add_log("error", "任务已手动停止")
            self._update_status("已停止")
        elif not manual:
            self._update_status("执行完成，等待下一次执行...")


def main():
    """入口函数"""
    app = TemplateManagerApp()
    app.run()


if __name__ == "__main__":
    main()
