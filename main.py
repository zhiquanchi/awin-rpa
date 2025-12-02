from DrissionPage import Chromium
from loguru import logger
import re
import pandas as pd
from bs4 import BeautifulSoup
import questionary
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
import json
from pathlib import Path

console = Console()

logger.add("file.log")

# 邀请信息配置文件路径
MESSAGES_FILE = Path(__file__).parent / "invitation_messages.json"


def load_messages() -> list[dict]:
    """从文件加载所有邀请信息"""
    if MESSAGES_FILE.exists():
        try:
            with open(MESSAGES_FILE, "r", encoding="utf-8") as f:
                messages = json.load(f)
                if messages:
                    return messages
        except (json.JSONDecodeError, IOError):
            pass
    # 如果文件不存在,提示用户创建
    console.print("[yellow]⚠️ 未找到邀请信息配置文件，请先在设置模式中添加邀请信息[/yellow]")
    return []


def save_messages(messages: list[dict]):
    """保存邀请信息到文件"""
    with open(MESSAGES_FILE, "w", encoding="utf-8") as f:
        json.dump(messages, f, ensure_ascii=False, indent=2)


def display_messages(messages: list[dict]):
    """显示所有邀请信息"""
    for idx, msg in enumerate(messages, 1):
        console.print(Panel(
            msg["content"],
            title=f"[bold cyan]#{idx} {msg['name']}[/bold cyan]",
            border_style="cyan",
            expand=False
        ))
        console.print()  # 添加空行分隔


def add_message(messages: list[dict]) -> list[dict]:
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
    save_messages(messages)
    console.print(f"[green]✅ 已添加邀请信息: {name}[/green]")
    return messages


def edit_message(messages: list[dict]) -> list[dict]:
    """编辑邀请信息"""
    if not messages:
        console.print("[yellow]没有可编辑的邀请信息[/yellow]")
        return messages
    
    display_messages(messages)
    
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
    
    save_messages(messages)
    console.print(f"[green]✅ 已更新邀请信息: {messages[idx]['name']}[/green]")
    return messages


def delete_message(messages: list[dict]) -> list[dict]:
    """删除邀请信息"""
    if len(messages) <= 0:
        console.print("[yellow]没有可删除的邀请信息[/yellow]")
        return messages
    
    display_messages(messages)
    
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
        save_messages(messages)
        console.print(f"[green]✅ 已删除邀请信息: {deleted_name}[/green]")
    
    return messages


def settings_mode():
    """设置模式 - 管理邀请信息"""
    console.print(Panel.fit(
        "[bold yellow]⚙️ 设置模式 - 管理邀请信息[/bold yellow]",
        border_style="yellow"
    ))
    
    messages = load_messages()
    
    while True:
        display_messages(messages)
        
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
            messages = add_message(messages)
        elif "编辑" in action:
            messages = edit_message(messages)
        elif "删除" in action:
            messages = delete_message(messages)


def select_message() -> str:
    """选择或修改邀请信息"""
    messages = load_messages()
    
    # 检查是否有可用的邀请信息
    if not messages:
        console.print("[red]❌ 没有可用的邀请信息，请先在设置模式中添加[/red]")
        settings_mode()
        messages = load_messages()
        if not messages:
            console.print("[red]❌ 仍然没有邀请信息，无法继续[/red]")
            exit(1)
    
    # 显示当前邀请信息
    console.print("\n[bold]📧 当前可用的邀请信息:[/bold]")
    display_messages(messages)
    
    # 选择邀请信息
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
    
    # 显示完整内容
    console.print(Panel(
        selected_msg["content"],
        title=f"[bold cyan]{selected_msg['name']}[/bold cyan]",
        border_style="cyan"
    ))
    
    # 询问是否需要修改
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
        
        # 询问是否保存修改
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
            save_messages(messages)
            console.print(f"[green]✅ 已更新邀请信息: {selected_msg['name']}[/green]")
        elif save_option == "保存为新的邀请信息":
            new_name = questionary.text(
                "请输入新邀请信息的名称:",
                default=f"{selected_msg['name']} (修改版)"
            ).ask()
            if new_name:
                messages.append({"name": new_name, "content": new_content})
                save_messages(messages)
                console.print(f"[green]✅ 已保存新邀请信息: {new_name}[/green]")
        
        return new_content
    
    return selected_msg["content"]

