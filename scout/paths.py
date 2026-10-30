"""路径解析：区分「只读资源」与「可写数据」，兼容 PyInstaller 打包后的 exe。

打包成 exe 后目录长这样：

    悉尼约会助理.exe          ← 单文件，内部打包了 rubric/sources/templates/fixtures
    config.yaml               ← 外部，用户可编辑
    data/
      scout.db                ← 外部，历史数据不会因为换 exe 而丢
      out/

所以：
  resource_dir() —— 读 rubric.yaml / templates / fixtures，frozen 时指向解压出的临时目录
  data_dir()     —— 写 SQLite / HTML，永远指向 exe（或项目）所在目录，重启后仍在
"""
from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def enable_dpi_awareness() -> None:
    """高分屏上让 tkinter 按物理像素渲染，否则字会发虚。

    必须在创建任何窗口之前调用。调用后窗口尺寸与字号都要自己按 DPI 放大，
    见 apply_scaling()。
    """
    if sys.platform != "win32":
        return
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)     # PROCESS_SYSTEM_DPI_AWARE
    except Exception:                               # noqa: BLE001
        try:
            from ctypes import windll
            windll.user32.SetProcessDPIAware()      # 老版本 Windows 的回退
        except Exception:                           # noqa: BLE001
            pass


def _query_dpi(window) -> float:
    """取当前窗口所在屏幕的实际 DPI。

    不能只信 tk 的 winfo_fpixels('1i')：PyInstaller 打出来的 exe 自带
    per-monitor DPI aware 的清单，这种模式下 tk 会返回 96 而不是真实的 192，
    导致窗口在高分屏上缩成一半大小。所以优先问 Windows 本身。
    """
    if sys.platform == "win32":
        try:
            from ctypes import windll
            hwnd = window.winfo_id()
            dpi = windll.user32.GetDpiForWindow(hwnd)       # Win10 1607+，按显示器
            if dpi:
                return float(dpi)
        except Exception:                                   # noqa: BLE001
            pass
        try:
            from ctypes import windll
            dpi = windll.user32.GetDpiForSystem()
            if dpi:
                return float(dpi)
        except Exception:                                   # noqa: BLE001
            pass
    try:
        return float(window.winfo_fpixels("1i"))
    except Exception:                                       # noqa: BLE001
        return 96.0


def apply_scaling(window) -> float:
    """让 tk 的磅值字号按屏幕 DPI 正确换算，返回缩放系数。

    开了 DPI awareness 之后，窗口按物理像素算，2880x1800 这类高分屏上
    1020x680 的窗口会小得看不清 —— 所以尺寸也要乘这个系数。
    """
    window.update_idletasks()          # 先让窗口真正创建出来，否则拿不到 hwnd
    dpi = _query_dpi(window)
    if not dpi:
        return 1.0
    window.tk.call("tk", "scaling", dpi / 72.0)     # 磅 → 像素
    return dpi / 96.0                               # 96 dpi 为 1.0 基准


def resource_dir() -> Path:
    """只读资源根目录。"""
    if is_frozen():
        # PyInstaller 单文件模式会把打包内容解压到 sys._MEIPASS
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    """可写数据根目录（配置、数据库、输出都以此为基准）。"""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def default_config_path() -> Path:
    """配置文件永远用 exe/项目同级的 config.yaml。

    打包后首次运行时该文件还不存在，就把内嵌的 config.example.yaml 复制出来 ——
    这样用户双击就能跑，同时又拿到一份可以直接编辑的配置（改预算、住址、
    区域锚点都在这里），而不是去动 exe 内部读不到的副本。
    """
    external = data_dir() / "config.yaml"
    if external.exists():
        return external

    bundled_example = resource_dir() / "config.example.yaml"
    if bundled_example.exists():
        try:
            external.write_text(
                bundled_example.read_text(encoding="utf-8"), encoding="utf-8")
            return external
        except OSError:
            return bundled_example      # 目录只读等极端情况，退而求其次
    return external          # 交给 load_config 报出明确的「找不到配置」错误
