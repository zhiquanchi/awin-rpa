# 构建 AwinRPA MSI 安装包：PyInstaller 打包 -> WiX 生成 MSI
# 用法: powershell -ExecutionPolicy Bypass -File scripts/build_msi.ps1
# 产物: dist/AwinRPA-<版本>-x64.msi
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

# 版本号取自 pyproject.toml
$version = (Select-String -Path pyproject.toml -Pattern '^version\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value
Write-Host "==> 版本: $version" -ForegroundColor Cyan

Write-Host "==> [1/2] PyInstaller 打包..." -ForegroundColor Cyan
& .venv\Scripts\python.exe -m PyInstaller awin-rpa.spec --noconfirm
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 打包失败" }

$buildDir = "dist\AwinRPA"
if (-not (Test-Path (Join-Path $buildDir "AwinRPA.exe"))) { throw "未找到 $buildDir\AwinRPA.exe" }

Write-Host "==> [2/2] WiX 生成 MSI..." -ForegroundColor Cyan
$msi = "dist\AwinRPA-$version-x64.msi"
# WiX 的 Files Include 相对路径基于 wxs 所在目录解析，必须传绝对路径
$buildDirAbs = (Resolve-Path $buildDir).Path
& wix build packaging\AwinRPA.wxs `
    -arch x64 `
    -o $msi `
    -define "BuildDir=$buildDirAbs" `
    -define "AppVersion=$version"
if ($LASTEXITCODE -ne 0) { throw "WiX 打包失败" }

$item = Get-Item $msi
Write-Host ("==> 完成: {0} ({1:N0} MB)" -f $item.FullName, ($item.Length / 1MB)) -ForegroundColor Green
