"""桌面弹窗通知。

只用标准库 tkinter：不引入额外依赖，打包进 exe 也不会出问题。
弹窗列出本次新推荐的活动，点「查看详情」直接拉起 GUI 主程序。
视觉与主界面共用 scout/theme.py 的「夜约」暗色主题（渐变头部 + 分数色带）。
"""
from __future__ import annotations

import logging
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .paths import apply_scaling, enable_dpi_awareness, is_frozen
from .theme import (ACCENT, ACCENT_D, BG, CARD, FONT, MUTED, SOFT, SOFT_H,
                    TEXT, band_color, draw_gradient, enable_dark_titlebar)

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
    except ImportError:
        log.warning("当前 Python 缺少 tkinter，跳过弹窗")
        return

    def _get(ev: Any, key: str, default: Any = None) -> Any:
        return ev.get(key, default) if isinstance(ev, dict) else getattr(ev, key, default)

    def _when(ev: Any) -> str:
        start = _get(ev, "start")
        if isinstance(start, datetime):
            dt = start
        else:
            try:
                dt = datetime.fromisoformat(start) if start else None
            except (ValueError, TypeError):
                dt = None
        if dt is None:
            return ""
        days = (dt.date() - datetime.now().date()).days
        tail = {0: " · 就在今天", 1: " · 就在明天"}.get(days, "")
        return dt.strftime("%m-%d %H:%M") + tail

    enable_dpi_awareness()
    root = tk.Tk()
    root.title("悉尼约会提案助理")
    root.configure(bg=BG)
    root.attributes("-topmost", True)
    enable_dark_titlebar(root)

    s = apply_scaling(root)
    row_h = 44
    height = int((136 + row_h * min(len(events), 5)
                  + (26 if len(events) > 5 else 0)) * s) if events else int(190 * s)
    width = int(480 * s)
    screen_w, screen_h = root.winfo_screenwidth(), root.winfo_screenheight()
    # 靠右下角显示，像系统通知那样不挡住屏幕中央
    root.geometry(f"{width}x{height}"
                  f"+{screen_w - width - int(40 * s)}+{screen_h - height - int(90 * s)}")

    header_text = (f"发现 {len(events)} 个新的约会提案" if events else "本次没有达标的推荐")
    header = tk.Canvas(root, height=int(54 * s), highlightthickness=0, bd=0)
    header.pack(fill="x")

    def _paint_header(_e=None) -> None:
        w, h = header.winfo_width(), header.winfo_height()
        if w <= 2:
            return
        header.delete("all")
        draw_gradient(header, w, h)
        header.create_text(16, h // 2, text=header_text, anchor="w",
                           font=(FONT, 12, "bold"), fill="white")
    header.bind("<Configure>", _paint_header)

    body = tk.Frame(root, bg=BG)
    body.pack(fill="both", expand=True, padx=16, pady=(12, 8))

    if events:
        for ev in events[:5]:
            total = float(_get(ev, "total", 0) or 0)
            row = tk.Frame(body, bg=BG)
            row.pack(fill="x", pady=int(3 * s))
            tk.Label(row, text=f"{total:.1f}", bg=BG, fg=band_color(total),
                     font=(FONT, 11, "bold"), width=5,
                     anchor="w").pack(side="left")
            col = tk.Frame(row, bg=BG)
            col.pack(side="left", fill="x", expand=True)
            tk.Label(col, text=_get(ev, "title", ""), bg=BG, fg=TEXT,
                     font=(FONT, 10), anchor="w",
                     wraplength=int(360 * s), justify="left").pack(anchor="w")
            sub = " · ".join(x for x in (_when(ev), _get(ev, "venue_name", "")) if x)
            if sub:
                tk.Label(col, text=sub, bg=BG, fg=MUTED,
                         font=(FONT, 8), anchor="w").pack(anchor="w")
        if len(events) > 5:
            tk.Label(body, text=f"…… 另有 {len(events) - 5} 个，点「查看详情」看全部",
                     bg=BG, fg=MUTED,
                     font=(FONT, 9)).pack(anchor="w", pady=(6, 0))
    else:
        tk.Label(body, text=empty_reason or "不降低标准凑数，下次再看。",
                 bg=BG, fg=MUTED, font=(FONT, 10),
                 wraplength=int(440 * s), justify="left").pack(anchor="w")

    footer = tk.Frame(root, bg=BG)
    footer.pack(fill="x", padx=16, pady=(0, 14))

    def _open_and_close() -> None:
        _launch_gui()
        root.destroy()

    def _flat(master, text, command, bg, fg, hover):
        lbl = tk.Label(master, text=text, bg=bg, fg=fg, font=(FONT, 10),
                       padx=14, pady=5, cursor="hand2")
        lbl.bind("<Button-1>", lambda _e: command())
        lbl.bind("<Enter>", lambda _e: lbl.config(bg=hover))
        lbl.bind("<Leave>", lambda _e: lbl.config(bg=bg))
        return lbl

    _flat(footer, "查看详情", _open_and_close,
          ACCENT, "white", ACCENT_D).pack(side="right")
    _flat(footer, "关闭", root.destroy,
          SOFT, TEXT, SOFT_H).pack(side="right", padx=(0, 8))

    if auto_close_seconds > 0:
        root.after(auto_close_seconds * 1000, root.destroy)

    root.mainloop()
