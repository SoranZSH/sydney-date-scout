"""桌面弹窗通知。

只用标准库 tkinter：不引入额外依赖，打包进 exe 也不会出问题。
弹窗列出本次新推荐的活动，点「查看详情」直接拉起 GUI 主程序。
"""
from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

from .paths import apply_scaling, enable_dpi_awareness, is_frozen

log = logging.getLogger(__name__)


def _launch_gui() -> None:
    """拉起 GUI 主程序。打包后就是 exe 自己（带 --gui 参数），
    开发环境下则是 python gui.py。"""
    try:
        if is_frozen():
            subprocess.Popen([sys.executable, "--gui"], close_fds=True)
        else:
            gui = Path(__file__).resolve().parent.parent / "gui.py"
            subprocess.Popen([sys.executable, str(gui)], close_fds=True)
    except Exception:                                  # noqa: BLE001
        log.warning("拉起 GUI 失败", exc_info=True)


def notify(events: list[Any], empty_reason: Optional[str] = None,
           auto_close_seconds: int = 0) -> None:
    """弹出通知窗口。

    events        —— 本次新推荐的活动（Event 或 dict 均可）
    empty_reason  —— 无推荐时的说明文案；传了就显示「本次无推荐」样式
    auto_close_seconds —— >0 则到时自动关闭，适合无人值守的定时任务
    """
    try:
        import tkinter as tk
        from tkinter import ttk
    except ImportError:
        log.warning("当前 Python 缺少 tkinter，跳过弹窗")
        return

    def _get(ev: Any, key: str, default: Any = None) -> Any:
        return ev.get(key, default) if isinstance(ev, dict) else getattr(ev, key, default)

    enable_dpi_awareness()
    root = tk.Tk()
    root.title("悉尼约会提案助理")
    root.configure(bg="#ffffff")
    root.attributes("-topmost", True)

    s = apply_scaling(root)
    width = int(460 * s)
    height = int((300 if events else 190) * s)
    screen_w, screen_h = root.winfo_screenwidth(), root.winfo_screenheight()
    # 靠右下角显示，像系统通知那样不挡住屏幕中央
    root.geometry(f"{width}x{height}"
                  f"+{screen_w - width - int(40 * s)}+{screen_h - height - int(90 * s)}")

    header_text = (f"发现 {len(events)} 个新的约会提案" if events else "本次没有达标的推荐")
    header = tk.Frame(root, bg="#c96a4e", height=int(54 * s))
    header.pack(fill="x")
    header.pack_propagate(False)
    tk.Label(header, text=header_text, bg="#c96a4e", fg="white",
             font=("Microsoft YaHei UI", 12, "bold")).pack(side="left", padx=16)

    body = tk.Frame(root, bg="#ffffff")
    body.pack(fill="both", expand=True, padx=16, pady=(12, 8))

    if events:
        for ev in events[:5]:
            row = tk.Frame(body, bg="#ffffff")
            row.pack(fill="x", pady=3)
            tk.Label(row, text=f"{_get(ev, 'total', 0):.1f}", bg="#ffffff", fg="#c96a4e",
                     font=("Microsoft YaHei UI", 11, "bold"), width=5,
                     anchor="w").pack(side="left")
            tk.Label(row, text=_get(ev, "title", ""), bg="#ffffff", fg="#2b2b2b",
                     font=("Microsoft YaHei UI", 10), anchor="w",
                     wraplength=int(350 * s), justify="left").pack(side="left", fill="x")
        if len(events) > 5:
            tk.Label(body, text=f"…… 另有 {len(events) - 5} 个，点「查看详情」看全部",
                     bg="#ffffff", fg="#999999",
                     font=("Microsoft YaHei UI", 9)).pack(anchor="w", pady=(6, 0))
    else:
        tk.Label(body, text=empty_reason or "不降低标准凑数，下次再看。",
                 bg="#ffffff", fg="#666666", font=("Microsoft YaHei UI", 10),
                 wraplength=int(420 * s), justify="left").pack(anchor="w")

    footer = tk.Frame(root, bg="#ffffff")
    footer.pack(fill="x", padx=16, pady=(0, 14))

    def _open_and_close() -> None:
        _launch_gui()
        root.destroy()

    ttk.Button(footer, text="查看详情", command=_open_and_close).pack(side="right")
    ttk.Button(footer, text="关闭", command=root.destroy).pack(side="right", padx=(0, 8))

    if auto_close_seconds > 0:
        root.after(auto_close_seconds * 1000, root.destroy)

    root.mainloop()
