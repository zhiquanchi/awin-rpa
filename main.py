from DrissionPage import Chromium
from loguru import logger
import questionary
from rich.console import Console
from rich.panel import Panel
import json
from pathlib import Path

console = Console()
logger.add("file.log")


class MessageManager:
    """邀请信息管理器"""
    
    def __init__(self, file_path: Path = None):
        self.file_path = file_path or Path(__file__).parent / "invitation_messages.json"
    
    def load(self) -> list[dict]:
        """从文件加载所有邀请信息"""
        if self.file_path.exists():
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    messages = json.load(f)
                    if messages:
                        return messages
            except (json.JSONDecodeError, IOError):
                pass
        console.print("[yellow]⚠️ 未找到邀请信息配置文件，请先在设置模式中添加邀请信息[/yellow]")
        return []
    
    def save(self, messages: list[dict]):
        """保存邀请信息到文件"""
        with open(self.file_path, "w", encoding="utf-8") as f:
            json.dump(messages, f, ensure_ascii=False, indent=2)
    
    def display(self, messages: list[dict]):
        """显示所有邀请信息"""
        for idx, msg in enumerate(messages, 1):
            console.print(Panel(
                msg["content"],
                title=f"[bold cyan]#{idx} {msg['name']}[/bold cyan]",
                border_style="cyan",
                expand=False
            ))
            console.print()
    
    def add(self, messages: list[dict]) -> list[dict]:
        """新增邀请信息"""
        console.print("\n[bold cyan]➕ 新增邀请信息[/bold cyan]")
        
        name = questionary.text("请输入邀请信息名称:").ask()
        if not name:
            console.print("[yellow]已取消[/yellow]")
            return messages
        
        content = questionary.text(
            "请输入邀请信息内容 (支持多行):",
            multiline=True
        ).ask()
        if not content:
            console.print("[yellow]已取消[/yellow]")
            return messages
        
        messages.append({"name": name, "content": content})
        self.save(messages)
        console.print(f"[green]✅ 已添加邀请信息: {name}[/green]")
        return messages
    
    def edit(self, messages: list[dict]) -> list[dict]:
        """编辑邀请信息"""
        if not messages:
            console.print("[yellow]没有可编辑的邀请信息[/yellow]")
            return messages
        
        self.display(messages)
        
        choices = [f"{i+1}. {msg['name']}" for i, msg in enumerate(messages)]
        choices.append("取消")
        
        selection = questionary.select(
            "选择要编辑的邀请信息:",
            choices=choices
        ).ask()
        
        if selection == "取消" or selection is None:
            return messages
        
        idx = int(selection.split(".")[0]) - 1
        msg = messages[idx]
        
        console.print(f"\n[bold]当前内容:[/bold]\n[dim]{msg['content']}[/dim]\n")
        
        new_name = questionary.text(
            "请输入新名称 (留空保持不变):",
            default=msg["name"]
        ).ask()
        
        new_content = questionary.text(
            "请输入新内容 (留空保持不变):",
            default=msg["content"],
            multiline=True
        ).ask()
        
        if new_name:
            messages[idx]["name"] = new_name
        if new_content:
            messages[idx]["content"] = new_content
        
        self.save(messages)
        console.print(f"[green]✅ 已更新邀请信息: {messages[idx]['name']}[/green]")
        return messages
    
    def delete(self, messages: list[dict]) -> list[dict]:
        """删除邀请信息"""
        if len(messages) <= 0:
            console.print("[yellow]没有可删除的邀请信息[/yellow]")
            return messages
        
        self.display(messages)
        
        choices = [f"{i+1}. {msg['name']}" for i, msg in enumerate(messages)]
        choices.append("取消")
        
        selection = questionary.select(
            "选择要删除的邀请信息:",
            choices=choices
        ).ask()
        
        if selection == "取消" or selection is None:
            return messages
        
        idx = int(selection.split(".")[0]) - 1
        deleted_name = messages[idx]["name"]
        
        confirm = questionary.confirm(
            f"确定要删除 '{deleted_name}' 吗?",
            default=False
        ).ask()
        
        if confirm:
            messages.pop(idx)
            self.save(messages)
            console.print(f"[green]✅ 已删除邀请信息: {deleted_name}[/green]")
        
        return messages


