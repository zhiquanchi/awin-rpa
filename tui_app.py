"""
AWIN RPA - 模板管理和预览 TUI 界面
基于 Textual 框架
"""

from datetime import datetime
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


class ConfirmQuitScreen(ModalScreen[bool]):
    """确认退出弹窗"""

    CSS = """
    ConfirmQuitScreen {
        align: center middle;
    }
    
    #confirm-dialog {
        width: 50;
        height: 11;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }
    
    #confirm-title {
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
    }
    
    #confirm-message {
        text-align: center;
        margin-bottom: 1;
    }
    
    #confirm-buttons {
        align: center middle;
        height: auto;
    }
    
    #confirm-buttons Button {
        margin: 0 2;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-dialog"):
            yield Static("确认退出", id="confirm-title")
            yield Static("确定要退出应用吗？", id="confirm-message")
            with Horizontal(id="confirm-buttons"):
                yield Button("确定", id="btn-confirm-yes", variant="error")
                yield Button("取消", id="btn-confirm-no", variant="default")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-confirm-yes":
            self.dismiss(True)
        else:
            self.dismiss(False)


class Template:
    """模板数据类"""

    def __init__(self, id: int, name: str, content: str):
        self.id = id
        self.name = name
        self.content = content


class TemplateListItem(ListItem):
    """模板列表项"""

    def __init__(self, template: Template) -> None:
        super().__init__()
        self.template = template

    def compose(self) -> ComposeResult:
        yield Label(self.template.name)


class TemplateManager(App):
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
    send_count: reactive[int] = reactive(10)
    is_running: reactive[bool] = reactive(False)

    def __init__(self):
        super().__init__()
        # 初始化模板数据
        self.templates: list[Template] = [
            Template(
                1,
                "邀请模板1",
                "尊敬的合作伙伴：\n\n您好！我们诚挚地邀请您加入我们的合作计划。\n\n期待您的回复！\n\n此致\n敬礼",
            ),
            Template(
                2,
                "邀请模板2",
                "Hi，\n\n我们注意到您在该领域有丰富的经验，希望能与您建立合作关系。\n\n如有兴趣，请回复此消息。\n\n谢谢！",
            ),
            Template(
                3,
                "跟进模板",
                "您好！\n\n之前发送的合作邀请不知您是否有时间查看？\n\n如有任何问题，欢迎随时沟通。\n\n期待您的回复！",
            ),
        ]
        self.editing_content: str = ""
        self.editing_count: int = 10
        self.execution_task = None

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
                        *[TemplateListItem(t) for t in self.templates],
                        id="template-list",
                    )
                    with Horizontal(id="template-list-buttons"):
                        yield Button("增加", id="btn-add", variant="primary")
                        yield Button("删除", id="btn-delete", variant="error")

                # 中间：模板预览
                with Vertical(id="template-preview-wrapper"):
                    yield Static("模板内容预览", classes="section-label")
                    yield TextArea(
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

            # 第三行：执行区域（占 10%）
            with Horizontal(id="execution-section"):
                # 左侧：开始执行按钮
                with Vertical(id="execute-button-wrapper"):
                    yield Button(
                        "开始执行", id="btn-execute", variant="success", classes="btn-large"
                    )

                # 中间：日志区域
                with Vertical(id="log-wrapper"):
                    yield Static("执行日志", id="log-header")
                    yield RichLog(id="log-content", highlight=True, markup=True)

                # 右侧：退出按钮（占 10%）
                with Vertical(id="quit-button-wrapper"):
                    yield Button(
                        "退出", id="btn-quit", variant="error", classes="btn-quit"
                    )
                    yield Static("Ctrl+Q", id="quit-hint")

    def on_mount(self) -> None:
        """挂载时初始化"""
        # 默认选中第一个模板
        if self.templates:
            list_view = self.query_one("#template-list", ListView)
            list_view.index = 0

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """处理列表选择事件"""
        if isinstance(event.item, TemplateListItem):
            self.selected_template = event.item.template
            self._update_preview()

    def _update_preview(self) -> None:
        """更新预览内容"""
        preview = self.query_one("#template-preview", TextArea)
        if self.selected_template:
            preview.load_text(self.selected_template.content)
        else:
            preview.load_text("")

    def _refresh_template_list(self) -> None:
        """刷新模板列表"""
        list_view = self.query_one("#template-list", ListView)
        list_view.clear()
        for template in self.templates:
            list_view.append(TemplateListItem(template))

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

    # ===== 模板操作 =====

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """处理按钮点击事件"""
        button_id = event.button.id

        if button_id == "btn-add":
            self.action_add_template()
        elif button_id == "btn-delete":
            self.action_delete_template()
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
        elif button_id == "btn-execute":
            self.action_start_execution()
        elif button_id == "btn-quit":
            self.action_request_quit()

    def action_request_quit(self) -> None:
        """请求退出（显示确认弹窗）"""
        self.push_screen(ConfirmQuitScreen(), self._handle_quit_confirm)

    def _handle_quit_confirm(self, confirmed: bool) -> None:
        """处理退出确认结果"""
        if confirmed:
            self.exit()

    def action_add_template(self) -> None:
        """增加模板"""
        new_id = max((t.id for t in self.templates), default=0) + 1
        new_template = Template(new_id, f"新模板{new_id}", "请输入模板内容...")
        self.templates.append(new_template)
        self._refresh_template_list()
        self.selected_template = new_template
        self._update_preview()
        self.notify("模板已添加")

    def action_delete_template(self) -> None:
        """删除模板"""
        if not self.selected_template:
            self.notify("请先选择要删除的模板", severity="warning")
            return

        template_name = self.selected_template.name
        self.templates = [t for t in self.templates if t.id != self.selected_template.id]
        self._refresh_template_list()

        if self.templates:
            self.selected_template = self.templates[0]
            list_view = self.query_one("#template-list", ListView)
            list_view.index = 0
        else:
            self.selected_template = None

        self._update_preview()
        self.notify(f"模板 '{template_name}' 已删除")

    def action_edit_template(self) -> None:
        """开始编辑模板"""
        if not self.selected_template:
            self.notify("请先选择要修改的模板", severity="warning")
            return

        self.is_template_editing = True
        self.editing_content = self.selected_template.content

        # 切换预览区域为可编辑
        preview = self.query_one("#template-preview", TextArea)
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

        preview = self.query_one("#template-preview", TextArea)
        self.selected_template.content = preview.text
        preview.read_only = True

        self.is_template_editing = False

        # 切换按钮显示
        self.query_one("#btn-edit-template").remove_class("hidden")
        self.query_one("#btn-save-template").add_class("hidden")
        self.query_one("#btn-cancel-template").add_class("hidden")

        self._add_log("success", "模板保存成功")

    def action_cancel_edit(self) -> None:
        """取消模板编辑"""
        if not self.is_template_editing:
            return

        preview = self.query_one("#template-preview", TextArea)
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

        self._add_log("success", f"发送数量已更新为 {self.send_count} 个")

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

    # ===== 执行操作 =====

    def action_start_execution(self) -> None:
        """开始/停止执行"""
        if self.is_running:
            self._stop_execution()
        else:
            self._start_execution()

    def _start_execution(self) -> None:
        """开始执行"""
        if not self.selected_template:
            self.notify("请先选择一个模板", severity="warning")
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

    @work(exclusive=True)
    async def _run_execution(self) -> None:
        """后台执行任务"""
        import asyncio

        for i in range(1, self.send_count + 1):
            if not self.is_running:
                break

            await asyncio.sleep(1)
            self._add_log("success", f"第 {i}/{self.send_count} 条消息发送成功")

        if self.is_running:
            self._add_log("info", "任务执行完成！")
            self._stop_execution()

    def _stop_execution(self) -> None:
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

        if was_running:
            self._add_log("error", "任务已手动停止")


def main():
    """入口函数"""
    app = TemplateManager()
    app.run()


if __name__ == "__main__":
    main()
