# Awin RPA

用于在 Awin 页面自动发送邀请消息，支持模板管理、发送数量控制、执行日志、重置记录与自动更新。

## 浏览器连接说明

程序默认连接 `127.0.0.1:9222`。如果本地没有可用的调试端口，程序会自动启动一个带远程调试端口的 Chrome，并使用独立用户数据目录，避免和你平时打开的 Chrome 实例互相影响。

启动示例：

```powershell
& "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222
```

如需修改连接地址，可设置环境变量：

- `AWIN_BROWSER_DEBUG_HOST`
- `AWIN_BROWSER_DEBUG_PORT`
- `AWIN_CHROME_PATH`：指定 Chrome 可执行文件路径
- `AWIN_CHROME_USER_DATA_DIR`：指定自动启动 Chrome 使用的数据目录

说明：

- 当 `AWIN_BROWSER_DEBUG_HOST` 为本地地址（`127.0.0.1`、`localhost`、`::1`）时，程序支持自动拉起 Chrome
- 当连接的是远程调试地址时，仍需先在目标机器上手动启动 Chrome 并开放对应调试端口

## 通知功能说明

当“邀请失败”时，程序支持以下通知渠道：

- `仅本地通知`：桌面通知（默认）
- `仅飞书通知`：只发送到飞书群机器人
- `本地 + 飞书`：同时发送桌面通知和飞书通知
- `不通知`：关闭失败通知

## UI 中配置通知渠道

在 `tui_app.py` 的 UI 页面中（发送数量一行）：

1. 点击“通知配置”区域的 `修改`
2. 点击 `切换` 按钮选择通知渠道
3. 如果选择 `仅飞书通知` 或 `本地 + 飞书`，必须在“飞书 Webhook”输入框中填写 `webhook_url`
4. 点击 `保存`

说明：

- 选择 `仅飞书通知` 或 `本地 + 飞书` 时，必须配置 `webhook_url`

## 终端设置模式中配置飞书通知

运行 `main.py` 后，可在 `⚙️ 设置模式 (管理邀请信息与通知)` 中进入 `🔔 飞书通知设置`：

1. 选择是否开启飞书通知
2. 开启时输入飞书机器人 `webhook_url`
3. 保存后会写入同一份 `tui_config.json`

## 飞书 Webhook 配置优先级

飞书 webhook 的读取顺序如下：

1. UI 配置（`tui_config.json` 中的 `feishu_webhook_url`，终端模式与 TUI 共用）
2. 环境变量 `FEISHU_WEBHOOK_URL`
3. 项目根目录 `feishu_webhook.txt`（文件内容为 webhook URL）

## 失败通知消息内容

飞书消息为纯文本，包含：

- Publisher ID
- 失败原因
- 当前页面 URL
- UTC 时间
