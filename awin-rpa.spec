# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 打包配置：GUI 入口 pyside6_app.py，onedir 独立目录模式。
# 构建命令: .venv/Scripts/python.exe -m PyInstaller awin-rpa.spec --noconfirm
# 产物: dist/AwinRPA/AwinRPA.exe 及同目录依赖

a = Analysis(
    ['pyside6_app.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # 仅 TUI/未使用的大型依赖，排除以减小安装包体积
        'pandas',
        'textual',
        'tkinter',
        'pytest',
        'numpy',  # 代码未直接使用（bs4/pydantic 的可选依赖被连带打包），裁掉省约 28MB
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='AwinRPA',
    debug=False,
    strip=False,
    upx=False,
    console=False,  # GUI 程序：不弹控制台窗口；日志走 logging_setup 文件日志
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='AwinRPA',
)
