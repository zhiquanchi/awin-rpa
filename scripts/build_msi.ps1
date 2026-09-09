# 构建 AwinRPA MSI 安装包：PyInstaller 打包 -> WiX 生成 MSI
# 用法: powershell -ExecutionPolicy Bypass -File scripts/build_msi.ps1
# 产物: dist/AwinRPA-<版本>-x64.msi
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

# 版本号取自 pyproject.toml
$version = (Select-String -Path pyproject.toml -Pattern '^version\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value
Write-Host "==> 版本: $version" -ForegroundColor Cyan

Write-Host "==> [1/3] PyInstaller 打包..." -ForegroundColor Cyan
& .venv\Scripts\python.exe -m PyInstaller awin-rpa.spec --noconfirm
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 打包失败" }

$buildDir = "dist\AwinRPA"
if (-not (Test-Path (Join-Path $buildDir "AwinRPA.exe"))) { throw "未找到 $buildDir\AwinRPA.exe" }

Write-Host "==> [2/3] 裁剪未使用的 Qt 模块..." -ForegroundColor Cyan
# 参考 https://www.zhihu.com/question/48776632/answer/2336654649
# GUI 只用 QtCore/QtGui/QtWidgets；删掉 Quick/QML/OpenGL 软渲染/PDF/虚拟键盘
# 等与 QtWidgets 无依赖关系的模块，以及除中文外的全部 Qt 翻译。
# 注意：platforms(qwindows.dll)/imageformats/tls/styles 插件是 GUI 必需，不可删。
$pyside = Join-Path $buildDir "_internal\PySide6"
$pruneFiles = @(
    "opengl32sw.dll",        # 软件 OpenGL 后备渲染器，约 20MB
    "Qt6Quick.dll", "Qt6QuickWidgets.dll", "Qt6QuickControls2.dll",
    "Qt6Qml.dll", "Qt6QmlModels.dll", "Qt6QmlWorkerScript.dll", "Qt6QmlMeta.dll",
    "Qt6Pdf.dll",            # PDF 渲染模块
    "Qt6VirtualKeyboard.dll",
    "Qt6Test.dll", "Qt6Help.dll", "Qt6Designer.dll", "Qt6UiTools.dll",
    "Qt6WebEngineCore.dll", "Qt6WebEngineWidgets.dll", "Qt6WebChannel.dll",
    "Qt6Multimedia.dll", "Qt6MultimediaWidgets.dll",
    "Qt6Bluetooth.dll", "Qt6Nfc.dll", "Qt6Positioning.dll", "Qt6Sensors.dll",
    "Qt6SerialPort.dll", "Qt6RemoteObjects.dll", "Qt6WebSockets.dll",
    "Qt63DCore.dll", "Qt63DRender.dll", "Qt63DInput.dll", "Qt63DLogic.dll",
    "Qt6Charts.dll", "Qt6DataVisualization.dll", "Qt6Sql.dll",
    "Qt6OpenGLWidgets.dll", "Qt6OpenGL.dll"
)
foreach ($f in $pruneFiles) {
    $p = Join-Path $pyside $f
    if (Test-Path $p) { Remove-Item $p -Force; Write-Host "  删除 $f" }
}
# QML/Quick 的 pyd 绑定（若被打入）
Get-ChildItem $pyside -Filter "Qt*.pyd" | Where-Object {
    $_.Name -match "Quick|Qml|Pdf|VirtualKeyboard|Test|Help|Designer|WebEngine|Multimedia|Bluetooth|Nfc|Positioning|Sensors|SerialPort|RemoteObjects|WebSockets|3D|Charts|Sql|OpenGL"
} | ForEach-Object { Remove-Item $_.FullName -Force; Write-Host "  删除 $($_.Name)" }
# 翻译文件只保留中文
$trans = Join-Path $pyside "translations"
if (Test-Path $trans) {
    Get-ChildItem $trans -File | Where-Object { $_.Name -notmatch "zh" } | Remove-Item -Force
    Write-Host "  翻译仅保留 zh_*"
}

Write-Host "==> [3/3] WiX 生成 MSI..." -ForegroundColor Cyan
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
