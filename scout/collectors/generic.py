"""通用采集器：rss / ics / json。

合规：只用公开 feed/API；每源请求后默认延时 1 秒；带 User-Agent。
只能尽力解析日期与价格，解析不出的字段留空，交给 verify 阶段决定是否剔除
（宁可漏推，不推错）。
"""
from __future__ import annotations

import logging
import re
import time

import requests

from ..models import Event
from . import collector
from ._common import dig, make_uid, parse_dt

log = logging.getLogger(__name__)

UA = "sydney-date-scout/1.0 (+https://example.invalid)"
PRICE_RE = re.compile(r"\$\s?(\d+(?:\.\d{1,2})?)")


def _throttle(cfg: dict) -> None:
    time.sleep(cfg.get("request_delay_seconds", 1))


def _guess_price(*texts: str) -> float | None:
    for t in texts:
        if not t:
            continue
        low = t.lower()
        if "free" in low or "免费" in t:
            return 0.0
        m = PRICE_RE.search(t)
        if m:
            return float(m.group(1))
    return None


def _base_event(source: dict, title: str, description: str, link: str) -> Event:
    e = Event()
    e.title = (title or "").strip()
    e.description = (description or "").strip()
    e.source_url = link or source.get("url", "")
    e.booking_url = link or None
    e.category = source.get("category_hint", "")
    e.venue_name = source.get("default_venue", "")
    e.suburb = source.get("default_suburb", "")
    e.source_id = source["id"]
    e.evidence = [source["id"]]
    e.price_per_person = _guess_price(description or "")
    e.merge_signal_defaults(source.get("defaults"))
    return e


@collector("rss")
def rss_collector(source: dict, cfg: dict) -> list[Event]:
    import feedparser

    resp = requests.get(source["url"], timeout=20, headers={"User-Agent": UA})
    resp.raise_for_status()
    _throttle(cfg)
    parsed = feedparser.parse(resp.content)

    events: list[Event] = []
    for entry in parsed.entries:
        description = entry.get("summary") or entry.get("description") or ""
        e = _base_event(source, entry.get("title", ""), description, entry.get("link", ""))
        start_raw = entry.get("published") or entry.get("updated") or entry.get("pubDate")
        e.start = parse_dt(start_raw)
        e.uid = make_uid(e.title, str(e.start), e.venue_name or source["id"])
        events.append(e)
    return events


@collector("ics")
def ics_collector(source: dict, cfg: dict) -> list[Event]:
    from icalendar import Calendar

    resp = requests.get(source["url"], timeout=20, headers={"User-Agent": UA})
    resp.raise_for_status()
    _throttle(cfg)
    cal = Calendar.from_ical(resp.content)

    events: list[Event] = []
    for comp in cal.walk("VEVENT"):
        title = str(comp.get("summary", ""))
        description = str(comp.get("description", ""))
        link = str(comp.get("url", "") or source.get("url", ""))
        e = _base_event(source, title, description, link)
        dtstart = comp.get("dtstart")
        dtend = comp.get("dtend")
        e.start = dtstart.dt if dtstart else None
        e.end = dtend.dt if dtend else None
        if e.start and e.end and hasattr(e.start, "hour"):
            e.duration_hours = round((e.end - e.start).total_seconds() / 3600, 1)
        location = str(comp.get("location", ""))
        if location and not e.venue_name:
            e.venue_name = location
        e.uid = make_uid(e.title, str(e.start), e.venue_name or source["id"])
        events.append(e)
    return events


@collector("json")
def json_collector(source: dict, cfg: dict) -> list[Event]:
    headers = {"User-Agent": UA, **(source.get("headers") or {})}
    resp = requests.get(source["url"], timeout=20, headers=headers)
    resp.raise_for_status()
    _throttle(cfg)
    data = resp.json()

    items = dig(data, source["items_path"]) if source.get("items_path") else data
    if not isinstance(items, list):
        log.warning("json 源 %s 的 items_path 未指向数组，跳过", source["id"])
        return []

    fmap = source.get("field_map", {})
    events: list[Event] = []
    for item in items:
        title = dig(item, fmap.get("title", "title")) or ""
        description = dig(item, fmap.get("description", "description")) or ""
        link = dig(item, fmap.get("source_url", "url")) or ""
        e = _base_event(source, title, description, link)
        e.booking_url = dig(item, fmap.get("booking_url", "")) or e.source_url
        e.start = parse_dt(dig(item, fmap.get("start", "start")))
        e.end = parse_dt(dig(item, fmap.get("end", "end")))
        if e.start and e.end:
            e.duration_hours = round((e.end - e.start).total_seconds() / 3600, 1)
        venue = dig(item, fmap.get("venue_name", ""))
        if venue:
            e.venue_name = venue
        suburb = dig(item, fmap.get("suburb", ""))
        if suburb:
            e.suburb = suburb
        price = dig(item, fmap.get("price_per_person", ""))
        if price is not None:
            try:
                e.price_per_person = float(price)
            except (TypeError, ValueError):
                pass
        e.uid = make_uid(e.title, str(e.start), e.venue_name or source["id"])
        events.append(e)
    return events