browser = Chromium()
tab = browser.latest_tab

    # 转到指定页面

def goto_page():
    '''
    跳转到邀请页面。
    '''
    # target_url = "https://ui.awin.com/awin/affiliate/2646380/merchant-directory/index/tab/notJoined/page/1"
    target_url ='https://ui.awin.com/awin/merchant/45307/affiliate-directory/index/tab/notInvited'
    tab.get(target_url)
    

def sector():
    '''选择筛选项目'''
    tab.ele('text=Finance & Insurance').click()
    tab.ele('text=Credit Cards').click()
    # tab.ele('text=Green').click()

def get_list():
    ul_elements = tab.ele('xpath=//*[@id="directoryResults"]/table')
    logger.info(ul_elements.html)

def get_table_rows()->str:
    # action_ids_list:list = []
    ul_elements = tab.ele('xpath=//*[@id="directoryResults"]/table')
    for ul_element in ul_elements.eles('xpath=./tbody/tr'):
        action_ids = re.findall(r'id="(action\d+)"', ul_element.html)
        # action_ids_list.append(action_ids[0])
        yield action_ids[0]
        # logger.info(action_ids[0])
        # 点击id为 action_ids[0] 的元素。这个是每一行的第一个action按钮。
        #  TODO 增加筛选功能。有的列表是已经拒绝的。拒绝的人，不用点击。
        # tab(f'#{action_ids[0]}').click()
        # logger.info(ul_element.html)

def get_table_rows_with_bs4_pandas():
    ul_elements = tab.ele('xpath=//*[@id="directoryResults"]/table')
    soup = BeautifulSoup(ul_elements.html, 'html5lib')
    df = pd.read_html(str(soup))[0]
    logger.info(df.columns)
    # for ul_element in ul_elements.eles('xpath=./tbody/tr'):

def get_table_rows_with_bs4()->list[str]:
    '''
    获取每个join按钮的 id值。
    '''
    ul_elements = tab.ele('xpath=//*[@id="directoryResults"]/table')
    soup = BeautifulSoup(ul_elements.html, 'html5lib')
    join_buttons = soup.find_all(
    'span',
    class_=lambda c: c and 'partnership-button' in c and 'join-button' in c,
    id=lambda x: x and x.startswith('action')
    )
    ids = [span.get('id') for span in join_buttons]
    return ids

def get_publisher_ids() -> list[str]:  
    '''获取所有 publisher ID'''  
    table = tab.ele('xpath=//*[@id="directoryResults"]/table')  
    invite_links = table.eles('xpath:.//a[@data-publisherid]')  
    publisher_ids = [link.attr('data-publisherid') for link in invite_links]  
    return publisher_ids

def click_Jion_button(id:str):
    '''
    点击指定id的join按钮。
    '''
    tab(f'#{id}').click()

def click_invite_button_by_publisher_id(publisher_id: str):  
    '''通过 publisher ID 点击邀请按钮'''  
    invite_link = tab.ele(f'xpath=//a[@data-publisherid="{publisher_id}"]')  
    invite_link.click()  

def input_message(message:str):
    '''
    在申请框里面填写申请信息。
    '''
    tab('#customMessage').input(message)



def click_next():
    '''
    点击下一页按钮
    '''
    tab('#nextPage').click()
    # 等待页面加载完成  
    tab.wait.doc_loaded()  # 等待文档加载完成
    # 等待表格内容更新
    tab.wait(2, 4)  # 额外等待确保内容刷新