class AwinRPA:
    """Awin RPA 自动化工具"""
    
    # 默认目标页面 URL
    DEFAULT_URL = 'https://ui.awin.com/awin/merchant/45307/affiliate-directory/index/tab/notInvited'
    
    def __init__(self):
        self.browser = Chromium()
        self.tab = self.browser.latest_tab
        self.message_manager = MessageManager()
    
    def refresh_tab(self):
        """重新获取当前浏览器标签页（不刷新页面）"""
        self.tab = self.browser.latest_tab
    
    def goto_page(self, url: str = None):
        """跳转到邀请页面"""
        target_url = url or self.DEFAULT_URL
        self.tab.get(target_url)
    
    def select_sector(self, *sectors: str):
        """选择筛选项目"""
        for sector in sectors:
            self.tab.ele(f'text={sector}').click()
    
    def get_publisher_ids(self) -> list[str]:
        """获取所有 publisher ID"""
        table = self.tab.ele('xpath=//*[@id="directoryResults"]/table')
        invite_links = table.eles('xpath:.//a[@data-publisherid]')
        publisher_ids = [link.attr('data-publisherid') for link in invite_links]
        return publisher_ids
    
    def input_message(self, message: str):
        """在申请框里面填写申请信息"""
        self.tab.ele('#customMessage').input(message)
    
    def click_next_page(self):
        """点击下一页按钮"""
        self.tab.ele('#nextPage').click()
        self.tab.wait.doc_loaded()
        self.tab.wait(2, 4)
    
    def send_invite_to_publisher(self, publisher_id: str, msg: str) -> bool:
        """
        向单个 publisher 发送邀请
        返回 True 表示成功，False 表示按钮不存在
        """
        # 查找对应的邀请按钮
        invite_link = self.tab.ele(f'xpath=//a[@data-publisherid="{publisher_id}"]', timeout=2)
        if not invite_link:
            logger.warning(f"找不到 publisher ID: {publisher_id} 的邀请按钮，尝试重新获取页面元素")
            self.refresh_tab()
            invite_link = self.tab.ele(f'xpath=//a[@data-publisherid="{publisher_id}"]', timeout=2)
            if not invite_link:
                logger.warning(f"重新获取后仍找不到 publisher ID: {publisher_id} 的邀请按钮，跳过")
                return False
        
        logger.info(f"向 publisher ID: {publisher_id} 发送 invitation")
        invite_link.click()
        
        # 输入邀请信息
        self.input_message(message=msg)
        
        # 等待 send invite 按钮可点击，然后点击
        send_btn = self.tab.ele('css:button.btn-small-green.modal_save')
        send_btn.wait.clickable(timeout=10)
        send_btn.click()
        
        # 等待弹窗出现
        popup_ok_btn = self.tab.ele('#popup_ok')
        popup_ok_btn.wait.displayed(timeout=10, raise_err=True)
        
        # 点击ok按钮关闭弹窗
        popup_ok_btn.click()
        
        self.tab.wait(2, 3)
        return True
    
    def run(self, invite_count: int, msg: str):
        """
        执行 RPA 主流程
        invite_count: 需要发送的邀请数量
        msg: 申请信息内容
        """
        sent_count = 0  # 已发送的邀请数量
        
        while sent_count < invite_count:
            publisher_ids = self.get_publisher_ids()
            
            if not publisher_ids:
                logger.info("当前页面没有可邀请的 publisher，尝试下一页")
                self.click_next_page()
                continue
            
            logger.info(f"当前页面找到 {len(publisher_ids)} 个可邀请的 publisher")
            console.print(f"\n[bold blue]📧 已发送 {sent_count}/{invite_count} 条邀请[/bold blue]")
            
            # 逐个处理
            processed_any = False
            for publisher_id in publisher_ids:
                if sent_count >= invite_count:
                    break
                    
                success = self.send_invite_to_publisher(publisher_id, msg)
                if success:
                    sent_count += 1
                    processed_any = True
                    console.print(f"[green]✅ 已发送 {sent_count}/{invite_count}[/green]")
            
            # 如果这一轮没有成功处理任何一个，说明列表已经空了或都失效了，进入下一页
            if not processed_any:
                logger.info("当前页面所有按钮都已失效，进入下一页")
                self.click_next_page()
        
        console.print(f"\n[bold green]✅ 已成功发送 {sent_count} 条邀请[/bold green]")


