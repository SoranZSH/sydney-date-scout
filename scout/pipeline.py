"""串联 采集 → 去重 → 核验 → 评分 → 阈值筛选 → 方案生成 → 渲染 → 发信 的完整管线。"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import requests
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .collectors import get_collector
from .config import db_path, resolve_env
from .dedupe import dedupe_events
from .mailer import send_digest
from .models import Event
from .plan import build_plan
from .scoring import passes_threshold, score_event
from .store import Store
from .verify import verify_events

log = logging.getLogger(__name__)

UA = "sydney-date-scout/1.0"


def _collect_all(cfg: dict, only_sources: Optional[list[str]], errors: list[str]) -> list[Event]:
    events: list[Event] = []
    for source in cfg["sources"]:
        if only_sources and source["id"] not in only_sources:
            continue
        if not source.get("enabled", True):
            continue
        try:
            fn = get_collector(source["type"])
            got = fn(source, cfg)
            events.extend(got)
            log.info("采集 %-28s %d 条", source["id"], len(got))
        except Exception as ex:                            # noqa: BLE001
            msg = f"{source['id']}: {type(ex).__name__}: {ex}"
            log.warning("采集失败 %s", msg)
            errors.append(msg)
    return events


def _check_urls(events: list[Event], errors: list[str]) -> None:
    for e in events:
        url = e.booking_url or e.source_url
        if not url:
            continue
        try:
            r = requests.head(url, timeout=10, allow_redirects=True, headers={"User-Agent": UA})
            if r.status_code >= 400:
                errors.append(f"链接疑似失效({r.status_code}): {e.title} {url}")
        except Exception as ex:                            # noqa: BLE001
            errors.append(f"链接不可达: {e.title} {url} ({ex})")


def _render_html(cfg: dict, events: list[Event], empty: bool, verified_count: int) -> tuple[str, str]:
    templates_dir = Path(cfg["_base_dir"]) / cfg.get("templates_dir", "templates")
    env = Environment(loader=FileSystemLoader(str(templates_dir)),
                      autoescape=select_autoescape(["html"]))
    tpl = env.get_template("digest.html.jinja")
    rubric = cfg["rubric"]
    subject = "悉尼约会提案 · 本次无推荐" if empty else f"悉尼约会提案 · {len(events)} 个新方案"
    html = tpl.render(
        subject=subject,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        rubric_version=rubric["version"],
        recommend_total=rubric["thresholds"]["recommend_total"],
        max_items=rubric["thresholds"]["max_items"],
        interval_days=cfg.get("interval_days", 3),
        events=events,
        empty=empty,
        verified_count=verified_count,
    )
    return subject, html


def _reason_key(reason: str) -> str:
    return re.split(r"[：(（]", reason)[0].strip()


def _empty_reason(verified_count: int, skipped_recent: int, rubric: dict) -> str:
    """区分「没有达标的」和「达标的最近都推过了」—— 两者原因完全不同，
    混为一谈会让人以为最近悉尼没好活动。"""
    if skipped_recent:
        return (f"有 {skipped_recent} 个达标活动，但最近都已经推荐过了，"
                f"暂时没有新的。\n可以打开程序回看上一批推荐。")
    return (f"本次核验通过 {verified_count} 个活动，但没有一个达到 "
            f"{rubric['thresholds']['recommend_total']} 分的推荐线。\n"
            f"不降低标准凑数——宁可这次不推。")


def run_once(cfg: dict, dry_run: bool = False, check_urls: bool = False,
            only_sources: Optional[list[str]] = None) -> dict[str, Any]:
    errors: list[str] = []
    rubric = cfg["rubric"]
    store = Store(db_path(cfg))

    raw_events = _collect_all(cfg, only_sources, errors)
    deduped = dedupe_events(raw_events, cfg)
    verified = verify_events(deduped, cfg)

    passed = [e for e in verified if e.exclude_reason is None]
    excluded = [e for e in verified if e.exclude_reason is not None]
    excluded_breakdown: dict[str, int] = {}
    for e in excluded:
        key = _reason_key(e.exclude_reason)
        excluded_breakdown[key] = excluded_breakdown.get(key, 0) + 1

    if check_urls:
        _check_urls(passed, errors)

    scored = [score_event(e, rubric) for e in passed]
    for e in scored:
        store.save_event(e)

    vetoed: list[str] = []
    below_threshold: list[float] = []
    qualifying: list[Event] = []
    for e in scored:
        ok, why = passes_threshold(e, rubric)
        if ok:
            qualifying.append(e)
        elif "低于下限" in why:
            vetoed.append(f"{e.title}：{why}")
        else:
            below_threshold.append(e.total)

    qualifying.sort(key=lambda e: e.total, reverse=True)
    max_items = rubric["thresholds"]["max_items"]
    resend_days = cfg.get("resend_after_days", 30)

    skipped_recent = 0
    final: list[Event] = []
    for e in qualifying:
        if not store.should_send(e, resend_days):
            skipped_recent += 1
            continue
        final.append(e)
        if len(final) >= max_items:
            break

    for e in final:
        build_plan(e, cfg)

    empty = len(final) == 0
    output_file: Optional[str] = None
    batch_id: Optional[int] = None
    notified = False
    sent = False

    announce = not (empty and not cfg.get("send_when_empty", True))

    if announce:
        subject, html = _render_html(cfg, final, empty, len(passed))
        output_dir = Path(cfg["_data_dir"]) / cfg.get("output_dir", "data/out")
        output_dir.mkdir(parents=True, exist_ok=True)
        out_path = output_dir / f"digest-{datetime.now().strftime('%Y%m%d-%H%M%S')}.html"
        out_path.write_text(html, encoding="utf-8")
        output_file = str(out_path)

    # 只有真正产生了推荐才落批次；空批次不入库，免得 GUI 里全是空记录。
    if final and not dry_run:
        batch_id = store.create_batch(final, rubric["version"])

    # 必须在弹窗之前记录本次运行。
    # notify() 最后是 root.mainloop()，弹窗不关就一直阻塞在那里；
    # 如果把 record_run() 放在它后面，无人值守时（比如夜里跑完没人关窗）
    # 这次运行永远不会被记录，间隔守卫下次读到的还是上一次的时间，
    # 于是「跑过了但不算数」——采集白跑一轮，日志里还看不出问题。
    # 这一轮是否真的跑完，与用户有没有点掉弹窗无关。
    store.record_run()

    if announce and not dry_run:
        # ---- 弹窗通知（默认投递方式）----
        if cfg.get("notify", {}).get("enabled", True):
            try:
                from .notifier import notify
                notify(final, empty_reason=_empty_reason(len(passed), skipped_recent, rubric),
                       auto_close_seconds=cfg["notify"].get("auto_close_seconds", 0)
                       if isinstance(cfg.get("notify"), dict) else 0)
                notified = True
            except Exception as ex:                        # noqa: BLE001
                errors.append(f"弹窗失败: {type(ex).__name__}: {ex}")

        # ---- 邮件（可选，默认关闭）----
        if cfg.get("email", {}).get("enabled", False):
            try:
                mail_cfg = dict(cfg)
                mail_cfg["email"] = resolve_env(cfg["email"])
                send_digest(mail_cfg, subject, html)
                sent = True
            except Exception as ex:                        # noqa: BLE001
                errors.append(f"发信失败: {type(ex).__name__}: {ex}")

    store.close()

    return {
        "collected": len(raw_events),
        "after_dedupe": len(deduped),
        "verified_passed": len(passed),
        "excluded_breakdown": excluded_breakdown,
        "batch_id": batch_id,
        "notified": notified,
        "recommended": len(final),
        "top_score": final[0].total if final else None,
        "below_threshold_scores": sorted(below_threshold, reverse=True),
        "vetoed": vetoed,
        "skipped_recent": skipped_recent,
        "output_file": output_file,
        "sent": sent,
        "errors": errors,
    }
