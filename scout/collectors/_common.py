"""采集器共用工具：uid 生成、字段路径取值、扁平事件解析。"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Any, Optional

from dateutil import parser as dateparser

from ..models import Event


def make_uid(*parts: str) -> str:
    key = "|".join(p.strip().lower() for p in parts if p)
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def dig(obj: Any, dotted_path: str) -> Any:
    """按 'a.b.c' 从嵌套 dict/list 中取值，取不到返回 None。"""
    cur = obj
    for part in dotted_path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit():
            idx = int(part)
            cur = cur[idx] if idx < len(cur) else None
        else:
            return None
        if cur is None:
            return None
    return cur


def parse_dt(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    try:
        return dateparser.parse(str(value))
    except (ValueError, OverflowError):
        return None


def _resolve_start(item: dict) -> Optional[datetime]:
    """支持两种写法：
    - `start`: 绝对时间（天文历等真实、固定日期的源用这个）。
    - `start_offset_days` + `start_time`: 相对「现在」的偏移（离线 fixtures 用这个，
      这样样例数据不会随时间流逝而"过期"，任何时候跑 --dry-run 都有效）。
    """
    if "start_offset_days" in item:
        base_date = (datetime.now() + timedelta(days=item["start_offset_days"])).date()
        hh, mm = (item.get("start_time") or "19:00").split(":")
        return datetime(base_date.year, base_date.month, base_date.day, int(hh), int(mm))
    return parse_dt(item.get("start"))


def parse_flat_event(item: dict, source: dict) -> Event:
    """astro / fixtures 两种本地源共用的扁平 schema：
    title/start/end/duration_hours/venue/suburb/url/booking_url/description/
    category/price_per_person + 一个可选的嵌套 `signals` 字典（对应 Event 的评分字段）。
    """
    e = Event()
    e.title = item.get("title", "")
    e.description = item.get("description", "")
    e.start = _resolve_start(item)
    e.end = parse_dt(item.get("end"))
    e.duration_hours = item.get("duration_hours")
    e.venue_name = item.get("venue", "")
    e.suburb = item.get("suburb", "")
    e.source_url = item.get("url", "")
    e.booking_url = item.get("booking_url", item.get("url", ""))
    e.category = item.get("category", source.get("category_hint", ""))
    e.price_per_person = item.get("price_per_person")
    e.source_id = source["id"]
    e.evidence = [source["id"]]

    for k, v in (item.get("signals") or {}).items():
        if hasattr(e, k):
            setattr(e, k, v)

    e.merge_signal_defaults(source.get("defaults"))
    e.uid = make_uid(e.title, str(e.start), e.venue_name)
    return e
