"""核验层：时间/地点/价格/来源有效性 + 预算/距离/类别过滤 + 硬排除 + solo_oriented 标记。

写入 event.exclude_reason（非 None 即应剔除），调用方（pipeline）据此过滤。
硬排除类别/关键词优先于「未订阅类别」判断，即使用户手滑把 esports/anime 加进
config 的 categories 也不会漏过 —— 对应 README「类别 + 关键词双重拦截」。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from .models import Event

HARD_EXCLUDE_CATEGORIES = {"esports", "anime"}
HARD_EXCLUDE_KEYWORDS = [
    "电竞", "esports", "e-sports", "csgo", "cs2", "dota", "league of legends", "lol 表演赛",
    "动漫", "anime", "cosplay", "漫展", "manga", "二次元",
]
# 更适合独自参加的项目：命中即降权（scoring.py 里的 solo_oriented_multiplier），不剔除。
SOLO_ORIENTED_KEYWORDS = [
    "meetup", "meet-up", "讲座", "lecture", "networking", "交流会", "读书会", "book club",
    "workshop", "研讨会", "seminar", "webinar", "行业沙龙", "职场社群", "求职",
]


def _text(e: Event) -> str:
    return f"{e.title} {e.description}".lower()


def _hard_excluded(e: Event) -> Optional[str]:
    if e.category in HARD_EXCLUDE_CATEGORIES:
        return f"硬排除类别：{e.category}"
    t = _text(e)
    for kw in HARD_EXCLUDE_KEYWORDS:
        if kw.lower() in t:
            return f"硬排除关键词命中：{kw}"
    return None


def verify_event(e: Event, cfg: dict) -> Event:
    reason = _hard_excluded(e)
    if reason:
        e.exclude_reason = reason
        return e

    if cfg.get("categories") and e.category and e.category not in cfg["categories"]:
        e.exclude_reason = f"未订阅类别：{e.category}"
        return e

    if not e.source_url:
        e.exclude_reason = "无来源链接"
        return e

    if e.start is None:
        e.exclude_reason = "缺少有效时间"
        return e

    now = datetime.now()
    start_naive = e.start.replace(tzinfo=None) if getattr(e.start, "tzinfo", None) else e.start
    lookahead_min = timedelta(days=cfg.get("lookahead_min_days", 0))
    lookahead_max = timedelta(days=cfg.get("lookahead_days", 21))

    if start_naive < now:
        e.exclude_reason = "已过期"
        return e
    if start_naive < now + lookahead_min:
        e.exclude_reason = "早于最短提前天数"
        return e
    if start_naive > now + lookahead_max:
        e.exclude_reason = f"超出推荐窗口（{cfg.get('lookahead_days')} 天）"
        return e

    if e.ticket_status == "sold_out":
        e.exclude_reason = "已售罄"
        return e

    if cfg.get("require_price", True) and e.price_per_person is None:
        e.exclude_reason = "价格未知"
        return e

    budget = cfg.get("budget_per_person_aud")
    if budget is not None and e.price_per_person is not None and e.price_per_person > budget:
        e.exclude_reason = f"超预算（A${e.price_per_person:.0f} > A${budget}）"
        return e

    max_dist = cfg.get("max_distance_km")
    if max_dist is not None and e.distance_km is not None and e.distance_km > max_dist:
        e.exclude_reason = f"超距离（{e.distance_km:.0f}km > {max_dist}km）"
        return e

    if any(kw.lower() in _text(e) for kw in SOLO_ORIENTED_KEYWORDS):
        e.solo_oriented = True

    return e


def verify_events(events: list[Event], cfg: dict) -> list[Event]:
    return [verify_event(e, cfg) for e in events]
