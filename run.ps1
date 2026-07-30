# Awin RPA 启动脚本
# 双击运行（需将 .ps1 关联到 powershell.exe，见下方说明）

# 切换到脚本所在目录，保证相对路径（配置文件、模板等）正确
Set-Location -Path $PSScriptRoot

# 检查 uv 是否可用
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "未找到 uv 命令，请先安装 uv 或将其加入 PATH。" -ForegroundColor Red
    Read-Host "按回车键退出"
    exit 1
}

uv run main.py

# 程序退出后暂停，便于查看输出或报错
if ($LASTEXITCODE -ne 0) {
    Write-Host "`n程序异常退出，退出码: $LASTEXITCODE" -ForegroundColor Red
}
Read-Host "`n按回车键关闭窗口"
