from DrissionPage import Chromium
from loguru import logger
import questionary
import inspect

# 兼容性猴子补丁：尝试为 questionary 的多行输入替换默认提示文本
# 不同版本的 questionary 实现细节不同：有的在类上提供 INSTRUCTIONS，有的没有。
# 优先尝试修补模块内任意具有 INSTRUCTIONS 属性的类；若找不到则回退为包装 questionary.text
try:
    patched = False
    for name, obj in inspect.getmembers(questionary.prompts.text):
        if inspect.isclass(obj) and hasattr(obj, "INSTRUCTIONS"):
            try:
                obj.INSTRUCTIONS = "模板输入完成后，先按 esc 再按 enter。保存当前模板。\n"
                patched = True
                break
            except Exception:
                # 某些实现可能不允许写入，忽略并继续
                continue

    if not patched:
        # 回退：包装 questionary.text 工厂函数，在调用时为多行输入尝试传递 instruction 参数（如果支持）
        _orig_text = questionary.text
        def _patched_text(*args, **kwargs):
            try:
                multiline = kwargs.get("multiline", False)
                if multiline:
                    # 仅当调用方没有指定 instruction 时才注入自定义提示
                    if "instruction" not in kwargs:
                        kwargs["instruction"] = "模板输入完成后，先按 esc 再按 enter。保存当前模板。\n"
                return _orig_text(*args, **kwargs)
            except TypeError:
                # 如果原函数不接受 instruction 参数，尝试移除并调用原函数
                #（这意味着无法通过该途径修改提示，保持原状）
                kwargs.pop("instruction", None)
                return _orig_text(*args, **kwargs)

        questionary.text = _patched_text
except Exception:
    # 任何意外不应阻止程序启动，记录并继续
    try:
        logger.warning("未能应用 questionary 多行提示的猴子补丁，继续以默认行为运行。")
    except Exception:
        pass
from rich.console import Console
from rich.panel import Panel
import json
from pathlib import Path
import pyperclip
from datetime import datetime, timezone
import re
from dulwich import porcelain
from dulwich.repo import Repo

console = Console()
logger.add("file.log")


AUDIT_LOG_PATH = Path(__file__).parent / "awin_audit.jsonl"
SEEN_IDS_PATH = Path(__file__).parent / "seen_publisher_ids.txt"
CLICKED_IDS_PATH = Path(__file__).parent / "clicked_publisher_ids.txt"
HTML_DUMP_DIR = Path(__file__).parent / "html_dumps"


def _audit_filter(record) -> bool:
    return bool(record["extra"].get("audit"))


def _load_id_set(path: Path) -> set[str]:
    try:
        if not path.exists():
            return set()
        ids: set[str] = set()
        for line in path.read_text(encoding="utf-8").splitlines():
            value = line.strip()
            if value:
                ids.add(value)
        return ids
    except Exception:
        return set()


def _append_new_ids(path: Path, ids: list[str]):
    if not ids:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for value in ids:
            if value:
                f.write(f"{value}\n")


# 结构化审计日志：只记录与「ID 获取/点击」相关的事件，便于后续分析重复/失效按钮问题
logger.add(
    AUDIT_LOG_PATH,
    serialize=True,
    filter=_audit_filter,
    level="INFO",
)


