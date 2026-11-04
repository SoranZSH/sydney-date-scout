#!/usr/bin/env python3
"""悉尼约会提案助理 —— 桌面查询程序。

左边选批次（最新一批＝当前推荐，往下是历史）+ 卡片式活动列表，
右边是详情：分数、倒计时、可复制的约会方案、七维度条形图、订票链接，
以及 👍/👎 反馈（可撤销）和「口味画像」面板（Track A/B 学习进度可视化）。

纯 tkinter（标准库），不引入 GUI 框架依赖，方便打包成单文件 exe。
视觉上走 v1.4.0「夜约」暗色主题（见 scout/theme.py）：
刻意不用 ttkbootstrap / CustomTkinter（要引第三方依赖），而是自绘扁平组件
（FlatButton / 渐变头部 / 胶囊 chip / 细滑块滚动条），设计代价换零依赖。
"""
from __future__ import annotations

import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any, Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from scout.config import db_path, load_config
from scout.paths import apply_scaling, default_config_path, enable_dpi_awareness
from scout.store import Store
from scout.theme import (ACCENT, ACCENT_D, ACCENT_SOFT, AMBER, BG, BORDER,
                         CARD, CARD_H, CHIP_BG, CHIP_FG, DANGER, DANGER_BG,
                         FAINT, FONT, GOOD, GOOD_BG, HEADER_BTN, HEADER_BTN_H,
                         LINK, MUTED, PEACH, SOFT, SOFT_H, TEXT, TRACK,
                         band_color, draw_gradient, enable_dark_titlebar)

CAT_ZH = {"film": "电影", "performance": "演出", "exhibition": "展览",
          "festival": "节庆", "market": "市集", "food": "美食", "venue": "场所",
          "experience": "体验", "sport": "运动", "nature": "自然",
          "sale": "折扣", "daytrip": "短途"}
WEEKDAY_ZH = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def _parse_dt(iso: Optional[str]) -> Optional[datetime]:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso)
    except ValueError:
        return None


def countdown_text(start_iso: Optional[str]) -> tuple[str, str]:
    """(文案, 颜色)。倒计时是「现在去订」的轻推——历史批次自然显示已结束。"""
    dt = _parse_dt(start_iso)
    if dt is None:
        return "", MUTED
    days = (dt.date() - datetime.now().date()).days
    if days < 0:
        return "已结束", FAINT
    if days == 0:
        return "就在今天", ACCENT
    if days == 1:
        return "就在明天", ACCENT
    if days <= 3:
        return f"仅剩 {days} 天", AMBER
    return f"{days} 天后", MUTED


def ellipsize(font: tkfont.Font, text: str, max_px: int) -> str:
    if max_px <= 0 or font.measure(text) <= max_px:
        return text
    while text and font.measure(text + "…") > max_px:
        text = text[:-1]
    return text + "…"


class FlatButton(tk.Label):
    """自绘扁平按钮：tk 原生按钮在 Windows 上无法可靠地做深色底+hover，
    这是 Sun-Valley / CustomTkinter 这类项目都绕开 ttk 默认按钮的原因。"""

    PALETTE = {
        "accent":     (ACCENT, "white", ACCENT_D),
        "secondary":  (SOFT, TEXT, SOFT_H),
        "ghost":      (BG, MUTED, SOFT),
        "header":     (HEADER_BTN, TEXT, HEADER_BTN_H),
        "chip":       (CHIP_BG, CHIP_FG, SOFT_H),
        "chip_on":    (ACCENT, "white", ACCENT_D),
        "like_on":    (GOOD_BG, GOOD, "#333d2f"),
        "dislike_on": (DANGER_BG, DANGER, "#44362f"),
    }

    def __init__(self, master, text: str, command: Callable[[], None],
                 kind: str = "secondary", font_size: int = 10, bold: bool = False,
                 padx: int = 14, pady: int = 6, **kw):
        self._command = command
        self._enabled = True
        self._kind = kind
        super().__init__(master, text=text,
                         font=(FONT, font_size, "bold" if bold else "normal"),
                         padx=padx, pady=pady, cursor="hand2", **kw)
        self.set_kind(kind)
        self.bind("<Button-1>", self._on_click)
        self.bind("<Enter>", lambda _e: self._enabled and self.config(bg=self._hover))
        self.bind("<Leave>", lambda _e: self._enabled and self.config(bg=self._bg))

    def set_kind(self, kind: str) -> None:
        self._kind = kind
        self._bg, fg, self._hover = self.PALETTE.get(kind, self.PALETTE["secondary"])
        self.config(bg=self._bg, fg=fg)

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self.config(cursor="hand2" if enabled else "arrow")
        if not enabled:
            self.config(bg=SOFT, fg=FAINT)
        else:
            self.set_kind(self._kind)

    def _on_click(self, _e) -> None:
        if self._enabled:
            self._command()


