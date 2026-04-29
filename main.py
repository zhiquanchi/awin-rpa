import inspect
import socket
import subprocess
import tempfile
import time

import questionary
from DrissionPage import Chromium, ChromiumOptions
from loguru import logger

# 兼容性猴子补丁：尝试为 questionary 的多行输入替换默认提示文本
# 不同版本的 questionary 实现细节不同：有的在类上提供 INSTRUCTIONS，有的没有。
# 优先尝试修补模块内任意具有 INSTRUCTIONS 属性的类；若找不到则回退为包装 questionary.text
try:
    patched = False
    for name, obj in inspect.getmembers(questionary.prompts.text):
        if inspect.isclass(obj) and hasattr(obj, "INSTRUCTIONS"):
            try:
                obj.INSTRUCTIONS = (
                    "模板输入完成后，先按 esc 再按 enter。保存当前模板。\n"
                )
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
                        kwargs["instruction"] = (
                            "模板输入完成后，先按 esc 再按 enter。保存当前模板。\n"
                        )
                return _orig_text(*args, **kwargs)
            except TypeError:
                # 如果原函数不接受 instruction 参数，尝试移除并调用原函数
                # （这意味着无法通过该途径修改提示，保持原状）
                kwargs.pop("instruction", None)
                return _orig_text(*args, **kwargs)

        questionary.text = _patched_text
except Exception:
    # 任何意外不应阻止程序启动，记录并继续
    try:
        logger.warning("未能应用 questionary 多行提示的猴子补丁，继续以默认行为运行。")
    except Exception:
        pass
import json
import os
import re
import shutil
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pyperclip
from dulwich import porcelain
from dulwich.repo import Repo
from rich.console import Console
from rich.panel import Panel

from config_manager import ConfigManager

console = Console()
logger.add("file.log")


AUDIT_LOG_PATH = Path(__file__).parent / "awin_audit.jsonl"
SEEN_IDS_PATH = Path(__file__).parent / "seen_publisher_ids.txt"
CLICKED_IDS_PATH = Path(__file__).parent / "clicked_publisher_ids.txt"
HTML_DUMP_DIR = Path(__file__).parent / "html_dumps"
FEISHU_WEBHOOK_PATH = Path(__file__).parent / "feishu_webhook.txt"
BROWSER_DEBUG_HOST = (
    os.getenv("AWIN_BROWSER_DEBUG_HOST", "127.0.0.1") or "127.0.0.1"
).strip()
BROWSER_DEBUG_PORT_RAW = (
    os.getenv("AWIN_BROWSER_DEBUG_PORT", "9222") or "9222"
).strip()
CHROME_PATH_RAW = (os.getenv("AWIN_CHROME_PATH", "") or "").strip()
CHROME_USER_DATA_DIR_RAW = (os.getenv("AWIN_CHROME_USER_DATA_DIR", "") or "").strip()


def _get_browser_debug_port() -> int:
    try:
        port = int(BROWSER_DEBUG_PORT_RAW)
    except ValueError as e:
        raise RuntimeError(
            f"环境变量 AWIN_BROWSER_DEBUG_PORT 的值无效: {BROWSER_DEBUG_PORT_RAW}"
        ) from e
    if not 1 <= port <= 65535:
        raise RuntimeError(
            f"环境变量 AWIN_BROWSER_DEBUG_PORT 超出有效范围: {BROWSER_DEBUG_PORT_RAW}"
        )
    return port


def _is_tcp_port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _is_local_browser_host(host: str) -> bool:
    normalized = (host or "").strip().lower()
    return normalized in {"127.0.0.1", "localhost", "::1"}


def _iter_chrome_candidates() -> list[Path]:
    candidates: list[Path] = []
    if CHROME_PATH_RAW:
        candidates.append(Path(CHROME_PATH_RAW).expanduser())

    which_chrome = shutil.which("chrome")
    if which_chrome:
        candidates.append(Path(which_chrome))

    env_candidates = [
        os.getenv("ProgramFiles", ""),
        os.getenv("ProgramFiles(x86)", ""),
        os.getenv("LOCALAPPDATA", ""),
    ]
    for base_dir in env_candidates:
        if base_dir:
            candidates.append(
                Path(base_dir) / "Google" / "Chrome" / "Application" / "chrome.exe"
            )

    deduped: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        resolved = str(path)
        if resolved in seen:
            continue
        seen.add(resolved)
        deduped.append(path)
    return deduped


def _get_chrome_path() -> Path:
    candidates = _iter_chrome_candidates()
    if CHROME_PATH_RAW and not candidates[0].exists():
        raise RuntimeError(f"AWIN_CHROME_PATH 指定的 Chrome 不存在: {candidates[0]}")

    for path in candidates:
        if path.exists():
            return path

    raise RuntimeError(
        "未找到 Chrome 可执行文件。请安装 Google Chrome，或设置 AWIN_CHROME_PATH。"
    )


def _get_chrome_user_data_dir(port: int) -> Path:
    if CHROME_USER_DATA_DIR_RAW:
        return Path(CHROME_USER_DATA_DIR_RAW).expanduser()
    base_dir = Path(os.getenv("LOCALAPPDATA", tempfile.gettempdir()))
    return base_dir / "awin-rpa" / f"chrome-debug-{port}"