def send_invite_to_publisher(publisher_id: str, msg: str) -> bool:
    '''
    向单个 publisher 发送邀请
    返回 True 表示成功，False 表示按钮不存在（需要刷新列表）
    '''
    global tab
    
    # 查找对应的邀请按钮
    invite_link = tab.ele(f'xpath=//a[@data-publisherid="{publisher_id}"]', timeout=2)
    if not invite_link:
        logger.warning(f"找不到 publisher ID: {publisher_id} 的邀请按钮，尝试重新获取页面元素")
        # 重新连接浏览器获取最新的 tab 对象
        tab = browser.latest_tab
        # 再次尝试查找
        invite_link = tab.ele(f'xpath=//a[@data-publisherid="{publisher_id}"]', timeout=2)
        if not invite_link:
            logger.warning(f"重新获取后仍找不到 publisher ID: {publisher_id} 的邀请按钮，跳过")
            return False
    
    logger.info(f"向 publisher ID: {publisher_id} 发送 invitation")
    # 点击邀请按钮
    invite_link.click()
    
    # 输入邀请信息
    input_message(message=msg)
    
    # 等待 send invite 按钮可点击，然后点击
    send_btn = tab.ele('css:button.btn-small-green.modal_save')
    send_btn.wait.clickable(timeout=10)
    send_btn.click()
    
    # 等待弹窗出现
    popup_ok_btn = tab.ele('#popup_ok')
    popup_ok_btn.wait.displayed(timeout=10, raise_err=True)
    
    # 点击ok按钮关闭弹窗
    popup_ok_btn.click()
    
    tab.wait(2, 3)
    return True


def main(page_count: int, msg: str):
    '''
    rpa 主函数。
    page_count: 需要处理的页数。
    msg: 申请信息内容。
    '''
    for i in range(page_count):
        console.print(f"\n[bold blue]📄 正在处理第 {i + 1}/{page_count} 页[/bold blue]")
        
        # 处理当前页面，直到没有可邀请的 publisher
        while True:
            # 获取当前页面的 publisher IDs
            publisher_ids = get_publisher_ids()
            
            if not publisher_ids:
                logger.info("当前页面没有可邀请的 publisher，进入下一页")
                break
            
            logger.info(f"当前页面找到 {len(publisher_ids)} 个可邀请的 publisher")
            
            # 逐个处理
            processed_any = False
            for publisher_id in publisher_ids:
                success = send_invite_to_publisher(publisher_id, msg)
                if success:
                    processed_any = True
                # 如果失败（按钮不存在），继续尝试下一个
            
            # 如果这一轮没有成功处理任何一个，说明列表已经空了或都失效了
            if not processed_any:
                logger.info("当前页面所有按钮都已失效，进入下一页")
                break
        
        # 如果不是最后一页，点击下一页
        if i < page_count - 1:
            click_next()
    
    console.print(f"\n[bold green]✅ 已处理完 {page_count} 页[/bold green]")


def get_user_input():
    '''
    使用终端UI交互获取用户输入的参数
    '''
    console.print(Panel.fit(
        "[bold cyan]🤖 Awin RPA 自动化工具[/bold cyan]\n"
        "[dim]自动发送邀请给 Publisher[/dim]",
        border_style="cyan"
    ))
    
    # 主菜单选择
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
        settings_mode()
        # 设置完成后重新显示主菜单
        return get_user_input()
    
    # 获取页数
    page_count = questionary.text(
        "请输入要处理的页数:",
        default="1",
        validate=lambda x: x.isdigit() and int(x) > 0 or "请输入有效的正整数"
    ).ask()
    
    if page_count is None:  # 用户按了 Ctrl+C
        console.print("[yellow]已取消操作[/yellow]")
        exit(0)
    
    # 选择邀请信息
    msg = select_message()
    
    # 确认执行
    console.print("\n[bold]📋 执行配置:[/bold]")
    console.print(f"  • 处理页数: [green]{page_count}[/green]")
    console.print(f"  • 消息内容: [dim]{msg[:50]}...[/dim]" if len(msg) > 50 else f"  • 消息内容: [dim]{msg}[/dim]")
    
    confirm = questionary.confirm(
        "\n确认开始执行?",
        default=True
    ).ask()
    
    if not confirm:
        console.print("[yellow]已取消操作[/yellow]")
        exit(0)
    
    return int(page_count), msg


if __name__ == "__main__":
    # login()
    # get_list()
    # goto_page()
    # sector()
    
    # 使用交互式界面获取用户输入
    page_count, msg = get_user_input()
    
    console.print("\n[bold green]🚀 开始执行 RPA...[/bold green]")
    main(page_count=page_count, msg=msg)
    console.print("\n[bold green]✅ 执行完成![/bold green]")
    
    