class ScoutApp(tk.Tk):
    def __init__(self, config_path: str):
        super().__init__()
        self.title("悉尼约会提案助理")
        # 高分屏上按 DPI 放大窗口与字号，否则开了 DPI awareness 后会小到看不清
        self.scale = apply_scaling(self)
        self.geometry(f"{int(1080 * self.scale)}x{int(700 * self.scale)}")
        self.minsize(int(920 * self.scale), int(580 * self.scale))
        self.configure(bg=BG)
        enable_dark_titlebar(self)

        self.config_path = config_path
        self.cfg: Optional[dict] = None
        self.batches: list[Any] = []
        self.batch_idx = 0
        self._batch_labels: list[str] = []
        self.current_events: list[dict] = []   # 当前批次全部活动
        self.visible_idx: list[int] = []       # 过滤后展示的下标
        self.selected_pos = -1                 # 选中项在 visible_idx 中的位置
        self.current_event: Optional[dict] = None
        self._cards: list[tk.Frame] = []
        self._embeds: list[tk.Widget] = []     # 详情里内嵌的 canvas，防泄漏
        self._filter_cat: Optional[str] = None
        self._busy = False
        self._status_text = ""

        self._fonts()
        self._build_ui()
        self._bind_keys()
        self._load_config_and_refresh()

    def _fonts(self) -> None:
        self.f_card_title = tkfont.Font(family=FONT, size=10, weight="bold")
        self.f_card_sub = tkfont.Font(family=FONT, size=9)
        self.f_chip = tkfont.Font(family=FONT, size=9)
        self.f_hdr_title = tkfont.Font(family=FONT, size=14, weight="bold")

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        style = ttk.Style(self)
        with_theme = "clam" if "clam" in style.theme_names() else style.theme_use()
        style.theme_use(with_theme)
        # 细滑块滚动条：去掉上下箭头，只留轨道+滑块，摆脱系统滚动条的年代感
        style.layout("Slim.Vertical.TScrollbar",
                     [("Vertical.Scrollbar.trough",
                       {"children": [("Vertical.Scrollbar.thumb",
                                      {"expand": "1"})],
                        "sticky": "ns"})])
        style.configure("Slim.Vertical.TScrollbar", background=SOFT,
                        troughcolor=BG, bordercolor=BG, relief="flat",
                        lightcolor=SOFT, darkcolor=SOFT,
                        width=int(8 * self.scale))
        style.map("Slim.Vertical.TScrollbar",
                  background=[("active", SOFT_H), ("pressed", SOFT_H)])
        style.configure("Horizontal.TProgressbar", background=ACCENT,
                        troughcolor=SOFT, bordercolor=SOFT,
                        lightcolor=ACCENT, darkcolor=ACCENT)

        s = self.scale

        # ---- 顶栏：玫瑰→紫罗兰渐变 ----
        self.header = tk.Canvas(self, height=int(60 * s), highlightthickness=0, bd=0)
        self.header.pack(fill="x")
        self.refresh_btn = FlatButton(self.header, "立即抓取新活动", self._run_pipeline,
                                      kind="header", bold=True)
        self.profile_btn = FlatButton(self.header, "口味画像", self._show_profile,
                                      kind="header")
        self.progress = ttk.Progressbar(self.header, mode="indeterminate",
                                        length=int(110 * s))
        self.header.bind("<Configure>", lambda _e: self._draw_header())

        # ---- 主体：左右分栏 ----
        main = tk.PanedWindow(self, orient="horizontal", bg=BG, sashwidth=6,
                              bd=0, sashrelief="flat")
        main.pack(fill="both", expand=True, padx=14, pady=12)

        # ======== 左：批次 + 类别 chips + 活动卡片 ========
        left = tk.Frame(main, bg=BG, width=int(330 * s))
        main.add(left, minsize=int(280 * s))

        batch_row = tk.Frame(left, bg=BG)
        batch_row.pack(fill="x")
        self.next_btn = FlatButton(batch_row, "›", lambda: self._step_batch(-1),
                                   kind="ghost", padx=9, pady=4)
        self.next_btn.pack(side="right")
        self.prev_btn = FlatButton(batch_row, "‹", lambda: self._step_batch(1),
                                   kind="ghost", padx=9, pady=4)
        self.prev_btn.pack(side="right", padx=(0, 2))
        self.batch_btn = FlatButton(batch_row, "— ▾", self._open_batch_menu,
                                    kind="secondary", font_size=9, padx=12, pady=5,
                                    anchor="w")
        self.batch_btn.pack(fill="x", padx=(0, 6))

        self.chipbar = tk.Frame(left, bg=BG)
        self.chipbar.pack(fill="x", pady=(8, 6))

        # 卡片列表：Canvas + 内嵌 Frame（标准库里做可滚动卡片列表的唯一姿势）
        list_wrap = tk.Frame(left, bg=BG)
        list_wrap.pack(fill="both", expand=True)
        self.list_canvas = tk.Canvas(list_wrap, bg=BG, highlightthickness=0)
        self.list_canvas.pack(side="left", fill="both", expand=True)
        lsb = ttk.Scrollbar(list_wrap, orient="vertical",
                            style="Slim.Vertical.TScrollbar",
                            command=self.list_canvas.yview)
        lsb.pack(side="right", fill="y", padx=(2, 0))
        self.list_canvas.config(yscrollcommand=lsb.set)
        self.list_inner = tk.Frame(self.list_canvas, bg=BG)
        self._list_win = self.list_canvas.create_window((0, 0), window=self.list_inner,
                                                        anchor="nw")
        self.list_inner.bind(
            "<Configure>",
            lambda _e: self.list_canvas.config(scrollregion=self.list_canvas.bbox("all")))
        self.list_canvas.bind(
            "<Configure>",
            lambda e: self.list_canvas.itemconfigure(self._list_win, width=e.width))
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.list_canvas.bind(seq, self._on_list_scroll)

        # ======== 右：详情 ========
        right = tk.Frame(main, bg=BG)
        main.add(right, minsize=int(500 * s))

        card = tk.Frame(right, bg=CARD, highlightthickness=1,
                        highlightbackground=BORDER)
        card.pack(fill="both", expand=True)

        # 详情头：分数 + 标题 + 信息 chips + 操作栏（固定，不随正文滚动）
        self.head = tk.Frame(card, bg=CARD)
        self.head.pack(fill="x", padx=int(20 * s), pady=(int(16 * s), 0))

        score_row = tk.Frame(self.head, bg=CARD)
        score_row.pack(fill="x")
        self.score_lbl = tk.Label(score_row, text="", bg=CARD, fg=ACCENT,
                                  font=(FONT, 24, "bold"))
        self.score_lbl.pack(side="left")
        tk.Label(score_row, text=" / 100", bg=CARD, fg=FAINT,
                 font=(FONT, 10)).pack(side="left", anchor="s", pady=(0, int(6 * s)))
        self.countdown_lbl = tk.Label(score_row, text="", bg=CARD, font=(FONT, 10, "bold"))
        self.countdown_lbl.pack(side="right", anchor="s", pady=(0, int(6 * s)))

        self.title_lbl = tk.Label(self.head, text="", bg=CARD, fg=TEXT,
                                  font=(FONT, 14, "bold"), anchor="w", justify="left")
        self.title_lbl.pack(fill="x", pady=(2, 6))
        self.title_lbl.bind("<Configure>",
                            lambda e: self.title_lbl.config(wraplength=e.width))

        self.chips_row = tk.Frame(self.head, bg=CARD)
        self.chips_row.pack(fill="x", pady=(0, 10))

        action_row = tk.Frame(self.head, bg=CARD)
        action_row.pack(fill="x", pady=(0, 4))
        self.open_btn = FlatButton(action_row, "打开订票页 ↗", self._open_current_link,
                                   kind="accent", bold=True)
        self.open_btn.pack(side="left")
        self.copy_btn = FlatButton(action_row, "复制约会方案", self._copy_plan,
                                   kind="secondary")
        self.copy_btn.pack(side="left", padx=(8, 0))
        self.dislike_btn = FlatButton(action_row, "👎 不再推这类",
                                      lambda: self._feedback("dislike"), kind="secondary")
        self.dislike_btn.pack(side="right")
        self.like_btn = FlatButton(action_row, "👍 感兴趣",
                                   lambda: self._feedback("like"), kind="secondary")
        self.like_btn.pack(side="right", padx=(0, 8))

        tk.Frame(card, bg=BORDER, height=1).pack(fill="x", padx=int(20 * s),
                                                 pady=(int(10 * s), 0))

        detail_wrap = tk.Frame(card, bg=CARD)
        detail_wrap.pack(fill="both", expand=True)
        self.detail = tk.Text(detail_wrap, bg=CARD, fg=TEXT, font=(FONT, 10),
                              relief="flat", wrap="word", padx=int(20 * s),
                              pady=int(12 * s), cursor="arrow", highlightthickness=0,
                              selectbackground=ACCENT, insertbackground=TEXT)
        self.detail.pack(side="left", fill="both", expand=True)
        dsb = ttk.Scrollbar(detail_wrap, orient="vertical",
                            style="Slim.Vertical.TScrollbar",
                            command=self.detail.yview)
        dsb.pack(side="right", fill="y")
        self.detail.config(yscrollcommand=dsb.set)

        self._configure_text_tags()
        self.detail.config(state="disabled")

    # ---- 渐变头部：标题/状态是 Canvas 文本，按钮用 create_window 吸附右侧 ----
    def _draw_header(self) -> None:
        c = self.header
        w, h = c.winfo_width(), c.winfo_height()
        if w <= 2:
            return
        c.delete("all")
        draw_gradient(c, w, h)
        s = self.scale
        x = int(18 * s)
        c.create_text(x, h // 2, text="悉尼约会提案助理", anchor="w",
                      font=self.f_hdr_title, fill="white")
        x += self.f_hdr_title.measure("悉尼约会提案助理") + int(14 * s)
        self._status_id = c.create_text(x, h // 2 + 1, text=self._status_text,
                                        anchor="w", font=(FONT, 9),
                                        fill="#f3d9dc")
        bx = w - int(16 * s)
        for btn in (self.refresh_btn, self.profile_btn):
            c.create_window(bx, h // 2, anchor="e", window=btn)
            bx -= btn.winfo_reqwidth() + int(10 * s)
        if self._busy:
            c.create_window(bx, h // 2, anchor="e", window=self.progress)

    def _set_status(self, text: str) -> None:
        self._status_text = text
        try:
            self.header.itemconfigure(self._status_id, text=text)
        except (tk.TclError, AttributeError):
            pass

    def _configure_text_tags(self) -> None:
        d = self.detail
        d.tag_configure("body", font=(FONT, 10), foreground=TEXT, spacing3=3)
        d.tag_configure("section", font=(FONT, 10, "bold"), foreground=ACCENT,
                        spacing1=14, spacing3=5)
        d.tag_configure("dim", font=(FONT, 9), foreground=MUTED, spacing3=2)
        d.tag_configure("bullet", font=(FONT, 10), foreground=TEXT,
                        lmargin1=14, lmargin2=58, spacing3=3)
        d.tag_configure("tl_time", font=(FONT, 10, "bold"), foreground=ACCENT)
        d.tag_configure("tl_label", font=(FONT, 10, "bold"), foreground=TEXT)
        d.tag_configure("placeholder", font=(FONT, 11), foreground=MUTED,
                        justify="center", spacing1=40)
        d.tag_configure("placeholder_big", font=(FONT, 28), justify="center",
                        spacing1=60)

    def _bind_keys(self) -> None:
        self.bind("<Up>", lambda _e: self._move_selection(-1))
        self.bind("<Down>", lambda _e: self._move_selection(1))
        self.bind("<Return>", lambda _e: self._open_current_link())
        self.bind("<Control-c>", lambda _e: self._copy_plan())

    # ------------------------------------------------------------- 小组件
    def _toast(self, text: str) -> None:
        """轻提示：代替 messagebox 的打断式确认。暗界面上用亮色托斯特。"""
        try:
            old = getattr(self, "_toast_win", None)
            if old is not None and old.winfo_exists():
                old.destroy()
            tl = tk.Toplevel(self)
            self._toast_win = tl
            tl.overrideredirect(True)
            tl.attributes("-topmost", True)
            lbl = tk.Label(tl, text=text, bg="#ece6da", fg="#2a2530",
                           font=(FONT, 10), padx=18, pady=9)
            lbl.pack()
            tl.update_idletasks()
            x = self.winfo_rootx() + (self.winfo_width() - tl.winfo_reqwidth()) // 2
            y = self.winfo_rooty() + self.winfo_height() - int(90 * self.scale)
            tl.geometry(f"+{x}+{y}")
            tl.after(2200, tl.destroy)
        except tk.TclError:
            pass

    def _chip(self, parent, text: str, fg: str = CHIP_FG, bg: str = CHIP_BG,
              max_px: int = 0) -> tk.Label:
        if max_px:
            text = ellipsize(self.f_chip, text, max_px)
        lbl = tk.Label(parent, text=text, bg=bg, fg=fg, font=self.f_chip,
                       padx=9, pady=3)
        lbl.pack(side="left", padx=(0, 6))
        return lbl

    # ------------------------------------------------------- 数据加载
    def _load_config_and_refresh(self) -> None:
        try:
            self.cfg = load_config(self.config_path)
        except Exception as ex:                            # noqa: BLE001
            messagebox.showerror("配置加载失败", str(ex))
            return
        self._reload_batches()

    def _store(self) -> Store:
        return Store(db_path(self.cfg))

    def _reload_batches(self, select_latest: bool = True) -> None:
        store = self._store()
        try:
            self.batches = store.list_batches()
        finally:
            store.close()

        if not self.batches:
            self._batch_labels = []
            self.batch_btn.config(text="还没有批次 ▾")
            self._build_cards([])
            self._render_empty_state(
                "🌃", "还没有任何推荐记录",
                "点右上角「立即抓取新活动」跑一次，\n达标的活动会出现在这里。")
            self._set_status("暂无数据")
            return

        self._batch_labels = []
        for i, b in enumerate(self.batches):
            when = datetime.fromisoformat(b["created_at"]).strftime("%m-%d %H:%M")
            tag = "当前推荐" if i == 0 else "历史"
            self._batch_labels.append(f"{tag} · {when} · {b['item_count']} 个")
        if select_latest:
            self.batch_idx = 0
        self._on_batch_change()
        self._set_status(f"共 {len(self.batches)} 批推荐记录")

    def _open_batch_menu(self) -> None:
        if not self._batch_labels:
            return
        menu = tk.Menu(self, tearoff=0, bg=CARD, fg=TEXT, bd=0, relief="flat",
                       activebackground=ACCENT, activeforeground="white",
                       font=(FONT, 9))
        for i, label in enumerate(self._batch_labels[:30]):
            menu.add_command(label=("● " if i == self.batch_idx else "   ") + label,
                             command=lambda i=i: self._set_batch(i))
        if len(self._batch_labels) > 30:
            menu.add_separator()
            menu.add_command(label=f"…更早的 {len(self._batch_labels) - 30} 批用 ‹ 翻页",
                             state="disabled")
        menu.tk_popup(self.batch_btn.winfo_rootx(),
                      self.batch_btn.winfo_rooty() + self.batch_btn.winfo_height())

    def _set_batch(self, idx: int) -> None:
        self.batch_idx = idx
        self._on_batch_change()

    def _step_batch(self, delta: int) -> None:
        idx = self.batch_idx + delta
        if 0 <= idx < len(self.batches):
            self.batch_idx = idx
            self._on_batch_change()

    def _on_batch_change(self) -> None:
        idx = self.batch_idx
        if idx < 0 or idx >= len(self.batches):
            return
        self.batch_btn.config(text=f"{self._batch_labels[idx]}  ▾")
        batch = self.batches[idx]
        store = self._store()
        try:
            self.current_events = store.batch_events(int(batch["id"]))
            self._verdicts = {i: store.feedback_for(ev.get("title", ""),
                                                    ev.get("venue_name", ""))
                              for i, ev in enumerate(self.current_events)}
        finally:
            store.close()

        # 类别 chips 随批次更新
        cats = sorted({ev.get("category", "") for ev in self.current_events} - {""})
        self._cat_codes = cats
        self._filter_cat = None
        self._build_filter_chips()

        self._apply_filter(select_first=True)

    # ------------------------------------------------------- 类别 chips
    def _build_filter_chips(self) -> None:
        for w in self.chipbar.winfo_children():
            w.destroy()
        s = self.scale
        max_w = max(int(260 * s), self.chipbar.winfo_width() or int(300 * s))
        row = tk.Frame(self.chipbar, bg=BG)
        row.pack(fill="x", anchor="w")
        used = 0
        chips = [(None, "全部")] + [(c, CAT_ZH.get(c, c)) for c in self._cat_codes]
        self._chip_btns = {}
        for code, label in chips:
            btn = FlatButton(row, label, lambda c=code: self._set_filter(c),
                             kind="chip_on" if code == self._filter_cat else "chip",
                             font_size=9, padx=11, pady=3)
            need = btn.winfo_reqwidth() + int(6 * s)
            if used + need > max_w and used > 0:      # 简易流式换行
                row = tk.Frame(self.chipbar, bg=BG)
                row.pack(fill="x", anchor="w", pady=(int(5 * s), 0))
                used = 0
            btn.pack(side="left", padx=(0, int(6 * s)))
            used += need
            self._chip_btns[code] = btn

    def _set_filter(self, cat: Optional[str]) -> None:
        self._filter_cat = cat
        for code, btn in self._chip_btns.items():
            btn.set_kind("chip_on" if code == cat else "chip")
        self._apply_filter(select_first=True)

    def _apply_filter(self, select_first: bool = False) -> None:
        self.visible_idx = [i for i, ev in enumerate(self.current_events)
                            if not self._filter_cat
                            or ev.get("category") == self._filter_cat]
        self._build_cards(self.visible_idx)
        if self.visible_idx and select_first:
            self._select_pos(0)
        elif not self.visible_idx:
            self.current_event = None
            self._render_empty_state("🪹", "这个批次没有符合条件的活动", "")

    # ------------------------------------------------------- 卡片列表
    def _build_cards(self, indices: list[int]) -> None:
        for w in self.list_inner.winfo_children():
            w.destroy()
        self._cards = []
        s = self.scale

        for pos, i in enumerate(indices):
            ev = self.current_events[i]
            total = float(ev.get("total", 0) or 0)
            card = tk.Frame(self.list_inner, bg=CARD, highlightthickness=1,
                            highlightbackground=BORDER, cursor="hand2")
            card.pack(fill="x", pady=(0, int(6 * s)), padx=(0, int(4 * s)))

            inner = tk.Frame(card, bg=CARD)
            inner.pack(fill="x", padx=int(10 * s), pady=int(8 * s))

            badge = tk.Label(inner, text=f"{total:.1f}", bg=CARD,
                             fg=band_color(total), font=(FONT, 12, "bold"), width=4)
            badge.pack(side="left", padx=(0, int(6 * s)))

            mid = tk.Frame(inner, bg=CARD)
            mid.pack(side="left", fill="x", expand=True)
            title_lbl = tk.Label(mid, text=ev.get("title", ""), bg=CARD, fg=TEXT,
                                 font=self.f_card_title, anchor="w")
            title_lbl.pack(fill="x")
            title_lbl.bind("<Configure>",
                           lambda e, l=title_lbl, t=ev.get("title", ""):
                           l.config(text=ellipsize(self.f_card_title, t, e.width)))

            dt = _parse_dt(ev.get("start"))
            when = (f"{dt.strftime('%m-%d')} {WEEKDAY_ZH[dt.weekday()]}"
                    if dt else "时间待定")
            cat = CAT_ZH.get(ev.get("category", ""), "其他")
            price = ev.get("price_per_person")
            price_txt = "免费" if not price else f"A${price:.0f}"
            where = ev.get("suburb") or ev.get("venue_name") or ""
            sub_txt = " · ".join(x for x in (when, cat, where, price_txt) if x)
            sub_lbl = tk.Label(mid, text=sub_txt, bg=CARD, fg=MUTED,
                               font=self.f_card_sub, anchor="w")
            sub_lbl.pack(fill="x")
            sub_lbl.bind("<Configure>",
                         lambda e, l=sub_lbl, t=sub_txt:
                         l.config(text=ellipsize(self.f_card_sub, t, e.width)))

            side = tk.Frame(inner, bg=CARD)
            side.pack(side="right", padx=(int(4 * s), 0))
            cd_text, cd_color = countdown_text(ev.get("start"))
            if cd_text:
                tk.Label(side, text=cd_text, bg=CARD, fg=cd_color,
                         font=(FONT, 9)).pack(anchor="e")
            verdict = self._verdicts.get(i)
            if verdict:
                tk.Label(side, text="👍" if verdict == "like" else "👎", bg=CARD,
                         fg=GOOD if verdict == "like" else DANGER,
                         font=(FONT, 9)).pack(anchor="e")

            widgets = [card, inner, badge, mid, title_lbl, sub_lbl, side] \
                + list(side.winfo_children())
            for w in widgets:
                w.bind("<Button-1>", lambda _e, p=pos: self._select_pos(p))
                w.bind("<Double-Button-1>", lambda _e: self._open_current_link())
                for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                    w.bind(seq, self._on_list_scroll)
                w.bind("<Enter>", lambda _e, p=pos: self._hover_card(p, True))
                w.bind("<Leave>", lambda _e, p=pos: self._hover_card(p, False))
            self._cards.append(card)

    def _paint_card(self, pos: int, bg: str, border: str) -> None:
        if not (0 <= pos < len(self._cards)):
            return
        card = self._cards[pos]
        card.config(bg=bg, highlightbackground=border)
        stack = list(card.winfo_children())
        while stack:
            w = stack.pop()
            stack.extend(w.winfo_children())
            if isinstance(w, (tk.Frame, tk.Label)):
                w.config(bg=bg)

    def _hover_card(self, pos: int, entering: bool) -> None:
        if pos == self.selected_pos:
            return
        self._paint_card(pos, CARD_H if entering else CARD, BORDER)

    def _select_pos(self, pos: int) -> None:
        if not (0 <= pos < len(self.visible_idx)):
            return
        old = self.selected_pos
        self.selected_pos = pos
        if 0 <= old < len(self._cards):
            self._paint_card(old, CARD, BORDER)
        self._paint_card(pos, ACCENT_SOFT, ACCENT)
        self.focus_set()                      # 让 ↑↓ 键生效

        self.current_event = self.current_events[self.visible_idx[pos]]
        self._render_event(self.current_event)
        self._refresh_feedback_ui()
        self._log_behavior("view", self.current_event)

        # 保证选中卡片在可视区内
        try:
            card = self._cards[pos]
            self.list_canvas.update_idletasks()
            top = card.winfo_y() / max(1, self.list_inner.winfo_height())
            bot = (card.winfo_y() + card.winfo_height()) \
                / max(1, self.list_inner.winfo_height())
            lo, hi = self.list_canvas.yview()
            if top < lo:
                self.list_canvas.yview_moveto(top)
            elif bot > hi:
                self.list_canvas.yview_moveto(bot - (hi - lo))
        except tk.TclError:
            pass

    def _move_selection(self, delta: int) -> None:
        if self.visible_idx:
            self._select_pos(max(0, min(len(self.visible_idx) - 1,
                                        self.selected_pos + delta)))

    def _on_list_scroll(self, e) -> None:
        if getattr(e, "num", None) == 4:
            self.list_canvas.yview_scroll(-2, "units")
        elif getattr(e, "num", None) == 5:
            self.list_canvas.yview_scroll(2, "units")
        else:
            self.list_canvas.yview_scroll(-int(e.delta / 40), "units")

    # ------------------------------------------------------- 行为日志（v1.3.0）
    def _log_behavior(self, kind: str, ev: dict) -> None:
        """查看/点链接/复制方案都是个性化（Track B）的学习信号，静默记录。"""
        try:
            store = self._store()
            try:
                store.log_behavior(kind, ev.get("title", ""), ev.get("venue_name", ""),
                                   ev.get("uid"), ev.get("category"))
            finally:
                store.close()
        except Exception:                                  # noqa: BLE001
            pass

    def _open_current_link(self) -> None:
        ev = self.current_event
        if not ev:
            return
        url = ev.get("booking_url") or ev.get("source_url")
        if url:
            webbrowser.open(url)
            self._log_behavior("open_link", ev)

    # ------------------------------------------------------- 复制约会方案
    def _copy_plan(self) -> None:
        ev = self.current_event
        if not ev:
            return
        lines = [f"【约会提案】{ev.get('title', '')}"]
        dt = _parse_dt(ev.get("start"))
        if dt:
            lines.append(f"时间：{dt.strftime('%m月%d日')} "
                         f"{WEEKDAY_ZH[dt.weekday()]} {dt.strftime('%H:%M')}")
        where = ev.get("venue_name") or ""
        if ev.get("suburb"):
            where = f"{where}（{ev['suburb']}）" if where else ev["suburb"]
        if where:
            lines.append(f"地点：{where}")
        price = ev.get("price_per_person")
        lines.append(f"票价：{'免费' if not price else f'A${price:.0f}/人'}")
        plan = ev.get("plan") or {}
        if plan.get("timeline"):
            lines.append("安排：")
            for st in plan["timeline"]:
                lines.append(f"  {st.get('time', '')} {st.get('label', '')}"
                             f" — {st.get('detail', '')}")
        budget = plan.get("budget") or {}
        if budget.get("per_person_total_aud") is not None:
            lines.append(f"预算：每人约 A${budget['per_person_total_aud']}")
        if (plan.get("alternates") or {}).get("rainy_plan"):
            lines.append(f"下雨备选：{plan['alternates']['rainy_plan']}")
        url = ev.get("booking_url") or ev.get("source_url")
        if url:
            lines.append(url)
        try:
            self.clipboard_clear()
            self.clipboard_append("\n".join(lines))
        except tk.TclError:
            return
        self._toast("已复制约会方案，直接粘贴发出去吧")
        self._log_behavior("copy_plan", ev)

    # ------------------------------------------------------- 反馈（v1.2.0）
    def _refresh_feedback_ui(self) -> None:
        ev = self.current_event
        verdict = None
        if ev:
            store = self._store()
            try:
                verdict = store.feedback_for(ev.get("title", ""),
                                             ev.get("venue_name", ""))
            finally:
                store.close()
        self.like_btn.set_kind("like_on" if verdict == "like" else "secondary")
        self.dislike_btn.set_kind("dislike_on" if verdict == "dislike" else "secondary")
        if ev is not None and self.selected_pos >= 0 \
                and self.selected_pos < len(self.visible_idx):
            self._verdicts[self.visible_idx[self.selected_pos]] = verdict

    def _feedback(self, verdict: str) -> None:
        ev = self.current_event
        if not ev:
            return
        store = self._store()
        try:
            current = store.feedback_for(ev.get("title", ""), ev.get("venue_name", ""))
            if current == verdict:                      # 再点一次＝撤销
                store.clear_feedback(ev.get("title", ""), ev.get("venue_name", ""))
                self._toast("已撤销标记")
            else:
                store.add_feedback(ev.get("title", ""), ev.get("venue_name", ""),
                                   verdict, ev.get("uid"), ev.get("category"))
                self._toast("已标记感兴趣，会推更多这类活动" if verdict == "like"
                            else "这类不再推送（再点一次可撤销）")
        finally:
            store.close()
        self._refresh_feedback_ui()

    # ------------------------------------------------------- 口味画像
    def _show_profile(self) -> None:
        """把 Track A/B 的学习状态摊开给用户看：看得见的成长才有标注的动力。"""
        if not self.cfg:
            return
        from scout.personalize import category_affinity

        store = self._store()
        try:
            affinity = category_affinity(store)
            n_labels = len(store.training_samples())
        finally:
            store.close()

        s = self.scale
        win = tk.Toplevel(self)
        win.title("口味画像")
        win.configure(bg=BG)
        w, h = int(430 * s), int(520 * s)
        x = self.winfo_rootx() + (self.winfo_width() - w) // 2
        y = self.winfo_rooty() + (self.winfo_height() - h) // 2
        win.geometry(f"{w}x{h}+{max(0, x)}+{max(0, y)}")
        win.transient(self)
        enable_dark_titlebar(win)

        tk.Label(win, text="口味画像", bg=BG, fg=TEXT,
                 font=(FONT, 13, "bold")).pack(anchor="w", padx=20, pady=(16, 2))
        tk.Label(win, text="由你的查看 / 点链接 / 👍👎 自动累积，只影响推荐排序",
                 bg=BG, fg=MUTED, font=(FONT, 9)).pack(anchor="w", padx=20)

        body = tk.Frame(win, bg=CARD, highlightthickness=1,
                        highlightbackground=BORDER)
        body.pack(fill="both", expand=True, padx=16, pady=12)

        items = sorted(affinity.items(), key=lambda kv: -kv[1])
        if not items:
            tk.Label(body, text="还没有足够的行为数据。\n多看看、多点点 👍👎，"
                     "画像会慢慢成形。", bg=CARD, fg=MUTED, font=(FONT, 10),
                     justify="center").pack(expand=True)
        else:
            rh = int(30 * s)
            c = tk.Canvas(body, bg=CARD, highlightthickness=0,
                          height=rh * len(items) + int(8 * s))
            c.pack(fill="x", padx=int(12 * s), pady=int(10 * s))
            mid_x, half = int(195 * s), int(110 * s)
            f = tkfont.Font(family=FONT, size=9)
            fb = tkfont.Font(family=FONT, size=9, weight="bold")
            c.create_line(mid_x, 4, mid_x, rh * len(items), fill=BORDER)
            for i, (cat, aff) in enumerate(items):
                y = i * rh + rh // 2 + 4
                c.create_text(mid_x - half - 10, y, text=CAT_ZH.get(cat, cat),
                              anchor="e", font=f, fill=TEXT)
                bw = int(abs(aff) * half)
                if aff >= 0:
                    c.create_rectangle(mid_x, y - 5, mid_x + max(bw, 2), y + 5,
                                       fill=GOOD, outline="")
                    c.create_text(mid_x + bw + 8, y, text=f"+{aff:.2f}",
                                  anchor="w", font=fb, fill=GOOD)
                else:
                    c.create_rectangle(mid_x - max(bw, 2), y - 5, mid_x, y + 5,
                                       fill=DANGER, outline="")
                    c.create_text(mid_x - bw - 8, y, text=f"{aff:.2f}",
                                  anchor="e", font=fb, fill=DANGER)

        prog = tk.Frame(win, bg=BG)
        prog.pack(fill="x", padx=20, pady=(0, 14))
        target = 30
        model_personal = Path(self.cfg["_data_dir"]) / "data" / "datefit-model.json"
        if model_personal.exists():
            msg = "内容判别模型已生成，正在参与筛选"
        elif n_labels >= target:
            msg = f"已标注 {n_labels} 条 — 可以训练了：python run.py train-datefit"
        else:
            msg = f"👍/👎 已标注 {n_labels}/{target} 条 — 攒够即可训练你的内容判别模型"
        tk.Label(prog, text=msg, bg=BG, fg=TEXT, font=(FONT, 9)).pack(anchor="w")
        bar = ttk.Progressbar(prog, maximum=target, value=min(n_labels, target),
                              length=int(390 * s))
        bar.pack(fill="x", pady=(5, 0))

    # ------------------------------------------------------- 详情渲染
    def _clear_detail(self) -> None:
        for w in self._embeds:
            try:
                w.destroy()
            except tk.TclError:
                pass
        self._embeds = []
        self.detail.config(state="normal")
        self.detail.delete("1.0", "end")

    def _render_empty_state(self, icon: str, line1: str, line2: str) -> None:
        self.score_lbl.config(text="--", fg=MUTED)
        self.countdown_lbl.config(text="")
        self.title_lbl.config(text=line1)
        for w in self.chips_row.winfo_children():
            w.destroy()
        self._clear_detail()
        self.detail.insert("end", f"{icon}\n", "placeholder_big")
        self.detail.insert("end", f"{line1}\n{line2}", "placeholder")
        self.detail.config(state="disabled")

    def _dims_canvas(self, dims: list[tuple[str, dict]], width: int) -> tk.Canvas:
        """七维度条形图：比一排数字可读得多——这是把文本墙变成图形的关键一步。"""
        s = self.scale
        rh = int(26 * s)
        lw = int(66 * s)
        bar_x, bar_w = lw + int(10 * s), int(110 * s)
        val_x = bar_x + bar_w + int(8 * s)
        reason_x = val_x + int(62 * s)
        c = tk.Canvas(self.detail, width=width, bg=CARD, highlightthickness=0,
                      height=rh * len(dims))
        f = tkfont.Font(family=FONT, size=9)
        fb = tkfont.Font(family=FONT, size=9, weight="bold")
        for i, (_key, sd) in enumerate(dims):
            y = i * rh + rh // 2
            score = float(sd.get("score", 0) or 0)
            color = GOOD if score >= 8 else (PEACH if score >= 5 else "#5a5664")
            c.create_text(lw, y, text=sd.get("label", ""), anchor="e", font=f,
                          fill=TEXT)
            c.create_rectangle(bar_x, y - int(4 * s), bar_x + bar_w, y + int(4 * s),
                               fill=TRACK, outline="")
            if score > 0:
                c.create_rectangle(bar_x, y - int(4 * s),
                                   bar_x + int(bar_w * min(score, 10) / 10),
                                   y + int(4 * s), fill=color, outline="")
            c.create_text(val_x, y, text=f"{score:g}", anchor="w", font=fb, fill=color)
            c.create_text(val_x + int(22 * s), y, text=f"×{sd.get('weight', 0)}",
                          anchor="w", font=f, fill=FAINT)
            reason = ellipsize(f, sd.get("reason", ""), width - reason_x - int(6 * s))
            c.create_text(reason_x, y, text=reason, anchor="w", font=f, fill=MUTED)
        return c

    def _render_event(self, ev: dict) -> None:
        s = self.scale
        total = float(ev.get("total", 0) or 0)
        self.score_lbl.config(text=f"{total:.1f}", fg=band_color(total))
        cd_text, cd_color = countdown_text(ev.get("start"))
        self.countdown_lbl.config(text=cd_text, fg=cd_color)
        self.title_lbl.config(text=ev.get("title", ""))

        # ---- 信息 chips ----
        for w in self.chips_row.winfo_children():
            w.destroy()
        dt = _parse_dt(ev.get("start"))
        if dt:
            self._chip(self.chips_row,
                       f"🕒 {dt.strftime('%m-%d')} {WEEKDAY_ZH[dt.weekday()]} "
                       f"{dt.strftime('%H:%M')}")
        where = ev.get("venue_name") or ""
        if ev.get("suburb"):
            where = f"{where} · {ev['suburb']}" if where else ev["suburb"]
        if where:
            self._chip(self.chips_row, f"📍 {where}", max_px=int(230 * s))
        price = ev.get("price_per_person")
        if not price:
            self._chip(self.chips_row, "免费", fg=GOOD, bg=GOOD_BG)
        else:
            self._chip(self.chips_row, f"A${price:.0f}/人")
        if ev.get("duration_hours"):
            self._chip(self.chips_row, f"{ev['duration_hours']:g} 小时")

        # ---- 正文 ----
        self._clear_detail()
        d = self.detail

        if ev.get("description"):
            d.insert("end", f"{ev['description']}\n", "body")

        plan = ev.get("plan") or {}
        if plan.get("reason"):
            d.insert("end", "推荐理由\n", "section")
            d.insert("end", f"{plan['reason']}\n", "body")

        if ev.get("audience"):
            d.insert("end", "适合对象\n", "section")
            d.insert("end", "、".join(ev["audience"]) + "\n", "body")

        scores = ev.get("scores") or {}
        dims = [(k, v) for k, v in scores.items() if not k.startswith("_")]
        if dims:
            d.insert("end", "各项得分\n", "section")
            width = max(int(430 * s),
                        self.detail.winfo_width() - int(52 * s))
            canvas = self._dims_canvas(dims, width)
            self._embeds.append(canvas)
            d.window_create("end", window=canvas)
            d.insert("end", "\n")
            extras = []
            if "_modifier" in scores:
                zh = {"solo_oriented": "单人向", "community_venue": "社区场地"}
                extras.append("降权：" + "、".join(
                    f"{zh.get(k, k)} ×{v}" for k, v in scores["_modifier"].items()))
            if "_datefit" in scores:
                extras.append(f"内容判别模型 P(有价值) = {scores['_datefit']}")
            if extras:
                d.insert("end", "　".join(extras) + "\n", "dim")

        if plan.get("timeline"):
            d.insert("end", "约会方案\n", "section")
            for step in plan["timeline"]:
                d.insert("end", f"　{step.get('time', ''):>5}  ", "tl_time")
                d.insert("end", f"{step.get('label', '')}", "tl_label")
                detail = step.get("detail", "")
                d.insert("end", f" — {detail}\n" if detail else "\n", "bullet")

        budget = plan.get("budget") or {}
        if budget:
            d.insert("end", "预算\n", "section")
            d.insert("end",
                     f"前置 A${budget.get('pre_aud', 0)} + 主活动 A${budget.get('main_aud', 0)}"
                     f" + 续摊 A${budget.get('post_aud', 0)} + 交通 A${budget.get('transit_aud', 0)}"
                     f"　＝　每人 A${budget.get('per_person_total_aud', 0)}"
                     f"（两人 A${budget.get('couple_total_aud', 0)}）\n", "body")

        alts = plan.get("alternates") or {}
        if alts.get("rainy_plan"):
            d.insert("end", "下雨备选\n", "section")
            d.insert("end", f"{alts['rainy_plan']}\n", "body")

        if plan.get("topics"):
            d.insert("end", "聊天话题\n", "section")
            for t in plan["topics"]:
                d.insert("end", f"• {t}\n", "bullet")

        if plan.get("narrative"):
            d.insert("end", "文案\n", "section")
            d.insert("end", f"{plan['narrative']}\n", "body")

        url = ev.get("booking_url") or ev.get("source_url")
        if url:
            d.insert("end", "订票 / 详情\n", "section")
            start_idx = d.index("end-1c")
            d.insert("end", f"{url}\n")
            end_idx = d.index("end-1c")
            tag = f"link_{abs(hash(url))}"
            d.tag_add(tag, start_idx, end_idx)
            d.tag_configure(tag, foreground=LINK, underline=True,
                            font=(FONT, 9))

            def _click_link(_e, u=url, event=ev):
                webbrowser.open(u)
                self._log_behavior("open_link", event)
            d.tag_bind(tag, "<Button-1>", _click_link)
            d.tag_bind(tag, "<Enter>", lambda _e: d.config(cursor="hand2"))
            d.tag_bind(tag, "<Leave>", lambda _e: d.config(cursor="arrow"))

        if ev.get("evidence"):
            d.insert("end", f"\n来源：{'、'.join(ev['evidence'])}\n", "dim")

        d.config(state="disabled")
        d.yview_moveto(0)

    # ------------------------------------------------------- 手动抓取
    def _run_pipeline(self) -> None:
        """在后台线程跑管线，避免界面卡死。"""
        self.refresh_btn.set_enabled(False)
        self.refresh_btn.config(text="抓取中…")
        self._busy = True
        self._set_status("正在抓取，约需几分钟")
        self._draw_header()
        self.progress.start(12)

        def work() -> None:
            try:
                from scout.pipeline import run_once
                # GUI 里手动触发时不再弹窗（人就在界面前），结果直接刷新到列表
                cfg = dict(self.cfg)
                cfg["notify"] = {**(cfg.get("notify") or {}), "enabled": False}
                stats = run_once(cfg)
                self.after(0, lambda: self._on_pipeline_done(stats, None))
            except Exception as ex:                        # noqa: BLE001
                self.after(0, lambda: self._on_pipeline_done(None, ex))

        threading.Thread(target=work, daemon=True).start()

    def _on_pipeline_done(self, stats: Optional[dict], error: Optional[Exception]) -> None:
        self.progress.stop()
        self._busy = False
        self.refresh_btn.set_enabled(True)
        self.refresh_btn.config(text="立即抓取新活动")
        self._draw_header()
        if error is not None:
            self._set_status("抓取失败")
            messagebox.showerror("抓取失败", f"{type(error).__name__}: {error}")
            return

        self._reload_batches()
        n = stats.get("recommended", 0)
        if n:
            self._set_status(f"本次新增 {n} 个推荐")
            self._toast(f"发现 {n} 个新的约会提案")
        else:
            self._set_status("本次没有达标的活动")
            self._toast(f"核验通过 {stats.get('verified_passed', 0)} 个，"
                        "但没有达到推荐线的——不凑数，下次再看")

        if stats.get("errors"):
            messagebox.showwarning("采集告警", "\n".join(stats["errors"][:8]))


def main() -> int:
    enable_dpi_awareness()
    app = ScoutApp(str(default_config_path()))
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
