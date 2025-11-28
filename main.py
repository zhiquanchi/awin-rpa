from DrissionPage import Chromium
from loguru import logger
import re
import pandas as pd
from bs4 import BeautifulSoup
import questionary
from rich.console import Console
from rich.panel import Panel

console = Console()

logger.add("file.log")

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
def main(page_count:int,msg:str):
    '''
    rpa 主函数。
    page_count: 需要处理的页数。
    msg: 申请信息内容。
    '''
    for i in range(page_count):
        publisher_ids=get_publisher_ids()
        for publisher_id in publisher_ids:
            logger.info(f"向 publisher ID: {publisher_id}的发送invitation")
            # 点击对应的publisher ID的邀请按钮
            click_invite_button_by_publisher_id(publisher_id)
            # 输入邀请信息
            input_message(message=msg)
            # 点击 send invite 按钮
            tab('.btn-small-green modal_save').click()
            # 等待Your invitation has been sent. 弹窗出现,如果指定时间内未出现则报错
            popup_border = tab.wait.ele_displayed('#popup_ok', timeout=10,raise_err=True)
            # 判断 popup_border 是发送成功，还是已经邀请过
            # TODO 这里可以根据 popup_border 的内容进行不同的处理。如果邀请过可能会无法进行下一步。实际执行中有问题再来看如何修复。
            # 点击ok按钮关闭弹窗
            tab('#popup_ok').click()

            tab.wait(3,5)
        # 点击下一页
        click_next()


def get_user_input():
    '''
    使用终端UI交互获取用户输入的参数
    '''
    console.print(Panel.fit(
        "[bold cyan]🤖 Awin RPA 自动化工具[/bold cyan]\n"
        "[dim]自动发送邀请给 Publisher[/dim]",
        border_style="cyan"
    ))
    
    # 获取页数
    page_count = questionary.text(
        "请输入要处理的页数:",
        default="1",
        validate=lambda x: x.isdigit() and int(x) > 0 or "请输入有效的正整数"
    ).ask()
    
    if page_count is None:  # 用户按了 Ctrl+C
        console.print("[yellow]已取消操作[/yellow]")
        exit(0)
    
    # 选择是否使用默认消息
    use_default_msg = questionary.confirm(
        "是否使用默认邀请消息?",
        default=True
    ).ask()
    
    default_msg = '''Join Giftlab Affiliate Program(95201) on Awin!
Want to offer your audience unique gifts while earning one of the best commission rates in the industry?
I'm from Giftlab, and we'd love to partner. Our Awin program offers:
💥 20% Commission on Your First Order 
✅ 10% Standard Commission & More Flexible Commissions
Your content is a perfect match for our brand. Join us to boost your revenue!
'''
    
    if use_default_msg:
        msg = default_msg
    else:
        msg = questionary.text(
            "请输入自定义邀请消息 (多行输入，输入完成后按 Enter):",
            multiline=True
        ).ask()
        if msg is None:
            console.print("[yellow]已取消操作[/yellow]")
            exit(0)
    
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
    
    
