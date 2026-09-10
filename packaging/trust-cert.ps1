# 让 Windows 信任 AwinRPA 内部签名证书（一次即可，需管理员）
# 用法: 右键"使用 PowerShell 运行"，或在管理员 PowerShell 中执行本脚本
# 效果: 安装 AwinRPA MSI 时"发布者"显示为已验证的 AwinRPA Internal，不再是未知发布者
# 说明: 不影响浏览器/SmartScreen 的下载提醒；签名证书为内部自签名
$ErrorActionPreference = "Stop"

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
    ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Start-Process powershell -Verb RunAs -ArgumentList @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $PSCommandPath
    )
    exit
}

$cer = Join-Path $PSScriptRoot "AwinRPA-signing.cer"
if (-not (Test-Path $cer)) { throw "未找到证书文件 $cer，请与本脚本放在同一目录" }

Import-Certificate -FilePath $cer -CertStoreLocation Cert:\LocalMachine\Root | Out-Null
Import-Certificate -FilePath $cer -CertStoreLocation Cert:\LocalMachine\TrustedPublisher | Out-Null
Write-Host "AwinRPA 内部签名证书已安装到本机信任库。"
Read-Host "按回车键关闭"