class VersionManager:
    """版本管理器 - 使用 Dulwich 进行 Git 操作"""
    
    def __init__(self, repo_path: Path = None):
        self.repo_path = repo_path or Path(__file__).parent
        self.pyproject_path = self.repo_path / "pyproject.toml"
    
    def get_current_version(self) -> str:
        """从 pyproject.toml 获取当前版本"""
        try:
            if self.pyproject_path.exists():
                content = self.pyproject_path.read_text(encoding="utf-8")
                match = re.search(r'version\s*=\s*"([^"]+)"', content)
                if match:
                    return match.group(1)
        except Exception as e:
            logger.error(f"读取版本失败: {e}")
        return "0.1.0"
    
    def update_version(self, new_version: str) -> bool:
        """
        更新 pyproject.toml 中的版本号
        返回 True 表示成功，False 表示失败
        """
        try:
            if not self.pyproject_path.exists():
                console.print("[red]❌ 找不到 pyproject.toml 文件[/red]")
                return False
            
            # 验证版本格式
            if not re.match(r'^\d+\.\d+\.\d+$', new_version):
                console.print("[red]❌ 版本号格式无效，应该是 x.y.z 格式[/red]")
                return False
            
            # 读取文件内容
            content = self.pyproject_path.read_text(encoding="utf-8")
            
            # 替换版本号
            new_content = re.sub(
                r'(version\s*=\s*)"([^"]+)"',
                f'\\1"{new_version}"',
                content
            )
            
            if new_content == content:
                console.print("[yellow]⚠️ 未找到版本号字段[/yellow]")
                return False
            
            # 写入文件
            self.pyproject_path.write_text(new_content, encoding="utf-8")
            console.print(f"[green]✅ 版本已更新为: {new_version}[/green]")
            return True
        except Exception as e:
            logger.error(f"更新版本失败: {e}")
            console.print(f"[red]❌ 更新版本失败: {e}[/red]")
            return False
    
    def commit_version_change(self, version: str) -> bool:
        """
        使用 Dulwich 提交版本更改
        返回 True 表示成功，False 表示失败
        """
        try:
            repo = Repo(str(self.repo_path))
        except Exception as e:
            logger.error(f"无法打开 Git 仓库: {e}")
            console.print(f"[red]❌ 目录不是一个 Git 仓库或无法访问[/red]")
            return False
        
        try:
            # 添加文件到暂存区
            porcelain.add(repo, [self.pyproject_path.name])
            
            # 获取 Git 配置的作者信息，如果没有则使用默认值
            try:
                config = repo.get_config()
                author_name = config.get((b"user",), b"name")
                author_email = config.get((b"user",), b"email")
                if author_name and author_email:
                    author = f"{author_name.decode('utf-8')} <{author_email.decode('utf-8')}>".encode("utf-8")
                else:
                    author = b"Awin RPA Bot <bot@awin-rpa.local>"
            except Exception:
                author = b"Awin RPA Bot <bot@awin-rpa.local>"
            
            # 提交更改
            commit_message = f"chore: bump version to {version}"
            porcelain.commit(
                repo,
                message=commit_message.encode("utf-8"),
                author=author,
                committer=author
            )
            
            console.print(f"[green]✅ 已提交版本更新: {commit_message}[/green]")
            return True
        except Exception as e:
            logger.error(f"提交版本更改失败: {e}")
            console.print(f"[red]❌ 提交失败: {e}[/red]")
            return False
    
    def get_git_status(self) -> dict:
        """获取 Git 仓库状态"""
        try:
            repo = Repo(str(self.repo_path))
            status = porcelain.status(repo)
            
            # 收集所有暂存的文件，包括添加、修改、删除等
            staged_files = []
            for change_type in ["add", "modify", "delete"]:
                if change_type in status.staged:
                    staged_files.extend([f.decode("utf-8") for f in status.staged[change_type]])
            
            return {
                "staged": staged_files,
                "unstaged": [f.decode("utf-8") for f in status.unstaged],
                "untracked": [f.decode("utf-8") for f in status.untracked],
            }
        except Exception as e:
            logger.error(f"获取 Git 状态失败: {e}")
            return {"staged": [], "unstaged": [], "untracked": []}


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
        
        clipboard_content = pyperclip.paste().strip()
        default_content = ""
        if clipboard_content:
            use_clipboard = questionary.confirm(
                f"检测到剪贴板内容，是否直接使用?\n[dim]{clipboard_content[:50]}...[/dim]",
                default=True
            ).ask()
            if use_clipboard:
                default_content = clipboard_content
        
        content = questionary.text(
            "请输入邀请信息内容 (支持多行):",
            default=default_content,
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
        
        clipboard_content = pyperclip.paste().strip()
        default_content = msg["content"]
        if clipboard_content and clipboard_content != msg["content"]:
            use_clipboard = questionary.confirm(
                f"检测到剪贴板内容，是否替换当前内容?\n[dim]{clipboard_content[:50]}...[/dim]",
                default=False
            ).ask()
            if use_clipboard:
                default_content = clipboard_content
        
        new_content = questionary.text(
            "请输入新内容 (留空保持不变):",
            default=default_content,
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
        self.tab = self._select_awin_tab()
        self.message_manager = MessageManager()
        self._fetch_seq = 0
        self._click_seq = 0
        self._seen_publisher_ids: set[str] = _load_id_set(SEEN_IDS_PATH)
        self._clicked_publisher_ids: set[str] = _load_id_set(CLICKED_IDS_PATH)
    
    def _page_context(self) -> dict:
        try:
            url = getattr(self.tab, "url", None)
        except Exception:
            url = None
        return {"url": url}
    
    def _audit(self, event: str, **extra):
        logger.bind(
            audit=True,
            event=event,
            ts=datetime.now(timezone.utc).isoformat(),
            **self._page_context(),
            **extra,
        ).info(event)

    def _notify(self, title: str, message: str):
        try:
            from plyer import notification
            notification.notify(title=title, message=message, app_name="Awin RPA", timeout=5)
        except Exception:
            try:
                logger.info(f"[通知]{title}: {message}")
            except Exception:
                pass

    def _safe_get_html(self) -> str:
        try:
            html = getattr(self.tab, "html", None)
            if isinstance(html, str) and html:
                return html
        except Exception:
            pass

        try:
            run_js = getattr(self.tab, "run_js", None)
            if callable(run_js):
                html = run_js("return document.documentElement.outerHTML")
                if isinstance(html, str) and html:
                    return html
        except Exception:
            pass

        return ""

    def _dump_html(self, publisher_id: str, phase: str) -> str | None:
        try:
            html = self._safe_get_html()
            if not html:
                return None
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
            safe_pid = "".join(ch for ch in str(publisher_id) if ch.isalnum() or ch in ("-", "_"))[:64] or "unknown"
            HTML_DUMP_DIR.mkdir(parents=True, exist_ok=True)
            path = HTML_DUMP_DIR / f"{ts}_clickseq{self._click_seq}_pid{safe_pid}_{phase}.html"
            path.write_text(html, encoding="utf-8", errors="ignore")
            return str(path)
        except Exception:
            return None

    def _save_snapshot(self, publisher_id: str, phase: str) -> str | None:
        """
        保存 HTML 快照用于对比
        返回 HTML 文件路径
        """
        return self._dump_html(publisher_id, phase)
    
    def _select_awin_tab(self):
        """
        选择 URL 中包含 'awin' 的标签页；若未找到则回退为最新标签页
        """
        try:
            tabs = getattr(self.browser, "tabs", None)
            if tabs:
                for t in tabs:
                    try:
                        url = getattr(t, "url", None)
                        if isinstance(url, str) and "awin" in url.lower():
                            return t
                    except Exception:
                        continue
        except Exception:
            pass
        try:
            get_tabs = getattr(self.browser, "get_tabs", None)
            if callable(get_tabs):
                for t in get_tabs():
                    try:
                        url = getattr(t, "url", None)
                        if isinstance(url, str) and "awin" in url.lower():
                            return t
                    except Exception:
                        continue
        except Exception:
            pass
        return getattr(self.browser, "latest_tab", None)
    
    def refresh_tab(self):
        """重新获取当前浏览器标签页（不刷新页面）"""
        self.tab = self._select_awin_tab()
    
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
        publisher_ids_raw = [link.attr('data-publisherid') for link in invite_links]
        publisher_ids = [pid for pid in publisher_ids_raw if pid]
        publisher_ids = list(dict.fromkeys(publisher_ids))  # 去重且保留顺序

        self._fetch_seq += 1
        new_ids = [pid for pid in publisher_ids if pid not in self._seen_publisher_ids]
        self._seen_publisher_ids.update(publisher_ids)
        _append_new_ids(SEEN_IDS_PATH, new_ids)

        self._audit(
            "publisher_ids_fetched",
            fetch_seq=self._fetch_seq,
            raw_count=len(publisher_ids_raw),
            unique_count=len(publisher_ids),
            publisher_ids=publisher_ids,
            new_publisher_ids=new_ids,
            new_count=len(new_ids),
            seen_total=len(self._seen_publisher_ids),
        )
        return publisher_ids
    
    def input_message(self, message: str):
        """在申请框里面填写申请信息"""
        self.tab.ele('#customMessage').input(message)
    
    def click_next_page(self):
        """点击下一页按钮"""
        before_url = self._page_context().get("url")
        self.tab.ele('#nextPage').click()
        self.tab.wait.doc_loaded()
        self.tab.wait(2, 4)
        after_url = self._page_context().get("url")
        self._audit("next_page_clicked", before_url=before_url, after_url=after_url)
    
    def send_invite_to_publisher(self, publisher_id: str, msg: str) -> bool:
        """
        向单个 publisher 发送邀请
        返回 True 表示成功，False 表示按钮不存在
        """
        self._click_seq += 1
        clicked_before = publisher_id in self._clicked_publisher_ids
        self._audit(
            "invite_click_attempt",
            click_seq=self._click_seq,
            publisher_id=publisher_id,
            clicked_before=clicked_before,
        )

        # 在点击前保存快照
        html_before = self._save_snapshot(publisher_id, "before_click")
        self._audit(
            "snapshot_before_click",
            click_seq=self._click_seq,
            publisher_id=publisher_id,
            html_path=html_before,
        )

        # 查找对应的邀请按钮
        invite_link = self.tab.ele(f'xpath=//a[@data-publisherid="{publisher_id}"]', timeout=2)
        if not invite_link:
            logger.warning(f"找不到 publisher ID: {publisher_id} 的邀请按钮，尝试重新获取页面元素")
            # 保存失败时的快照
            html_fail = self._save_snapshot(publisher_id, "button_not_found")
            self._audit(
                "invite_button_missing",
                click_seq=self._click_seq,
                publisher_id=publisher_id,
                after_refresh=False,
                html_path=html_fail,
            )
            self.refresh_tab()
            invite_link = self.tab.ele(f'xpath=//a[@data-publisherid="{publisher_id}"]', timeout=2)
            if not invite_link:
                logger.warning(f"重新获取后仍找不到 publisher ID: {publisher_id} 的邀请按钮，跳过")
                self._audit(
                    "invite_button_missing",
                    click_seq=self._click_seq,
                    publisher_id=publisher_id,
                    after_refresh=True,
                )
                self._notify("邀请失败", f"未找到按钮：publisher {publisher_id}")
                return False
        
        logger.info(f"向 publisher ID: {publisher_id} 发送 invitation")

        try:
            invite_link.click()
        except Exception as e:
            # 保存点击失败时的快照
            html_fail = self._save_snapshot(publisher_id, "click_failed")
            self._audit(
                "invite_click_failed",
                click_seq=self._click_seq,
                publisher_id=publisher_id,
                stage="click_invite_link",
                error=str(e),
                attrs={
                    "class": invite_link.attr("class"),
                    "aria-disabled": invite_link.attr("aria-disabled"),
                    "href": invite_link.attr("href"),
                },
                html_path=html_fail,
            )
            self._notify("邀请失败", f"点击失败：publisher {publisher_id}")
            return False

        # 输入邀请信息（等待弹窗/输入框真正出现，避免"按钮已失效但元素仍在"的情况）
        try:
            custom_message = self.tab.ele("#customMessage", timeout=8)
            if not custom_message:
                # 保存找不到输入框时的快照
                html_fail = self._save_snapshot(publisher_id, "no_input_box")
                self._audit(
                    "invite_click_failed",
                    click_seq=self._click_seq,
                    publisher_id=publisher_id,
                    stage="wait_custom_message",
                    error="customMessage_not_found",
                    html_path=html_fail,
                )
                self._notify("邀请失败", f"未找到输入框：publisher {publisher_id}")
                return False
            custom_message.input(msg)
        except Exception as e:
            self._audit(
                "invite_click_failed",
                click_seq=self._click_seq,
                publisher_id=publisher_id,
                stage="input_message",
                error=str(e),
            )
            self._notify("邀请失败", f"填写信息失败：publisher {publisher_id}")
            return False

        # 等待 send invite 按钮可点击，然后点击
        try:
            send_btn = self.tab.ele("css:button.btn-small-green.modal_save", timeout=8)
            if not send_btn:
                self._audit(
                    "invite_click_failed",
                    click_seq=self._click_seq,
                    publisher_id=publisher_id,
                    stage="wait_send_button",
                    error="send_button_not_found",
                )
                self._notify("邀请失败", f"未找到发送按钮：publisher {publisher_id}")
                return False
            send_btn.wait.clickable(timeout=10)
            send_btn.click()
        except Exception as e:
            self._audit(
                "invite_click_failed",
                click_seq=self._click_seq,
                publisher_id=publisher_id,
                stage="click_send_button",
                error=str(e),
            )
            self._notify("邀请失败", f"点击发送失败：publisher {publisher_id}")
            return False

        # 等待弹窗出现并关闭
        try:
            popup_ok_btn = self.tab.ele("#popup_ok", timeout=12)
            if not popup_ok_btn:
                self._audit(
                    "invite_click_failed",
                    click_seq=self._click_seq,
                    publisher_id=publisher_id,
                    stage="wait_popup_ok",
                    error="popup_ok_not_found",
                )
                self._notify("邀请失败", f"未出现确认弹窗：publisher {publisher_id}")
                return False
            popup_ok_btn.wait.displayed(timeout=10, raise_err=True)
            popup_ok_btn.click()
        except Exception as e:
            self._audit(
                "invite_click_failed",
                click_seq=self._click_seq,
                publisher_id=publisher_id,
                stage="close_popup_ok",
                error=str(e),
            )
            self._notify("邀请失败", f"关闭弹窗失败：publisher {publisher_id}")
            return False

        # 保存成功发送后的快照
        html_after = self._save_snapshot(publisher_id, "after_click")
        self._audit(
            "invite_sent_success",
            click_seq=self._click_seq,
            publisher_id=publisher_id,
            html_path=html_after,
        )
        self._notify("邀请成功", f"已发送给 publisher {publisher_id}")
        if publisher_id not in self._clicked_publisher_ids:
            self._clicked_publisher_ids.add(publisher_id)
            _append_new_ids(CLICKED_IDS_PATH, [publisher_id])
        self.tab.wait(2, 3)
        return True
    
    def reset_clicked_ids(self):
        """重置已点击记录，清空内存集合和持久化文件"""
        count = len(self._clicked_publisher_ids)
        self._clicked_publisher_ids.clear()
        try:
            if CLICKED_IDS_PATH.exists():
                CLICKED_IDS_PATH.write_text("", encoding="utf-8")
        except Exception as e:
            logger.error(f"清空已点击记录文件失败: {e}")
        logger.info(f"已重置已点击记录，共清除 {count} 条")
        return count

    def run(self, invite_count: int, msg: str):
        """
        执行 RPA 主流程
        invite_count: 需要发送的邀请数量
        msg: 申请信息内容
        """
        sent_count = 0  # 已发送的邀请数量

        while sent_count < invite_count:
            # 每次循环都重新从网页获取所有 publisher IDs
            publisher_ids = self.get_publisher_ids()

            if not publisher_ids:
                logger.info("当前页面没有可邀请的 publisher，尝试下一页")
                self.click_next_page()
                continue

            logger.info(f"当前页面找到 {len(publisher_ids)} 个可邀请的 publisher")
            console.print(f"\n[bold blue]📧 已发送 {sent_count}/{invite_count} 条邀请[/bold blue]")

            # 遍历所有 ID，找到第一个未发送过的并发送（发送成功后会重新获取 ID）
            found_new = False
            for publisher_id in publisher_ids:
                if sent_count >= invite_count:
                    break

                # 如果该 ID 已经点击过，跳过
                if publisher_id in self._clicked_publisher_ids:
                    logger.debug(f"publisher ID: {publisher_id} 已经点击过，跳过")
                    continue

                # 找到一个未发送的 ID，发送邀约
                success = self.send_invite_to_publisher(publisher_id, msg)
                if success:
                    found_new = True
                    sent_count += 1
                    console.print(f"[green]✅ 已发送 {sent_count}/{invite_count}[/green]")
                    # 发送成功后立即跳出内层循环，重新获取页面上的所有 ID
                    break

            # 如果当前页所有 ID 都已经点击过，进入下一页
            if not found_new:
                logger.info("当前页所有 ID 都已经点击过，进入下一页")
                self.click_next_page()

        console.print(f"\n[bold green]✅ 已成功发送 {sent_count} 条邀请[/bold green]")
        self._notify("任务完成", f"已成功发送 {sent_count} 条邀请")


class AppUI:
    """应用程序 UI 交互"""
    
    def __init__(self, rpa: AwinRPA):
        self.rpa = rpa
        self.message_manager = rpa.message_manager
        self.version_manager = VersionManager()
    
    def reset_clicked_mode(self):
        """重置已点击记录"""
        count = len(self.rpa._clicked_publisher_ids)
        console.print(Panel.fit(
            "[bold red]🔄 重置已点击记录[/bold red]",
            border_style="red"
        ))
        console.print(f"\n[bold]当前已点击记录:[/bold] [yellow]{count}[/yellow] 条\n")

        if count == 0:
            console.print("[dim]当前没有已点击记录，无需重置。[/dim]\n")
            return

        confirm = questionary.confirm(
            f"确认清空全部 {count} 条已点击记录？清空后可重新对这些 publisher 发送邀请。",
            default=False
        ).ask()

        if not confirm:
            console.print("[yellow]已取消重置[/yellow]\n")
            return

        cleared = self.rpa.reset_clicked_ids()
        console.print(f"[green]✅ 已成功清除 {cleared} 条已点击记录[/green]\n")

    def version_mode(self):
        """版本管理模式"""
        console.print(Panel.fit(
            "[bold cyan]📦 版本管理[/bold cyan]",
            border_style="cyan"
        ))
        
        current_version = self.version_manager.get_current_version()
        console.print(f"\n[bold]当前版本:[/bold] [green]{current_version}[/green]\n")
        
        # 显示 Git 状态
        status = self.version_manager.get_git_status()
        if status["unstaged"] or status["untracked"]:
            console.print("[yellow]⚠️ 有未提交的更改:[/yellow]")
            for f in status["unstaged"]:
                console.print(f"  [dim]修改: {f}[/dim]")
            for f in status["untracked"]:
                console.print(f"  [dim]未跟踪: {f}[/dim]")
            console.print()
        
        action = questionary.select(
            "请选择操作:",
            choices=[
                "📝 更新版本号",
                "📊 查看 Git 状态",
                "🔙 返回主菜单"
            ]
        ).ask()
        
        if action is None or "返回" in action:
            return
        elif "更新版本" in action:
            self._update_version_flow(current_version)
        elif "查看" in action:
            self._display_git_status()
    
    def _update_version_flow(self, current_version: str):
        """版本更新流程"""
        console.print(f"\n[bold]当前版本:[/bold] {current_version}")
        
        # 解析当前版本
        parts = current_version.split(".")
        if len(parts) != 3:
            console.print("[red]❌ 当前版本格式无效[/red]")
            return
        
        try:
            major, minor, patch = map(int, parts)
        except ValueError:
            console.print("[red]❌ 当前版本包含非数字字符，无法解析[/red]")
            return
        
        # 提供版本更新选项
        choices = [
            f"主版本 (Major): {major + 1}.0.0",
            f"次版本 (Minor): {major}.{minor + 1}.0",
            f"补丁版本 (Patch): {major}.{minor}.{patch + 1}",
            "自定义版本号",
            "取消"
        ]
        
        selection = questionary.select(
            "请选择版本更新类型:",
            choices=choices
        ).ask()
        
        if selection is None or "取消" in selection:
            return
        
        if "自定义" in selection:
            new_version = questionary.text(
                "请输入新版本号 (格式: x.y.z):",
                validate=lambda x: True if re.match(r'^\d+\.\d+\.\d+$', x) else "版本号格式应为 x.y.z"
            ).ask()
            if not new_version:
                return
        else:
            # 从选项中提取版本号
            new_version = selection.split(": ")[1]
        
        # 确认更新
        confirm = questionary.confirm(
            f"确认将版本从 {current_version} 更新到 {new_version}?",
            default=True
        ).ask()
        
        if not confirm:
            console.print("[yellow]已取消[/yellow]")
            return
        
        # 更新版本
        if self.version_manager.update_version(new_version):
            # 询问是否提交
            commit = questionary.confirm(
                "是否使用 Git 提交此更改?",
                default=True
            ).ask()
            
            if commit:
                self.version_manager.commit_version_change(new_version)
    
    def _display_git_status(self):
        """显示 Git 状态"""
        status = self.version_manager.get_git_status()
        
        console.print("\n[bold cyan]📊 Git 仓库状态:[/bold cyan]\n")
        
        if status["staged"]:
            console.print("[green]已暂存的更改:[/green]")
            for f in status["staged"]:
                console.print(f"  [green]✓[/green] {f}")
            console.print()
        
        if status["unstaged"]:
            console.print("[yellow]未暂存的更改:[/yellow]")
            for f in status["unstaged"]:
                console.print(f"  [yellow]M[/yellow] {f}")
            console.print()
        
        if status["untracked"]:
            console.print("[dim]未跟踪的文件:[/dim]")
            for f in status["untracked"]:
                console.print(f"  [dim]?[/dim] {f}")
            console.print()
        
        if not any([status["staged"], status["unstaged"], status["untracked"]]):
            console.print("[green]✅ 工作目录干净[/green]\n")
    
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
                "🔄 重置已点击记录",
                "📦 版本管理",
                "❌ 退出"
            ]
        ).ask()
        
        if action is None or "退出" in action:
            console.print("[yellow]已退出[/yellow]")
            exit(0)
        
        if "设置" in action:
            self.settings_mode()
            return self.get_user_input()
        
        if "重置" in action:
            self.reset_clicked_mode()
            return self.get_user_input()
        
        if "版本" in action:
            self.version_mode()
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
        try:
            self.rpa.run(invite_count=invite_count, msg=msg)
        except Exception as e:
            try:
                self.rpa._notify("任务失败", f"执行异常：{e}")
            except Exception:
                pass
            raise
        console.print("\n[bold green]✅ 执行完成![/bold green]")


if __name__ == "__main__":
    rpa = AwinRPA()
    app = AppUI(rpa)
    app.start()