def _ensure_local_debug_chrome(
    host: str, port: int, startup_timeout: float = 10.0
) -> None:
    address = f"{host}:{port}"
    if _is_tcp_port_open(host, port):
        return
    if not _is_local_browser_host(host):
        raise RuntimeError(
            f"未检测到 Chrome 调试端口 {address}。当前配置为远程地址，"
            "仅支持对本地浏览器自动拉起，请先在目标机器上启动 Chrome 调试端口。"
        )

    chrome_path = _get_chrome_path()
    user_data_dir = _get_chrome_user_data_dir(port)
    user_data_dir.mkdir(parents=True, exist_ok=True)

    logger.info(
        f"未检测到 Chrome 调试端口 {address}，尝试自动启动 Chrome: {chrome_path}"
    )
    try:
        subprocess.Popen(
            [
                str(chrome_path),
                f"--remote-debugging-port={port}",
                f"--user-data-dir={user_data_dir}",
                "--no-first-run",
                "--no-default-browser-check",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as e:
        raise RuntimeError(f"自动启动 Chrome 失败: {e}") from e

    deadline = time.monotonic() + startup_timeout
    while time.monotonic() < deadline:
        if _is_tcp_port_open(host, port, timeout=0.5):
            return
        time.sleep(0.25)

    raise RuntimeError(
        f"已尝试自动启动 Chrome，但调试端口 {address} 未在 {startup_timeout:g} 秒内就绪。"
        " 请手动确认 Chrome 是否成功启动，或设置 AWIN_CHROME_PATH。"
    )


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

    def __init__(self, repo_path: Path | None = None):
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
            if not re.match(r"^\d+\.\d+\.\d+$", new_version):
                console.print("[red]❌ 版本号格式无效，应该是 x.y.z 格式[/red]")
                return False

            # 读取文件内容
            content = self.pyproject_path.read_text(encoding="utf-8")

            # 替换版本号
            new_content = re.sub(
                r'(version\s*=\s*)"([^"]+)"', f'\\1"{new_version}"', content
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
                    author = f"{author_name.decode('utf-8')} <{author_email.decode('utf-8')}>".encode(
                        "utf-8"
                    )
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
                committer=author,
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
                    staged_files.extend(
                        [f.decode("utf-8") for f in status.staged[change_type]]
                    )

            return {
                "staged": staged_files,
                "unstaged": [f.decode("utf-8") for f in status.unstaged],
                "untracked": [f.decode("utf-8") for f in status.untracked],
            }
        except Exception as e:
            logger.error(f"获取 Git 状态失败: {e}")
            return {"staged": [], "unstaged": [], "untracked": []}


class Updater:
    """自动更新器 - 通过 GitHub Raw URL 检查版本并下载更新文件"""

    GITHUB_REPO = "zhiquanchi/awin-rpa"
    GITHUB_BRANCH = "master"
    RAW_BASE_URL = "https://raw.githubusercontent.com"
    UPDATE_FILES = [
        "main.py",
        "tui_app.py",
        "tui_app.tcss",
        "config_manager.py",
        "pyproject.toml",
    ]
    TIMEOUT = 15  # 网络请求超时（秒）

    def __init__(self, base_dir: Path | None = None):
        self.base_dir = base_dir or Path(__file__).parent
        self.version_manager = VersionManager(self.base_dir)

    def _raw_url(self, filename: str) -> str:
        """构造 GitHub Raw 文件 URL"""
        return f"{self.RAW_BASE_URL}/{self.GITHUB_REPO}/{self.GITHUB_BRANCH}/{filename}"

    @staticmethod
    def _parse_version(text: str) -> str | None:
        """从 pyproject.toml 内容中解析 version 字段"""
        match = re.search(r'version\s*=\s*"([^"]+)"', text)
        return match.group(1) if match else None

    @staticmethod
    def _version_tuple(ver: str) -> tuple[int, ...]:
        """将版本号字符串转为可比较的元组"""
        try:
            return tuple(int(x) for x in ver.split("."))
        except (ValueError, AttributeError):
            return (0, 0, 0)

    def check_for_updates(self) -> dict:
        """
        检查是否有新版本可用。
        返回 {"has_update": bool, "local_version": str, "remote_version": str, "error": str|None}
        """
        local_version = self.version_manager.get_current_version()
        try:
            url = self._raw_url("pyproject.toml")
            req = urllib.request.Request(url, headers={"User-Agent": "AwinRPA-Updater"})
            with urllib.request.urlopen(req, timeout=self.TIMEOUT) as resp:
                content = resp.read().decode("utf-8")
        except urllib.error.URLError as e:
            logger.error(f"检查更新失败（网络错误）: {e}")
            return {
                "has_update": False,
                "local_version": local_version,
                "remote_version": "",
                "error": f"无法连接 GitHub: {e}",
            }
        except Exception as e:
            logger.error(f"检查更新失败: {e}")
            return {
                "has_update": False,
                "local_version": local_version,
                "remote_version": "",
                "error": str(e),
            }

        remote_version = self._parse_version(content)
        if not remote_version:
            return {
                "has_update": False,
                "local_version": local_version,
                "remote_version": "",
                "error": "无法解析远程版本号",
            }

        has_update = self._version_tuple(remote_version) > self._version_tuple(
            local_version
        )
        return {
            "has_update": has_update,
            "local_version": local_version,
            "remote_version": remote_version,
            "error": None,
        }

    def download_updates(self, on_progress=None) -> dict:
        """
        下载并覆盖代码文件。
        on_progress: 可选回调函数 (filename: str, index: int, total: int) -> None
        返回 {"success": bool, "updated_files": list[str], "message": str}
        """
        backup_map: dict[str, Path] = {}  # 原文件 -> 备份路径
        updated: list[str] = []
        total = len(self.UPDATE_FILES)

        try:
            for idx, filename in enumerate(self.UPDATE_FILES):
                if on_progress:
                    on_progress(filename, idx + 1, total)

                target = self.base_dir / filename
                backup = self.base_dir / f"{filename}.bak"

                # 备份旧文件（如果存在）
                if target.exists():
                    shutil.copy2(target, backup)
                    backup_map[filename] = backup

                # 下载新文件
                url = self._raw_url(filename)
                req = urllib.request.Request(
                    url, headers={"User-Agent": "AwinRPA-Updater"}
                )
                with urllib.request.urlopen(req, timeout=self.TIMEOUT) as resp:
                    data = resp.read()

                target.write_bytes(data)
                updated.append(filename)
                logger.info(f"已更新文件: {filename}")

            # 全部成功，删除备份文件
            for bak_path in backup_map.values():
                try:
                    bak_path.unlink()
                except Exception:
                    pass

            new_version = self.version_manager.get_current_version()
            return {
                "success": True,
                "updated_files": updated,
                "message": f"更新成功，新版本: {new_version}，请重新启动程序以生效。",
            }

        except Exception as e:
            logger.error(f"下载更新失败: {e}")
            # 回滚：将备份文件恢复
            for filename, bak_path in backup_map.items():
                target = self.base_dir / filename
                try:
                    shutil.copy2(bak_path, target)
                    bak_path.unlink()
                except Exception:
                    pass
            return {
                "success": False,
                "updated_files": [],
                "message": f"更新失败，已回滚: {e}",
            }


class MessageManager:
    """邀请信息管理器"""

    def __init__(self, file_path: Path | None = None):
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
        console.print(
            "[yellow]⚠️ 未找到邀请信息配置文件，请先在设置模式中添加邀请信息[/yellow]"
        )
        return []

    def save(self, messages: list[dict]):
        """保存邀请信息到文件"""
        with open(self.file_path, "w", encoding="utf-8") as f:
            json.dump(messages, f, ensure_ascii=False, indent=2)

    def display(self, messages: list[dict]):
        """显示所有邀请信息"""
        for idx, msg in enumerate(messages, 1):
            console.print(
                Panel(
                    msg["content"],
                    title=f"[bold cyan]#{idx} {msg['name']}[/bold cyan]",
                    border_style="cyan",
                    expand=False,
                )
            )
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
                default=True,
            ).ask()
            if use_clipboard:
                default_content = clipboard_content

        content = questionary.text(
            "请输入邀请信息内容 (支持多行):", default=default_content, multiline=True
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

        choices = [f"{i + 1}. {msg['name']}" for i, msg in enumerate(messages)]
        choices.append("取消")

        selection = questionary.select("选择要编辑的邀请信息:", choices=choices).ask()

        if selection == "取消" or selection is None:
            return messages

        idx = int(selection.split(".")[0]) - 1
        msg = messages[idx]

        console.print(f"\n[bold]当前内容:[/bold]\n[dim]{msg['content']}[/dim]\n")

        new_name = questionary.text(
            "请输入新名称 (留空保持不变):", default=msg["name"]
        ).ask()

        clipboard_content = pyperclip.paste().strip()
        default_content = msg["content"]
        if clipboard_content and clipboard_content != msg["content"]:
            use_clipboard = questionary.confirm(
                f"检测到剪贴板内容，是否替换当前内容?\n[dim]{clipboard_content[:50]}...[/dim]",
                default=False,
            ).ask()
            if use_clipboard:
                default_content = clipboard_content

        new_content = questionary.text(
            "请输入新内容 (留空保持不变):", default=default_content, multiline=True
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

        choices = [f"{i + 1}. {msg['name']}" for i, msg in enumerate(messages)]
        choices.append("取消")

        selection = questionary.select("选择要删除的邀请信息:", choices=choices).ask()

        if selection == "取消" or selection is None:
            return messages

        idx = int(selection.split(".")[0]) - 1
        deleted_name = messages[idx]["name"]

        confirm = questionary.confirm(
            f"确定要删除 '{deleted_name}' 吗?", default=False
        ).ask()

        if confirm:
            messages.pop(idx)
            self.save(messages)
            console.print(f"[green]✅ 已删除邀请信息: {deleted_name}[/green]")

        return messages


class AwinRPA:
    """Awin RPA 自动化工具"""

    # 默认目标页面 URL
    DEFAULT_URL = "https://ui.awin.com/awin/merchant/45307/affiliate-directory/index/tab/notInvited"
    INVITE_ALREADY_EXISTS_TEXT = (
        "invitation already exists. please refresh and try again"
    )

    def __init__(
        self, notify_channel: str | None = None, feishu_webhook_url: str | None = None
    ):
        self.message_manager = MessageManager()
        self.config_manager = ConfigManager()
        self.browser_host = BROWSER_DEBUG_HOST
        self.browser_port = _get_browser_debug_port()
        self.browser = self._connect_browser()
        self.tab = self._select_awin_tab()
        self.current_template_name = ""
        self.notify_channel = self._normalize_notify_channel(
            notify_channel
            if notify_channel is not None
            else self.config_manager.notify_channel
        )
        self.feishu_webhook_url = (
            (feishu_webhook_url or "").strip()
            or self.config_manager.feishu_webhook_url
            or self._load_feishu_webhook_url()
        )
        self._fetch_seq = 0
        self._click_seq = 0
        self._seen_publisher_ids: set[str] = _load_id_set(SEEN_IDS_PATH)
        self._clicked_publisher_ids: set[str] = _load_id_set(CLICKED_IDS_PATH)

    def _connect_browser(self) -> Chromium:
        address = f"{self.browser_host}:{self.browser_port}"
        if not _is_tcp_port_open(self.browser_host, self.browser_port):
            _ensure_local_debug_chrome(self.browser_host, self.browser_port)

        options = ChromiumOptions()
        options.set_address(address)
        return Chromium(options)

    @staticmethod
    def _normalize_notify_channel(channel: str) -> str:
        allowed = {"desktop", "feishu", "both", "none"}
        normalized = (channel or "").strip().lower()
        if normalized in allowed:
            return normalized
        return "desktop"

    def _load_feishu_webhook_url(self) -> str | None:
        """
        优先从环境变量读取飞书机器人 webhook；
        若未设置，则尝试从 feishu_webhook.txt 读取。
        """
        env_value = os.getenv("FEISHU_WEBHOOK_URL", "").strip()
        if env_value:
            return env_value
        try:
            if FEISHU_WEBHOOK_PATH.exists():
                value = FEISHU_WEBHOOK_PATH.read_text(encoding="utf-8").strip()
                return value or None
        except Exception as e:
            logger.warning(f"读取飞书 webhook 配置失败: {e}")
        return None

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

            notification.notify(
                title=title, message=message, app_name="Awin RPA", timeout=5
            )
        except Exception:
            try:
                logger.info(f"[通知]{title}: {message}")
            except Exception:
                pass

    def _template_display_name(self) -> str:
        return self.current_template_name or "(未指定模板)"

    def _notify_feishu_invite_failure(self, publisher_id: str, reason: str):
        """单个 publisher 邀请失败时仅记录日志，不再发送实时通知。
        任务结束后由 run() 统一发送完成通知。
        """
        logger.info(f"邀请失败（已跳过）: publisher={publisher_id}，原因={reason}")

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
            safe_pid = (
                "".join(
                    ch for ch in str(publisher_id) if ch.isalnum() or ch in ("-", "_")
                )[:64]
                or "unknown"
            )
            HTML_DUMP_DIR.mkdir(parents=True, exist_ok=True)
            path = (
                HTML_DUMP_DIR
                / f"{ts}_clickseq{self._click_seq}_pid{safe_pid}_{phase}.html"
            )
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

    def goto_page(self, url: str | None = None):
        """跳转到邀请页面"""
        target_url = url or self.DEFAULT_URL
        self.tab.get(target_url)

    def select_sector(self, *sectors: str):
        """选择筛选项目"""
        for sector in sectors:
            self.tab.ele(f"text={sector}").click()

    def get_publisher_ids(self) -> list[str]:
        """获取所有 publisher ID"""
        table = self.tab.ele('xpath=//*[@id="directoryResults"]/table')
        invite_links = table.eles("xpath:.//a[@data-publisherid]")
        publisher_ids_raw = [link.attr("data-publisherid") for link in invite_links]
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
        self.tab.ele("#customMessage").input(message)

    def click_next_page(self):
        """点击下一页按钮"""
        before_url = self._page_context().get("url")
        self.tab.ele("#nextPage").click()
        self.tab.wait.doc_loaded()
        self.tab.wait(2, 4)
        after_url = self._page_context().get("url")
        self._audit("next_page_clicked", before_url=before_url, after_url=after_url)

    def _mark_publisher_processed(self, publisher_id: str) -> None:
        """将 publisher 标记为已处理，避免在后续循环中重复尝试。"""
        if publisher_id in self._clicked_publisher_ids:
            return
        self._clicked_publisher_ids.add(publisher_id)
        _append_new_ids(CLICKED_IDS_PATH, [publisher_id])

    def _get_invite_result_popup_text(self, timeout: float = 8.0) -> str:
        """
        等待并读取发送邀请后的结果弹窗文本，用于识别明确失败原因。

        轮询 #popup_message（结果弹窗专属容器）直到它真正可见且有内容，
        最多等待 timeout 秒后返回空字符串。
        - 只读 #popup_message，不回退到邀请表单的 modal 选择器。
        - 用 JS 检查 display/visibility/opacity，避免读到隐藏的残留文本。
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                text = self.tab.run_js(
                    """
                    const el = document.querySelector('#popup_message');
                    if (!el) return '';
                    const style = window.getComputedStyle(el);
                    if (style.display === 'none' || style.visibility === 'hidden') return '';
                    if (parseFloat(style.opacity) === 0) return '';
                    const rect = el.getBoundingClientRect();
                    if (rect.width === 0 || rect.height === 0) return '';
                    return (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 500);
                    """
                )
                if isinstance(text, str) and text:
                    return re.sub(r"\s+", " ", text).strip()
            except Exception:
                pass
            time.sleep(0.3)
        return ""

    def _get_invite_modal_state(self) -> dict[str, object]:
        """读取结果弹窗与邀请表单的可见状态。"""
        fallback = {
            "popup_border_exists": False,
            "popup_border_visible": False,
            "membership_modal_exists": False,
            "membership_modal_visible": False,
            "membership_modal_text": "",
            "popup_ok_exists": False,
            "popup_ok_visible": False,
            "popup_message_exists": False,
            "popup_message_visible": False,
            "popup_message_text": "",
        }
        try:
            state = self.tab.run_js(
                """
                const describe = (selector) => {
                    const el = document.querySelector(selector);
                    if (!el) {
                        return { exists: false, visible: false, text: '' };
                    }
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    const visible =
                        style.display !== 'none' &&
                        style.visibility !== 'hidden' &&
                        parseFloat(style.opacity || '1') !== 0 &&
                        rect.width > 0 &&
                        rect.height > 0;
                    return {
                        exists: true,
                        visible,
                        text: (el.innerText || el.textContent || '')
                            .replace(/\\s+/g, ' ')
                            .trim()
                            .slice(0, 500),
                    };
                };

                const popupBorder = describe('#popup_border');
                const membershipModal = describe('#membershipModal');
                const popupOk = describe('#popup_ok');
                const popupMessage = describe('#popup_message');

                return {
                    popup_border_exists: popupBorder.exists,
                    popup_border_visible: popupBorder.visible,
                    membership_modal_exists: membershipModal.exists,
                    membership_modal_visible: membershipModal.visible,
                    membership_modal_text: membershipModal.text,
                    popup_ok_exists: popupOk.exists,
                    popup_ok_visible: popupOk.visible,
                    popup_message_exists: popupMessage.exists,
                    popup_message_visible: popupMessage.visible,
                    popup_message_text: popupMessage.text,
                };
                """
            )
            if isinstance(state, dict):
                return {**fallback, **state}
        except Exception:
            pass
        return fallback

    def _wait_for_popup_border_state(
        self, visible: bool, timeout: float = 8.0, poll_interval: float = 0.2
    ) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = self._get_invite_modal_state()
            if bool(state.get("popup_border_visible")) is visible:
                return True
            time.sleep(poll_interval)
        state = self._get_invite_modal_state()
        return bool(state.get("popup_border_visible")) is visible

    def _wait_for_membership_modal_hidden(
        self, timeout: float = 5.0, poll_interval: float = 0.2
    ) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = self._get_invite_modal_state()
            if not bool(state.get("membership_modal_visible")):
                return True
            time.sleep(poll_interval)
        state = self._get_invite_modal_state()
        return not bool(state.get("membership_modal_visible"))

    def _dismiss_invite_form(self) -> bool:
        """如果邀请表单 modal 仍然打开，点击 Cancel 将其关闭。

        Awin 在 error（如"already exists"）时不自动关闭表单，
        需手动关闭，避免后续 publisher 的 invite_link.click() 受到干扰。
        """
        selectors = [
            "css:#membershipModal button.modal_cancel",
            "xpath=//*[@id='membershipModal']//button[contains(@class, 'modal_cancel')]",
            "css:button.modal_cancel",
        ]
        for sel in selectors:
            try:
                cancel = self.tab.ele(sel, timeout=1)
                if cancel:
                    cancel.click()
                    return True
            except Exception:
                continue
        try:
            clicked = self.tab.run_js(
                """
                const modal = document.querySelector('#membershipModal');
                if (!modal) return false;
                const cancel = modal.querySelector('button.modal_cancel');
                if (!cancel) return false;
                const style = window.getComputedStyle(modal);
                const rect = modal.getBoundingClientRect();
                if (
                    style.display === 'none' ||
                    style.visibility === 'hidden' ||
                    parseFloat(style.opacity || '1') === 0 ||
                    rect.width === 0 ||
                    rect.height === 0
                ) {
                    return false;
                }
                cancel.click();
                return true;
                """
            )
            if bool(clicked):
                return True
        except Exception:
            pass
        # 兜底：Escape 键
        try:
            self.tab.key_up("Escape")
            return True
        except Exception:
            pass
        return False

    def _dismiss_invite_form_until_closed(
        self, timeout: float = 5.0, poll_interval: float = 0.3
    ) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = self._get_invite_modal_state()
            if not bool(state.get("membership_modal_visible")):
                return True
            self._dismiss_invite_form()
            time.sleep(poll_interval)
        return self._wait_for_membership_modal_hidden(timeout=0.8, poll_interval=0.2)

    def _close_invite_result_popup(self, publisher_id: str) -> bool:
        """
        关闭发送邀请后的结果弹窗。
        兼容不同 DOM 结构，避免仅依赖单一 #popup_ok 导致卡住。
        """
        selectors = [
            "#popup_ok",
            "css:button#popup_ok",
            "#popup_border",
            "css:div#popup_border",
            "xpath=//button[@id='popup_ok']",
            "xpath=//button[contains(normalize-space(.), '确定')]",
            "xpath=//a[@id='popup_ok']",
        ]

        for sel in selectors:
            try:
                btn = self.tab.ele(sel, timeout=20)
                if not btn:
                    continue
                try:
                    btn.wait.displayed(timeout=20, raise_err=False)
                except Exception:
                    pass
                btn.click()
                if self._wait_for_popup_border_state(
                    visible=False, timeout=2.0, poll_interval=0.2
                ):
                    self._audit(
                        "invite_popup_closed",
                        click_seq=self._click_seq,
                        publisher_id=publisher_id,
                        close_selector=sel,
                    )
                    return True
            except Exception:
                continue

        # JS 兜底：在常见弹窗容器中寻找「确定/OK」按钮并点击
        try:
            clicked = self.tab.run_js(
                """
                const el = document.querySelector('#popup_ok');
                if (el) {
                    const style = window.getComputedStyle(el);
                    if (style && style.display !== 'none' && style.visibility !== 'hidden') {
                        el.click();
                        return true;
                    }
                }
                return false;
                """
            )
            if bool(clicked) and self._wait_for_popup_border_state(
                visible=False, timeout=2.0, poll_interval=0.2
            ):
                self._audit(
                    "invite_popup_closed",
                    click_seq=self._click_seq,
                    publisher_id=publisher_id,
                    close_selector="js_fallback",
                )
                return True
        except Exception:
            pass

        self._audit(
            "invite_click_failed",
            click_seq=self._click_seq,
            publisher_id=publisher_id,
            stage="close_popup_ok",
            error="popup_close_not_found",
        )
        return False

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
        invite_link = self.tab.ele(
            f'xpath=//a[@data-publisherid="{publisher_id}"]', timeout=2
        )
        if not invite_link:
            logger.warning(
                f"找不到 publisher ID: {publisher_id} 的邀请按钮，尝试重新获取页面元素"
            )
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
            invite_link = self.tab.ele(
                f'xpath=//a[@data-publisherid="{publisher_id}"]', timeout=2
            )
            if not invite_link:
                logger.warning(
                    f"重新获取后仍找不到 publisher ID: {publisher_id} 的邀请按钮，跳过"
                )
                self._audit(
                    "invite_button_missing",
                    click_seq=self._click_seq,
                    publisher_id=publisher_id,
                    after_refresh=True,
                )
                self._notify_feishu_invite_failure(publisher_id, "未找到按钮")
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
            self._notify_feishu_invite_failure(publisher_id, "点击失败")
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
                self._notify_feishu_invite_failure(publisher_id, "未找到输入框")
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
            self._notify_feishu_invite_failure(publisher_id, "填写信息失败")
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
                self._notify_feishu_invite_failure(publisher_id, "未找到发送按钮")
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
            self._notify_feishu_invite_failure(publisher_id, "点击发送失败")
            return False

        # 关闭结果弹窗（兼容多种弹窗结构，避免卡住）
        try:
            # 给弹窗一点渲染时间，再尝试关闭。
            self.tab.wait(0.3, 10)
            popup_border_visible = self._wait_for_popup_border_state(
                visible=True, timeout=8.0, poll_interval=0.2
            )
            result_popup_text = self._get_invite_result_popup_text()
            modal_state_before_close = self._get_invite_modal_state()
            if not popup_border_visible:
                html_fail = self._save_snapshot(publisher_id, "popup_border_not_found")
                self._audit(
                    "invite_send_failed",
                    click_seq=self._click_seq,
                    publisher_id=publisher_id,
                    stage="result_popup",
                    error="popup_border_not_found",
                    popup_text=result_popup_text,
                    modal_state_before_close=modal_state_before_close,
                    html_path=html_fail,
                )
                self._notify_feishu_invite_failure(
                    publisher_id, "发送后未出现结果弹窗"
                )
                return False
            if self.INVITE_ALREADY_EXISTS_TEXT in result_popup_text.lower():
                html_fail = self._save_snapshot(publisher_id, "invite_already_exists")
                self._audit(
                    "invite_send_failed",
                    click_seq=self._click_seq,
                    publisher_id=publisher_id,
                    stage="result_popup",
                    error="invitation_already_exists",
                    popup_text=result_popup_text,
                    modal_state_before_close=modal_state_before_close,
                    html_path=html_fail,
                )
                closed = self._close_invite_result_popup(publisher_id)
                dismissed = self._dismiss_invite_form_until_closed()
                if not closed:
                    self._notify_feishu_invite_failure(
                        publisher_id, "Invitation already exists，且关闭结果弹窗失败"
                    )
                    return False
                if not dismissed:
                    self._notify_feishu_invite_failure(
                        publisher_id, "Invitation already exists，且关闭邀请弹窗失败"
                    )
                    return False
                self._mark_publisher_processed(publisher_id)
                self._notify_feishu_invite_failure(
                    publisher_id,
                    "Invitation already exists. Please refresh and try again",
                )
                self.tab.wait(0.5, 10)
                return False

            closed = self._close_invite_result_popup(publisher_id)
            if not closed:
                self._notify_feishu_invite_failure(publisher_id, "关闭结果弹窗失败")
                return False

            self.tab.wait(0.2, 10)
            modal_state_after_close = self._get_invite_modal_state()
            if bool(modal_state_after_close.get("membership_modal_visible")):
                html_fail = self._save_snapshot(
                    publisher_id, "membership_modal_still_visible"
                )
                self._audit(
                    "invite_send_failed",
                    click_seq=self._click_seq,
                    publisher_id=publisher_id,
                    stage="result_popup",
                    error="membership_modal_still_visible_after_popup_close",
                    popup_text=result_popup_text,
                    modal_state_before_close=modal_state_before_close,
                    modal_state_after_close=modal_state_after_close,
                    html_path=html_fail,
                )
                dismissed = self._dismiss_invite_form_until_closed()
                modal_state_after_cancel = self._get_invite_modal_state()
                self._audit(
                    "invite_form_cancelled_after_popup_close",
                    click_seq=self._click_seq,
                    publisher_id=publisher_id,
                    dismissed=dismissed,
                    modal_state_after_cancel=modal_state_after_cancel,
                )
                if not dismissed:
                    self._notify_feishu_invite_failure(
                        publisher_id, "点击 OK 后邀请弹窗残留，且自动取消失败"
                    )
                    return False
                self._mark_publisher_processed(publisher_id)
                self._notify_feishu_invite_failure(
                    publisher_id, "点击 OK 后邀请弹窗仍保留，已自动取消并跳过"
                )
                self.tab.wait(0.5, 10)
                return False
        except Exception as e:
            self._audit(
                "invite_click_failed",
                click_seq=self._click_seq,
                publisher_id=publisher_id,
                stage="close_popup_ok",
                error=str(e),
            )
            self._notify_feishu_invite_failure(publisher_id, "关闭弹窗失败")
            return False

        # 保存成功发送后的快照
        html_after = self._save_snapshot(publisher_id, "after_click")
        self._audit(
            "invite_sent_success",
            click_seq=self._click_seq,
            publisher_id=publisher_id,
            html_path=html_after,
        )
        self._mark_publisher_processed(publisher_id)
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

    def run(self, invite_count: int, msg: str, template_name: str = ""):
        """
        执行 RPA 主流程
        invite_count: 需要发送的邀请数量
        msg: 申请信息内容
        """
        self.current_template_name = template_name.strip()
        sent_count = 0  # 已发送的邀请数量

        while sent_count < invite_count:
            # 每次循环都重新从网页获取所有 publisher IDs
            publisher_ids = self.get_publisher_ids()

            if not publisher_ids:
                logger.info("当前页面没有可邀请的 publisher，尝试下一页")
                self.click_next_page()
                continue

            logger.info(f"当前页面找到 {len(publisher_ids)} 个可邀请的 publisher")
            console.print(
                f"\n[bold blue]📧 已发送 {sent_count}/{invite_count} 条邀请[/bold blue]"
            )

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
                    console.print(
                        f"[green]✅ 已发送 {sent_count}/{invite_count}[/green]"
                    )
                    # 发送成功后立即跳出内层循环，重新获取页面上的所有 ID
                    break

            # 如果当前页所有 ID 都已经点击过，进入下一页
            if not found_new:
                logger.info("当前页所有 ID 都已经点击过，进入下一页")
                self.click_next_page()

        console.print(f"\n[bold green]✅ 已成功发送 {sent_count} 条邀请[/bold green]")
        self._notify(
            "任务完成",
            f"模板：{self._template_display_name()}\n已成功发送 {sent_count} 条邀请",
        )


class AppUI:
    """应用程序 UI 交互"""

    def __init__(self, rpa: AwinRPA):
        self.rpa = rpa
        self.message_manager = rpa.message_manager
        self.config_manager = ConfigManager()
        self.version_manager = VersionManager()
        self.updater = Updater()

    def _sync_rpa_notification_config(self):
        """将共享配置同步到当前 RPA 实例"""
        self.rpa.notify_channel = self.config_manager.notify_channel
        self.rpa.feishu_webhook_url = self.config_manager.feishu_webhook_url

    def _configure_feishu_notifications(self):
        """配置飞书通知开关与 Webhook"""
        while True:
            enabled_text = (
                "[green]已开启[/green]"
                if self.config_manager.feishu_enabled
                else "[yellow]已关闭[/yellow]"
            )
            webhook_text = self.config_manager.feishu_webhook_url or "(未配置)"
            console.print(
                Panel.fit("[bold cyan]🔔 飞书通知设置[/bold cyan]", border_style="cyan")
            )
            console.print(f"当前状态: {enabled_text}")
            console.print(f"Webhook URL: [dim]{webhook_text}[/dim]\n")

            action = questionary.select(
                "请选择操作:",
                choices=[
                    "✅ 开启飞书通知"
                    if not self.config_manager.feishu_enabled
                    else "❌ 关闭飞书通知",
                    "✏️ 设置 Webhook URL",
                    "🔙 返回设置",
                ],
            ).ask()

            if action is None or "返回" in action:
                return

            if "开启" in action:
                webhook_url = questionary.text(
                    "请输入飞书机器人 Webhook URL:",
                    default=self.config_manager.feishu_webhook_url,
                    validate=lambda x: (
                        True
                        if x.strip().startswith(("http://", "https://"))
                        else "请输入有效的 Webhook URL"
                    ),
                ).ask()
                if webhook_url is None:
                    console.print("[yellow]已取消开启飞书通知[/yellow]\n")
                    continue

                self.config_manager.feishu_webhook_url = webhook_url.strip()
                self.config_manager.set_feishu_enabled(True)
                self._sync_rpa_notification_config()
                console.print("[green]✅ 已开启飞书通知[/green]\n")
                return

            if "关闭" in action:
                self.config_manager.set_feishu_enabled(False)
                self._sync_rpa_notification_config()
                console.print("[green]✅ 已关闭飞书通知[/green]\n")
                return

            webhook_url = questionary.text(
                "请输入飞书机器人 Webhook URL:",
                default=self.config_manager.feishu_webhook_url,
                validate=lambda x: (
                    True
                    if x.strip().startswith(("http://", "https://"))
                    else "请输入有效的 Webhook URL"
                ),
            ).ask()
            if webhook_url is None:
                console.print("[yellow]已取消修改 Webhook URL[/yellow]\n")
                continue

            self.config_manager.feishu_webhook_url = webhook_url.strip()
            self._sync_rpa_notification_config()
            console.print("[green]✅ Webhook URL 已保存[/green]\n")

    def reset_clicked_mode(self):
        """重置已点击记录"""
        count = len(self.rpa._clicked_publisher_ids)
        console.print(
            Panel.fit("[bold red]🔄 重置已点击记录[/bold red]", border_style="red")
        )
        console.print(f"\n[bold]当前已点击记录:[/bold] [yellow]{count}[/yellow] 条\n")

        if count == 0:
            console.print("[dim]当前没有已点击记录，无需重置。[/dim]\n")
            return

        confirm = questionary.confirm(
            f"确认清空全部 {count} 条已点击记录？清空后可重新对这些 publisher 发送邀请。",
            default=False,
        ).ask()

        if not confirm:
            console.print("[yellow]已取消重置[/yellow]\n")
            return

        cleared = self.rpa.reset_clicked_ids()
        console.print(f"[green]✅ 已成功清除 {cleared} 条已点击记录[/green]\n")

    def check_update_mode(self):
        """检查更新"""
        console.print(
            Panel.fit("[bold cyan]🔍 检查更新[/bold cyan]", border_style="cyan")
        )
        console.print("\n[dim]正在连接 GitHub 检查新版本...[/dim]\n")

        result = self.updater.check_for_updates()

        if result["error"]:
            console.print(f"[red]❌ {result['error']}[/red]\n")
            return

        if not result["has_update"]:
            console.print(
                f"[green]✅ 当前已是最新版本 ({result['local_version']})[/green]\n"
            )
            return

        console.print(f"[bold yellow]发现新版本！[/bold yellow]")
        console.print(f"  当前版本: [red]{result['local_version']}[/red]")
        console.print(f"  最新版本: [green]{result['remote_version']}[/green]\n")

        confirm = questionary.confirm("是否立即更新？", default=True).ask()

        if not confirm:
            console.print("[yellow]已取消更新[/yellow]\n")
            return

        console.print("\n[dim]正在下载更新...[/dim]")

        def on_progress(filename, index, total):
            console.print(f"  [{index}/{total}] 正在更新 {filename}...")

        dl_result = self.updater.download_updates(on_progress=on_progress)

        if dl_result["success"]:
            console.print(f"\n[bold green]✅ {dl_result['message']}[/bold green]\n")
        else:
            console.print(f"\n[red]❌ {dl_result['message']}[/red]\n")

    def version_mode(self):
        """版本管理模式"""
        console.print(
            Panel.fit("[bold cyan]📦 版本管理[/bold cyan]", border_style="cyan")
        )

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
            choices=["📝 更新版本号", "📊 查看 Git 状态", "🔙 返回主菜单"],
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
            "取消",
        ]

        selection = questionary.select("请选择版本更新类型:", choices=choices).ask()

        if selection is None or "取消" in selection:
            return

        if "自定义" in selection:
            new_version = questionary.text(
                "请输入新版本号 (格式: x.y.z):",
                validate=lambda x: (
                    True if re.match(r"^\d+\.\d+\.\d+$", x) else "版本号格式应为 x.y.z"
                ),
            ).ask()
            if not new_version:
                return
        else:
            # 从选项中提取版本号
            new_version = selection.split(": ")[1]

        # 确认更新
        confirm = questionary.confirm(
            f"确认将版本从 {current_version} 更新到 {new_version}?", default=True
        ).ask()

        if not confirm:
            console.print("[yellow]已取消[/yellow]")
            return

        # 更新版本
        if self.version_manager.update_version(new_version):
            # 询问是否提交
            commit = questionary.confirm("是否使用 Git 提交此更改?", default=True).ask()

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
        """设置模式 - 管理邀请信息与通知"""
        console.print(
            Panel.fit(
                "[bold yellow]⚙️ 设置模式 - 管理邀请信息与通知[/bold yellow]",
                border_style="yellow",
            )
        )

        messages = self.message_manager.load()

        while True:
            feishu_status = "已开启" if self.config_manager.feishu_enabled else "已关闭"
            self.message_manager.display(messages)

            action = questionary.select(
                "请选择操作:",
                choices=[
                    "➕ 新增邀请信息",
                    "✏️ 编辑邀请信息",
                    "🗑️ 删除邀请信息",
                    f"🔔 飞书通知设置 ({feishu_status})",
                    "🔙 返回主菜单",
                ],
            ).ask()

            if action is None or "返回" in action:
                break
            elif "新增" in action:
                messages = self.message_manager.add(messages)
            elif "编辑" in action:
                messages = self.message_manager.edit(messages)
            elif "删除" in action:
                messages = self.message_manager.delete(messages)
            elif "飞书通知" in action:
                self._configure_feishu_notifications()

    def select_message(self) -> tuple[str, str]:
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

        choices = [f"{i + 1}. {msg['name']}" for i, msg in enumerate(messages)]

        selection = questionary.select("请选择要使用的邀请信息:", choices=choices).ask()

        if selection is None:
            console.print("[yellow]已取消操作[/yellow]")
            exit(0)

        idx = int(selection.split(".")[0]) - 1
        selected_msg = messages[idx]

        console.print(
            Panel(
                selected_msg["content"],
                title=f"[bold cyan]{selected_msg['name']}[/bold cyan]",
                border_style="cyan",
            )
        )

        modify = questionary.confirm("是否需要修改这条邀请信息?", default=False).ask()

        if modify:
            new_content = questionary.text(
                "请输入修改后的邀请信息 (支持多行):",
                default=selected_msg["content"],
                multiline=True,
            ).ask()

            if new_content is None:
                console.print("[yellow]已取消修改[/yellow]")
                return selected_msg["name"], selected_msg["content"]

            save_option = questionary.select(
                "是否保存这次修改?",
                choices=["仅本次使用 (不保存)", "覆盖原有信息", "保存为新的邀请信息"],
            ).ask()

            if save_option == "覆盖原有信息":
                messages[idx]["content"] = new_content
                self.message_manager.save(messages)
                console.print(
                    f"[green]✅ 已更新邀请信息: {selected_msg['name']}[/green]"
                )
            elif save_option == "保存为新的邀请信息":
                new_name = questionary.text(
                    "请输入新邀请信息的名称:",
                    default=f"{selected_msg['name']} (修改版)",
                ).ask()
                if new_name:
                    messages.append({"name": new_name, "content": new_content})
                    self.message_manager.save(messages)
                    console.print(f"[green]✅ 已保存新邀请信息: {new_name}[/green]")
                    return new_name, new_content

            return selected_msg["name"], new_content

        return selected_msg["name"], selected_msg["content"]

    def get_user_input(self) -> tuple[int, str, str]:
        """使用终端UI交互获取用户输入的参数"""
        console.print(
            Panel.fit(
                "[bold cyan]🤖 Awin RPA 自动化工具[/bold cyan]\n"
                "[dim]自动发送邀请给 Publisher[/dim]",
                border_style="cyan",
            )
        )

        action = questionary.select(
            "请选择操作:",
            choices=[
                "🚀 开始执行 RPA",
                "⚙️ 设置模式 (管理邀请信息与通知)",
                "🔄 重置已点击记录",
                "🔍 检查更新",
                "📦 版本管理",
                "❌ 退出",
            ],
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

        if "检查更新" in action:
            self.check_update_mode()
            return self.get_user_input()

        if "版本" in action:
            self.version_mode()
            return self.get_user_input()

        invite_count = questionary.text(
            "请输入要发送的邀请数量:",
            default="10",
            validate=lambda x: x.isdigit() and int(x) > 0 or "请输入有效的正整数",
        ).ask()

        if invite_count is None:
            console.print("[yellow]已取消操作[/yellow]")
            exit(0)

        template_name, msg = self.select_message()

        console.print("\n[bold]📋 执行配置:[/bold]")
        console.print(f"  • 发送数量: [green]{invite_count}[/green]")
        console.print(f"  • 模板名称: [cyan]{template_name}[/cyan]")
        console.print(
            f"  • 消息内容: [dim]{msg[:50]}...[/dim]"
            if len(msg) > 50
            else f"  • 消息内容: [dim]{msg}[/dim]"
        )

        confirm = questionary.confirm("\n确认开始执行?", default=True).ask()

        if not confirm:
            console.print("[yellow]已取消操作[/yellow]")
            exit(0)

        return int(invite_count), template_name, msg

    def start(self):
        """启动应用程序"""
        invite_count, template_name, msg = self.get_user_input()

        console.print("\n[bold green]🚀 开始执行 RPA...[/bold green]")
        try:
            self.rpa.run(
                invite_count=invite_count, msg=msg, template_name=template_name
            )
        except Exception as e:
            try:
                self.rpa._notify(
                    "任务失败",
                    f"模板：{self.rpa._template_display_name()}\n执行异常：{e}",
                )
            except Exception:
                pass
            raise
        console.print("\n[bold green]✅ 执行完成![/bold green]")


if __name__ == "__main__":
    rpa = AwinRPA()
    app = AppUI(rpa)
    app.start()
