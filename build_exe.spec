# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置。

构建：
    pip install pyinstaller
    pyinstaller build_exe.spec --noconfirm

产物：dist/悉尼约会助理.exe（单文件）

打包进 exe 的是「只读资源」（评分锚点、采集源清单、邮件模板、离线样例）；
config.yaml 和 data/ 留在 exe 外面，首次运行会自动生成 config.yaml，
这样用户改配置、以及历史数据库都不会因为换新版 exe 而丢失。
"""

datas = [
    ("rubric.yaml", "."),
    ("sources.yaml", "."),
    ("config.example.yaml", "."),
    ("templates", "templates"),
    ("fixtures", "fixtures"),
]

hiddenimports = [
    "feedparser",
    "icalendar",
    "dateutil",
    "yaml",
    "jinja2",
    "requests",
    "scout.collectors.generic",
    "scout.collectors.astro",
    "scout.collectors.fixtures",
    "scout.collectors.cityofsydney",
]

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 这些是 PyInstaller 会顺手打进来但本项目用不到的大块头，排除以缩小体积
    excludes=["matplotlib", "numpy", "pandas", "scipy", "PIL", "pytest", "anthropic"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="悉尼约会助理",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # 无控制台窗口：定时任务静默运行，日志写 data/out/scout.log
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