class AppUI:
    """应用程序 UI 交互"""
    
    def __init__(self, rpa: AwinRPA):
        self.rpa = rpa
        self.message_manager = rpa.message_manager
    
    def settings_mode(self):
        """设置模式 - 管理邀请信息"""
        console.print(Panel.fit(
            "[bold yellow]⚙️ 设置模式 - 管理邀请信息[/bold yellow]",
            border_style="yellow"
        ))
        
        messages = self.message_manager.load()
        
        while True:
            self.message_manager.display(messages)
            
            action = questionary.select(
                "请选择操作:",
                choices=[
                    "➕ 新增邀请信息",
                    "✏️ 编辑邀请信息",
                    "🗑️ 删除邀请信息",
                    "🔙 返回主菜单"
                ]
            ).ask()
            
            if action is None or "返回" in action:
                break
            elif "新增" in action:
                messages = self.message_manager.add(messages)
            elif "编辑" in action:
                messages = self.message_manager.edit(messages)
            elif "删除" in action:
                messages = self.message_manager.delete(messages)
    
    def select_message(self) -> str:
        """选择或修改邀请信息"""
        messages = self.message_manager.load()
        
        if not messages:
            console.print("[red]❌ 没有可用的邀请信息，请先在设置模式中添加[/red]")
            self.settings_mode()
            messages = self.message_manager.load()
            if not messages:
                console.print("[red]❌ 仍然没有邀请信息，无法继续[/red]")
                exit(1)
        
        console.print("\n[bold]📧 当前可用的邀请信息:[/bold]")
        self.message_manager.display(messages)
        
        choices = [f"{i+1}. {msg['name']}" for i, msg in enumerate(messages)]
        
        selection = questionary.select(
            "请选择要使用的邀请信息:",
            choices=choices
        ).ask()
        
        if selection is None:
            console.print("[yellow]已取消操作[/yellow]")
            exit(0)
        
        idx = int(selection.split(".")[0]) - 1
        selected_msg = messages[idx]
        
        console.print(Panel(
            selected_msg["content"],
            title=f"[bold cyan]{selected_msg['name']}[/bold cyan]",
            border_style="cyan"
        ))
        
        modify = questionary.confirm(
            "是否需要修改这条邀请信息?",
            default=False
        ).ask()
        
        if modify:
            new_content = questionary.text(
                "请输入修改后的邀请信息 (支持多行):",
                default=selected_msg["content"],
                multiline=True
            ).ask()
            
            if new_content is None:
                console.print("[yellow]已取消修改[/yellow]")
                return selected_msg["content"]
            
            save_option = questionary.select(
                "是否保存这次修改?",
                choices=[
                    "仅本次使用 (不保存)",
                    "覆盖原有信息",
                    "保存为新的邀请信息"
                ]
            ).ask()
            
            if save_option == "覆盖原有信息":
                messages[idx]["content"] = new_content
                self.message_manager.save(messages)
                console.print(f"[green]✅ 已更新邀请信息: {selected_msg['name']}[/green]")
            elif save_option == "保存为新的邀请信息":
                new_name = questionary.text(
                    "请输入新邀请信息的名称:",
                    default=f"{selected_msg['name']} (修改版)"
                ).ask()
                if new_name:
                    messages.append({"name": new_name, "content": new_content})
                    self.message_manager.save(messages)
                    console.print(f"[green]✅ 已保存新邀请信息: {new_name}[/green]")
            
            return new_content
        
        return selected_msg["content"]
    
    def get_user_input(self) -> tuple[int, str]:
        """使用终端UI交互获取用户输入的参数"""
        console.print(Panel.fit(
            "[bold cyan]🤖 Awin RPA 自动化工具[/bold cyan]\n"
            "[dim]自动发送邀请给 Publisher[/dim]",
            border_style="cyan"
        ))
        
        action = questionary.select(
            "请选择操作:",
            choices=[
                "🚀 开始执行 RPA",
                "⚙️ 设置模式 (管理邀请信息)",
                "❌ 退出"
            ]
        ).ask()
        
        if action is None or "退出" in action:
            console.print("[yellow]已退出[/yellow]")
            exit(0)
        
        if "设置" in action:
            self.settings_mode()
            return self.get_user_input()
        
        invite_count = questionary.text(
            "请输入要发送的邀请数量:",
            default="10",
            validate=lambda x: x.isdigit() and int(x) > 0 or "请输入有效的正整数"
        ).ask()
        
        if invite_count is None:
            console.print("[yellow]已取消操作[/yellow]")
            exit(0)
        
        msg = self.select_message()
        
        console.print("\n[bold]📋 执行配置:[/bold]")
        console.print(f"  • 发送数量: [green]{invite_count}[/green]")
        console.print(f"  • 消息内容: [dim]{msg[:50]}...[/dim]" if len(msg) > 50 else f"  • 消息内容: [dim]{msg}[/dim]")
        
        confirm = questionary.confirm(
            "\n确认开始执行?",
            default=True
        ).ask()
        
        if not confirm:
            console.print("[yellow]已取消操作[/yellow]")
            exit(0)
        
        return int(invite_count), msg
    
    def start(self):
        """启动应用程序"""
        invite_count, msg = self.get_user_input()
        
        console.print("\n[bold green]🚀 开始执行 RPA...[/bold green]")
        self.rpa.run(invite_count=invite_count, msg=msg)
        console.print("\n[bold green]✅ 执行完成![/bold green]")


if __name__ == "__main__":
    rpa = AwinRPA()
    app = AppUI(rpa)
    app.start()


