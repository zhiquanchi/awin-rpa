"""
AWIN RPA - 模板管理和预览 TUI 界面
基于 Textual 框架
"""

from datetime import datetime
from pathlib import Path
import json
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
from textual.message import Message
from textual.binding import Binding
from textual.screen import ModalScreen
from textual import work

# 导入 main.py 中的类
from main import MessageManager, AwinRPA, Updater, _load_id_set, CLICKED_IDS_PATH
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


class ConfigManager:
    """配置管理器 - 保存 TUI 相关配置"""
    
    def __init__(self, config_path: Path = None):
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
    
    def _default_config(self) -> dict:
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


class Template:
    """模板数据类"""

    def __init__(self, id: int, name: str, content: str):
        self.id = id
        self.name = name
        self.content = content
    
    def to_dict(self) -> dict:
        """转换为字典（用于保存）"""
        return {"name": self.name, "content": self.content}
    
    @classmethod
    def from_dict(cls, data: dict, id: int) -> "Template":
        """从字典创建（用于加载）"""
        return cls(id=id, name=data.get("name", ""), content=data.get("content", ""))


class TemplateListItem(ListItem):
    """模板列表项"""

    def __init__(self, template: Template, is_active: bool = False) -> None:
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
    selected_template: reactive[Template | None] = reactive(None)
    is_template_editing: reactive[bool] = reactive(False)
    is_count_editing: reactive[bool] = reactive(False)
    is_notify_editing: reactive[bool] = reactive(False)
    send_count: reactive[int] = reactive(10)
    is_running: reactive[bool] = reactive(False)

    def __init__(self):
        super().__init__()
        
        # 使用 main.py 中的 MessageManager 管理模板
        self.message_manager = MessageManager()
        # 配置管理器
        self.config_manager = ConfigManager()
        
        # 从配置文件加载模板
        self.templates: list[Template] = self._load_templates()
        
        # 从配置文件加载发送数量
        self.send_count = self.config_manager.send_count
        self.notify_channel: str = self.config_manager.notify_channel
        self.feishu_webhook_url: str = self.config_manager.feishu_webhook_url
        
        self.editing_content: str = ""
        self.editing_count: int = self.send_count
        self.editing_notify_channel: str = self.notify_channel
        self.editing_feishu_webhook_url: str = self.feishu_webhook_url
        self.execution_task = None
        
        # RPA 实例（延迟初始化，避免启动时连接浏览器）
        self._rpa: AwinRPA | None = None

    def _load_templates(self) -> list[Template]:
        """从配置文件加载模板"""
        # 直接读取 JSON 文件，避免 console.print 的编码问题
        file_path = self.message_manager.file_path
        messages = []
        
        if file_path.exists():
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    messages = json.load(f)
            except (json.JSONDecodeError, IOError):
                pass
        
        if not messages:
            # 如果没有模板，创建默认模板
            default_templates = [
                {"name": "默认模板", "content": "请输入模板内容..."}
            ]
            self.message_manager.save(default_templates)
            messages = default_templates
        
        return [
            Template.from_dict(msg, idx + 1)
            for idx, msg in enumerate(messages)
        ]
    
    def _save_templates(self):
        """保存模板到配置文件"""
        messages = [t.to_dict() for t in self.templates]
        self.message_manager.save(messages)

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
        if self._rpa is not None:
            count = len(self._rpa._clicked_publisher_ids)
        else:
            # 未连接浏览器时，从文件读取记录数
            count = len(_load_id_set(CLICKED_IDS_PATH))

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

        if self._rpa is not None:
            cleared = self._rpa.reset_clicked_ids()
        else:
            # 未连接浏览器时，直接清空文件
            count = len(_load_id_set(CLICKED_IDS_PATH))
            try:
                if CLICKED_IDS_PATH.exists():
                    CLICKED_IDS_PATH.write_text("", encoding="utf-8")
            except Exception as e:
                logger.error(f"清空已点击记录文件失败: {e}")
                self.notify("重置失败", severity="error")
                return
            cleared = count

        self._add_log("success", f"已清除 {cleared} 条已点击记录")
        self.notify(f"已成功清除 {cleared} 条已点击记录")

    def action_connect_browser(self) -> None:
        """连接浏览器"""
        # 检查是否有激活的模板
        active_index = self.config_manager.active_template_index
        if active_index < 0 or active_index >= len(self.templates):
            self.notify("请先激活一个模板", severity="warning")
            return
        
        if self._rpa is not None:
            self.notify("浏览器已连接", severity="warning")
            return
        
        self._add_log("info", "正在连接浏览器...")
        self._connect_browser()

    @work(exclusive=True, thread=True)
    def _connect_browser(self) -> None:
        """后台连接浏览器"""
        try:
            self._rpa = AwinRPA(
                notify_channel=self.notify_channel,
                feishu_webhook_url=self.feishu_webhook_url,
            )
            self.call_from_thread(self._on_browser_connected)
        except Exception as e:
            logger.error(f"连接浏览器失败: {e}")
            self.call_from_thread(self._add_log, "error", f"连接浏览器失败: {e}")
            self.call_from_thread(self._update_status, "连接失败")

    def _on_browser_connected(self) -> None:
        """浏览器连接成功回调"""
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
        
        # 刷新列表以显示激活标记
        self._refresh_template_list()
        
        self.notify(f"模板 '{self.selected_template.name}' 已激活")
        self._add_log("success", f"激活模板: {self.selected_template.name}")

    def action_add_template(self) -> None:
        """增加模板"""
        new_id = max((t.id for t in self.templates), default=0) + 1
        new_template = Template(new_id, f"新模板{new_id}", "请输入模板内容...")
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
        
        if self._rpa is None:
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
            # 获取模板内容和发送数量
            msg = self.selected_template.content
            invite_count = self.send_count
            sent_count = 0
            
            while sent_count < invite_count and self.is_running:
                # 每次循环都重新从网页获取所有 publisher IDs
                self.call_from_thread(self._add_log, "info", "正在获取 publisher 列表...")
                publisher_ids = self._rpa.get_publisher_ids()
                
                if not publisher_ids:
                    self.call_from_thread(self._add_log, "info", "当前页面没有可邀请的 publisher，尝试下一页")
                    self._rpa.click_next_page()
                    continue
                
                self.call_from_thread(
                    self._add_log, "info", 
                    f"当前页面找到 {len(publisher_ids)} 个可邀请的 publisher"
                )
                
                # 遍历所有 ID，找到第一个未发送过的并发送
                found_new = False
                for publisher_id in publisher_ids:
                    if not self.is_running:
                        break
                    
                    if sent_count >= invite_count:
                        break
                    
                    # 如果该 ID 已经点击过，跳过
                    if publisher_id in self._rpa._clicked_publisher_ids:
                        continue
                    
                    # 发送邀约
                    self.call_from_thread(
                        self._add_log, "info", 
                        f"正在向 publisher {publisher_id} 发送邀请..."
                    )
                    
                    success = self._rpa.send_invite_to_publisher(publisher_id, msg)
                    if success:
                        found_new = True
                        sent_count += 1
                        self.call_from_thread(
                            self._add_log, "success", 
                            f"第 {sent_count}/{invite_count} 条消息发送成功 (publisher: {publisher_id})"
                        )
                        # 发送成功后立即跳出内层循环，重新获取页面上的所有 ID
                        break
                    else:
                        self.call_from_thread(
                            self._add_log, "error", 
                            f"发送失败 (publisher: {publisher_id})"
                        )
                
                # 如果当前页所有 ID 都已经点击过，进入下一页
                if not found_new and self.is_running:
                    self.call_from_thread(self._add_log, "info", "当前页所有 ID 都已处理，进入下一页")
                    self._rpa.click_next_page()
            
            if self.is_running:
                self.call_from_thread(
                    self._add_log, "success", 
                    f"任务执行完成！共发送 {sent_count} 条邀请"
                )
                self.call_from_thread(self._stop_execution)
                
        except Exception as e:
            logger.error(f"RPA 执行出错: {e}")
            self.call_from_thread(self._add_log, "error", f"执行出错: {e}")
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
