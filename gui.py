#!/usr/bin/env python3
"""悉尼约会提案助理 —— 桌面查询程序。

左边选批次（最新一批＝当前推荐，往下是历史），右边看该批次里每个活动的
完整信息：摘要、七维度得分、推荐理由、适合对象、预算、时间地点、订票链接、
可直接执行的约会方案。

纯 tkinter（标准库），不引入 GUI 框架依赖，方便打包成单文件 exe。
"""
from __future__ import annotations

import sys
import threading
import tkinter as tk
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from scout.config import db_path, load_config
from scout.paths import apply_scaling, default_config_path, enable_dpi_awareness
from scout.store import Store

BG = "#faf7f4"
CARD = "#ffffff"
ACCENT = "#c96a4e"
TEXT = "#2b2b2b"
MUTED = "#8a8a8a"

FONT = "Microsoft YaHei UI"

CAT_ZH = {"film": "电影", "performance": "演出", "exhibition": "展览",
          "festival": "节庆", "market": "市集", "food": "美食", "venue": "场所",
          "experience": "体验", "sport": "运动", "nature": "自然",
          "sale": "折扣", "daytrip": "短途"}


class ScoutApp(tk.Tk):
    def __init__(self, config_path: str):
        super().__init__()
        self.title("悉尼约会提案助理")
        # 高分屏上按 DPI 放大窗口与字号，否则开了 DPI awareness 后会小到看不清
        self.scale = apply_scaling(self)
        self.geometry(f"{int(1020 * self.scale)}x{int(680 * self.scale)}")
        self.minsize(int(880 * self.scale), int(560 * self.scale))
        self.configure(bg=BG)

        self.config_path = config_path
        self.cfg: Optional[dict] = None
        self.batches: list[Any] = []
        self.current_events: list[dict] = []

        self._build_ui()
        self._load_config_and_refresh()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        style = ttk.Style(self)
        with_theme = "clam" if "clam" in style.theme_names() else style.theme_use()
        style.theme_use(with_theme)
        style.configure("TButton", font=(FONT, 9))
        style.configure("Accent.TButton", foreground="white", background=ACCENT)

        s = self.scale

        # ---- 顶栏 ----
        top = tk.Frame(self, bg=ACCENT, height=int(56 * s))
        top.pack(fill="x")
        top.pack_propagate(False)
        tk.Label(top, text="悉尼约会提案助理", bg=ACCENT, fg="white",
                 font=(FONT, 14, "bold")).pack(side="left", padx=18)
        self.status_label = tk.Label(top, text="", bg=ACCENT, fg="#ffe4d9", font=(FONT, 9))
        self.status_label.pack(side="left", padx=8)

        self.refresh_btn = ttk.Button(top, text="立即抓取新活动", command=self._run_pipeline)
        self.refresh_btn.pack(side="right", padx=14)

        # ---- 主体：左右分栏 ----
        main = tk.PanedWindow(self, orient="horizontal", bg=BG, sashwidth=6,
                              bd=0, sashrelief="flat")
        main.pack(fill="both", expand=True, padx=12, pady=12)

        # 左：批次 + 活动列表
        left = tk.Frame(main, bg=BG, width=int(300 * s))
        main.add(left, minsize=int(240 * s))

        tk.Label(left, text="推荐批次", bg=BG, fg=MUTED,
                 font=(FONT, 9)).pack(anchor="w", pady=(0, 4))
        self.batch_combo = ttk.Combobox(left, state="readonly", font=(FONT, 9))
        self.batch_combo.pack(fill="x")
        self.batch_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_batch_change())

        tk.Label(left, text="活动", bg=BG, fg=MUTED,
                 font=(FONT, 9)).pack(anchor="w", pady=(12, 4))

        list_wrap = tk.Frame(left, bg=CARD, highlightthickness=1,
                             highlightbackground="#eee2da")
        list_wrap.pack(fill="both", expand=True)
        self.event_list = tk.Listbox(list_wrap, bg=CARD, fg=TEXT, font=(FONT, 10),
                                     relief="flat", highlightthickness=0,
                                     selectbackground=ACCENT, selectforeground="white",
                                     activestyle="none")
        self.event_list.pack(side="left", fill="both", expand=True, padx=4, pady=4)
        sb = ttk.Scrollbar(list_wrap, orient="vertical", command=self.event_list.yview)
        sb.pack(side="right", fill="y")
        self.event_list.config(yscrollcommand=sb.set)
        self.event_list.bind("<<ListboxSelect>>", lambda _e: self._on_event_select())
        self.event_list.bind("<Double-Button-1>", lambda _e: self._open_current_link())

        # 右：详情
        right = tk.Frame(main, bg=BG)
        main.add(right, minsize=int(460 * s))

        detail_wrap = tk.Frame(right, bg=CARD, highlightthickness=1,
                               highlightbackground="#eee2da")
        detail_wrap.pack(fill="both", expand=True)

        # ---- 反馈栏（v1.2.0）：没有反馈的推荐系统只是定时广播 ----
        fb = tk.Frame(right, bg=BG)
        fb.pack(fill="x", pady=(8, 0))
        self.fb_status = tk.Label(fb, text="", bg=BG, fg=MUTED, font=(FONT, 9))
        self.fb_status.pack(side="left")
        self.dislike_btn = ttk.Button(fb, text="👎 不再推这类",
                                      command=lambda: self._feedback("dislike"))
        self.dislike_btn.pack(side="right")
        self.like_btn = ttk.Button(fb, text="👍 感兴趣",
                                   command=lambda: self._feedback("like"))
        self.like_btn.pack(side="right", padx=(0, 8))

        self.detail = tk.Text(detail_wrap, bg=CARD, fg=TEXT, font=(FONT, 10),
                              relief="flat", wrap="word", padx=18, pady=16,
                              cursor="arrow", highlightthickness=0)
        self.detail.pack(side="left", fill="both", expand=True)
        dsb = ttk.Scrollbar(detail_wrap, orient="vertical", command=self.detail.yview)
        dsb.pack(side="right", fill="y")
        self.detail.config(yscrollcommand=dsb.set)

        self._configure_text_tags()
        self.detail.config(state="disabled")

    def _configure_text_tags(self) -> None:
        d = self.detail
        d.tag_configure("h1", font=(FONT, 15, "bold"), foreground=TEXT, spacing3=6)
        d.tag_configure("score", font=(FONT, 22, "bold"), foreground=ACCENT, spacing3=8)
        d.tag_configure("meta", font=(FONT, 9), foreground=MUTED, spacing3=10)
        d.tag_configure("section", font=(FONT, 10, "bold"), foreground=ACCENT,
                        spacing1=12, spacing3=4)
        d.tag_configure("body", font=(FONT, 10), foreground=TEXT, spacing3=3)
        d.tag_configure("dim", font=(FONT, 9), foreground=MUTED, spacing3=2)
        d.tag_configure("link", font=(FONT, 9, "underline"), foreground="#2a6fb5")
        d.tag_configure("bullet", font=(FONT, 10), foreground=TEXT,
                        lmargin1=16, lmargin2=30, spacing3=2)

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
            self.batch_combo["values"] = []
            self.batch_combo.set("")
            self.event_list.delete(0, "end")
            self._render_placeholder(
                "还没有任何推荐记录。\n\n"
                "点右上角「立即抓取新活动」跑一次，达标的活动会出现在这里。"
            )
            self.status_label.config(text="暂无数据")
            return

        labels = []
        for i, b in enumerate(self.batches):
            when = datetime.fromisoformat(b["created_at"]).strftime("%Y-%m-%d %H:%M")
            tag = "当前推荐" if i == 0 else "历史"
            labels.append(f"{tag} · {when} · {b['item_count']} 个")
        self.batch_combo["values"] = labels
        if select_latest:
            self.batch_combo.current(0)
        self._on_batch_change()
        self.status_label.config(text=f"共 {len(self.batches)} 批推荐记录")

    def _on_batch_change(self) -> None:
        idx = self.batch_combo.current()
        if idx < 0 or idx >= len(self.batches):
            return
        batch = self.batches[idx]
        store = self._store()
        try:
            self.current_events = store.batch_events(int(batch["id"]))
        finally:
            store.close()

        self.event_list.delete(0, "end")
        for ev in self.current_events:
            day = "--"
            if ev.get("start"):
                try:
                    day = datetime.fromisoformat(ev["start"]).strftime("%m-%d")
                except ValueError:
                    pass
            tag = CAT_ZH.get(ev.get("category", ""), "其他")
            self.event_list.insert(
                "end", f" {ev.get('total', 0):>5.1f}  {day}  {tag}  {ev.get('title', '')}")
        if self.current_events:
            self.event_list.selection_set(0)
            self._on_event_select()
        else:
            self._render_placeholder("该批次没有活动记录。")

    def _on_event_select(self) -> None:
        sel = self.event_list.curselection()
        if not sel:
            return
        self.current_event = self.current_events[sel[0]]
        self._render_event(self.current_event)
        self._refresh_feedback_label()
        self._log_behavior("view", self.current_event)

    # ------------------------------------------------------- 行为日志（v1.3.0）
    def _log_behavior(self, kind: str, ev: dict) -> None:
        """查看/点链接都是个性化（Track B）的学习信号，静默记录，失败不打扰。"""
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
        ev = getattr(self, "current_event", None)
        if not ev:
            return
        url = ev.get("booking_url") or ev.get("source_url")
        if url:
            webbrowser.open(url)
            self._log_behavior("open_link", ev)

    # ------------------------------------------------------- 反馈（v1.2.0）
    def _refresh_feedback_label(self) -> None:
        ev = getattr(self, "current_event", None)
        if not ev:
            self.fb_status.config(text="")
            return
        store = self._store()
        try:
            verdict = store.feedback_for(ev.get("title", ""), ev.get("venue_name", ""))
        finally:
            store.close()
        text = {"like": "已标记：感兴趣",
                "dislike": "已标记：不再推这类（同名同场地的活动以后不会出现）"}.get(verdict, "")
        self.fb_status.config(text=text)

    def _feedback(self, verdict: str) -> None:
        ev = getattr(self, "current_event", None)
        if not ev:
            return
        store = self._store()
        try:
            store.add_feedback(ev.get("title", ""), ev.get("venue_name", ""),
                               verdict, ev.get("uid"), ev.get("category"))
        finally:
            store.close()
        self._refresh_feedback_label()

    # ------------------------------------------------------- 详情渲染
    def _render_placeholder(self, text: str) -> None:
        self.detail.config(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("end", text, "body")
        self.detail.config(state="disabled")

    def _render_event(self, ev: dict) -> None:
        d = self.detail
        d.config(state="normal")
        d.delete("1.0", "end")

        d.insert("end", f"{ev.get('total', 0):.1f}", "score")
        d.insert("end", "  / 100\n", "dim")
        d.insert("end", f"{ev.get('title', '')}\n", "h1")

        # ---- 时间 / 地点 / 价格 ----
        start = ev.get("start")
        when = "时间待定"
        if start:
            try:
                when = datetime.fromisoformat(start).strftime("%Y-%m-%d (%a) %H:%M")
            except ValueError:
                when = start
        where = ev.get("venue_name") or ""
        if ev.get("suburb"):
            where = f"{where}，{ev['suburb']}" if where else ev["suburb"]
        price = ev.get("price_per_person")
        price_txt = "免费" if not price else f"A${price:.0f}/人"
        dur = f"　时长 {ev['duration_hours']:g} 小时" if ev.get("duration_hours") else ""
        d.insert("end", f"{when}　{where}　{price_txt}{dur}\n", "meta")

        if ev.get("description"):
            d.insert("end", f"{ev['description']}\n", "body")

        plan = ev.get("plan") or {}

        if plan.get("reason"):
            d.insert("end", "推荐理由\n", "section")
            d.insert("end", f"{plan['reason']}\n", "body")

        if ev.get("audience"):
            d.insert("end", "适合对象\n", "section")
            d.insert("end", "、".join(ev["audience"]) + "\n", "body")

        # ---- 七维度得分 ----
        scores = ev.get("scores") or {}
        dims = [(k, v) for k, v in scores.items() if not k.startswith("_")]
        if dims:
            d.insert("end", "各项得分\n", "section")
            for _key, s in dims:
                d.insert("end",
                         f"　{s.get('label', ''):　<6}{s.get('score', 0):>5}/10　"
                         f"×{s.get('weight', 0)}　{s.get('reason', '')}\n", "dim")
            if "_modifier" in scores:
                zh = {"solo_oriented": "单人向", "community_venue": "社区场地"}
                parts = [f"{zh.get(k, k)} ×{v}" for k, v in scores["_modifier"].items()]
                d.insert("end", f"　（降权：{'、'.join(parts)}）\n", "dim")
            if "_datefit" in scores:
                d.insert("end",
                         f"　内容判别模型 P(有价值) = {scores['_datefit']}\n", "dim")

        # ---- 约会方案 ----
        if plan.get("timeline"):
            d.insert("end", "约会方案\n", "section")
            for step in plan["timeline"]:
                d.insert("end", f"• {step.get('time','')}　{step.get('label','')} — "
                                f"{step.get('detail','')}\n", "bullet")

        budget = plan.get("budget") or {}
        if budget:
            d.insert("end", "预算\n", "section")
            d.insert("end",
                     f"前置 A${budget.get('pre_aud',0)} + 主活动 A${budget.get('main_aud',0)}"
                     f" + 续摊 A${budget.get('post_aud',0)} + 交通 A${budget.get('transit_aud',0)}"
                     f"　＝　每人 A${budget.get('per_person_total_aud',0)}"
                     f"（两人 A${budget.get('couple_total_aud',0)}）\n", "body")

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

        # ---- 链接 ----
        url = ev.get("booking_url") or ev.get("source_url")
        if url:
            d.insert("end", "订票 / 详情\n", "section")
            start_idx = d.index("end-1c")
            d.insert("end", f"{url}\n", "link")
            end_idx = d.index("end-1c")
            tag = f"link_{abs(hash(url))}"
            d.tag_add(tag, start_idx, end_idx)
            d.tag_configure(tag, foreground="#2a6fb5", underline=True)

            def _click_link(_e, u=url, event=ev):
                webbrowser.open(u)
                self._log_behavior("open_link", event)
            d.tag_bind(tag, "<Button-1>", _click_link)
            d.tag_bind(tag, "<Enter>", lambda _e: d.config(cursor="hand2"))
            d.tag_bind(tag, "<Leave>", lambda _e: d.config(cursor="arrow"))

        if ev.get("evidence"):
            d.insert("end", f"\n来源：{'、'.join(ev['evidence'])}\n", "dim")

        d.config(state="disabled")

    # ------------------------------------------------------- 手动抓取
    def _run_pipeline(self) -> None:
        """在后台线程跑管线，避免界面卡死。"""
        self.refresh_btn.config(state="disabled")
        self.status_label.config(text="正在抓取…")

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
        self.refresh_btn.config(state="normal")
        if error is not None:
            self.status_label.config(text="抓取失败")
            messagebox.showerror("抓取失败", f"{type(error).__name__}: {error}")
            return

        self._reload_batches()
        n = stats.get("recommended", 0)
        if n:
            self.status_label.config(text=f"本次新增 {n} 个推荐")
        else:
            self.status_label.config(text="本次没有达标的活动")
            messagebox.showinfo(
                "本次无推荐",
                f"核验通过 {stats.get('verified_passed', 0)} 个活动，"
                f"但没有一个达到推荐线。\n\n不降低标准凑数——下次再看。")

        if stats.get("errors"):
            messagebox.showwarning("采集告警", "\n".join(stats["errors"][:8]))


def main() -> int:
    enable_dpi_awareness()
    app = ScoutApp(str(default_config_path()))
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
