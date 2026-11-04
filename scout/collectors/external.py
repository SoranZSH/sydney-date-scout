"""外部爬虫接口（`type: external`）—— 本项目与任意爬虫工具之间的唯一契约。

设计取舍
--------
本项目**不内置任何站点爬虫**。哪个站能抓、怎么抓、条款是否允许，因城市、因站点、
因时间而变，而 GitHub 上的通用爬虫工具（Scrapy、Playwright、Crawl4AI、Firecrawl……）
迭代得比任何一个专用适配器都快。所以这里只定义「交货格式」：

    你的爬虫（任何语言、任何工具） ──► 标准 JSON 事件 ──► 本项目（去重/核验/评分/方案）

两种交货方式，在 sources.yaml 里二选一：

- `mode: file`    —— 爬虫把结果写成 .json / .jsonl 放进收件箱目录（默认 `data/inbox/`），
                     本采集器每轮读取。**最解耦**：爬虫可以单独定时、单独部署。
- `mode: command` —— 每轮运行一条命令，读取它打印到 stdout 的 JSON / JSONL。
                     适合「抓一次就出结果」的轻量脚本。

数据契约见 `docs/event.schema.json`；`python run.py validate-feed <文件>` 可自检。
只有 title / start / url 是必填，其余字段越全，评分越准 —— 缺了的约会氛围、
互动性等信号会按类别画像（CATEGORY_PROFILES）保守推断，推断不会覆盖爬虫给出的值。
"""
from __future__ import annotations

import glob
import json
import logging
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

from ..geo import distance_and_transit
from ..models import Event
from . import collector
from ._common import make_uid, parse_dt

log = logging.getLogger(__name__)

CONTRACT_VERSION = "1.0"

# --------------------------------------------------------------------------
# 类别：爬虫给的是站点自己的分类词，这里归一到本项目的 config.categories。
# 匹配规则：小写后按「词」包含判断，先命中先得；都不命中则用 source.category_hint。
# --------------------------------------------------------------------------
PROJECT_CATEGORIES = {
    "film", "performance", "exhibition", "festival", "market", "food", "venue",
    "experience", "sport", "nature", "sale", "daytrip", "esports", "anime",
    "family", "community",
}
CATEGORY_ALIASES: list[tuple[str, tuple[str, ...]]] = [
    # 先放会被硬排除的类别：宁可误判成排除，也不让儿童/社区活动混进推荐
    ("family", ("kids", "children", "family", "school holiday", "亲子", "儿童")),
    ("community", ("community", "volunteer", "社区", "志愿")),
    ("esports", ("esports", "e-sports", "gaming", "电竞")),
    ("anime", ("anime", "cosplay", "manga", "动漫", "漫展")),
    ("film", ("film", "cinema", "movie", "screening", "电影")),
    ("performance", ("music", "concert", "gig", "theatre", "theater", "dance", "comedy",
                     "opera", "ballet", "musical", "演出", "音乐", "话剧", "脱口秀")),
    ("exhibition", ("exhibition", "gallery", "museum", "art", "展")),
    ("festival", ("festival", "节")),
    ("market", ("market", "fair", "bazaar", "市集", "集市")),
    ("food", ("food", "drink", "dining", "wine", "beer", "restaurant", "tasting", "美食", "餐")),
    ("venue", ("nightlife", "bar", "club", "rooftop", "酒吧")),
    ("sport", ("sport", "fitness", "match", "game day", "运动", "比赛")),
    ("nature", ("nature", "outdoor", "park", "beach", "garden", "walk", "hike",
                "astronomy", "自然", "徒步", "海滩")),
    ("daytrip", ("day trip", "daytrip", "一日游")),
    ("sale", ("sale", "discount", "折扣")),
    ("experience", ("workshop", "class", "course", "tour", "experience", "体验", "工作坊")),
]


def normalize_category(raw: Iterable[str], fallback: str) -> str:
    words = [str(w).strip().lower() for w in raw if w]
    for w in words:
        if w in PROJECT_CATEGORIES:
            return w
    for cat, keys in CATEGORY_ALIASES:
        for w in words:
            if any(re.search(rf"(?<![a-z]){re.escape(k)}(?:s|es)?(?![a-z])", w) for k in keys):
                return cat
    return fallback


# --------------------------------------------------------------------------
# 类别画像：爬虫通常拿不到「噪不噪、有没有座位、能不能互动」这类约会信号，
# 按活动形态给一个保守的先验。只填 None 字段，爬虫显式给出的值永远优先。
# --------------------------------------------------------------------------
CATEGORY_PROFILES: dict[str, dict[str, Any]] = {
    "film":        {"noise_level": "quiet",    "has_seating": True,  "interaction": "spectate_silent",   "photogenic": False},
    "performance": {"noise_level": "moderate", "has_seating": True,  "interaction": "spectate_shared",   "photogenic": True},
    "exhibition":  {"noise_level": "quiet",    "has_seating": False, "interaction": "spectate_shared",   "photogenic": True},
    "festival":    {"noise_level": "moderate", "has_seating": False, "interaction": "light_participate", "photogenic": True},
    "market":      {"noise_level": "moderate", "has_seating": False, "interaction": "light_participate", "photogenic": True},
    "food":        {"noise_level": "moderate", "has_seating": True,  "interaction": "light_participate", "photogenic": True,
                    "nearby_food_options": True},
    "venue":       {"noise_level": "moderate", "has_seating": True,  "interaction": "spectate_shared",   "photogenic": True,
                    "nearby_food_options": True},
    "experience":  {"noise_level": "moderate", "has_seating": False, "interaction": "hands_on",          "photogenic": False},
    "sport":       {"noise_level": "loud",     "has_seating": True,  "interaction": "spectate_shared",   "photogenic": False},
    "nature":      {"noise_level": "quiet",    "has_seating": False, "interaction": "light_participate", "photogenic": True},
    "daytrip":     {"noise_level": "quiet",    "has_seating": False, "interaction": "light_participate", "photogenic": True},
}

SIGNAL_FIELDS = {
    "window_days", "total_sessions", "ticket_status",
    "first_time_in_city", "is_first_edition", "new_opening_days", "is_annual_repeat",
    "has_new_content", "is_permanent", "media_mentions", "social_signal",
    "noise_level", "has_seating", "photogenic", "evening_or_weekend", "nearby_food_options",
    "interaction", "transit_minutes", "transit_transfers", "walk_minutes", "distance_km",
    "solo_oriented", "community_venue",
}
ENUMS = {
    "noise_level": {"quiet", "moderate", "loud"},
    "interaction": {"co_create", "hands_on", "light_participate", "spectate_shared",
                    "spectate_silent", "none"},
    "ticket_status": {"available", "limited", "sold_out"},
}
PRICE_RE = re.compile(r"\$?\s*(\d+(?:\.\d{1,2})?)")


class FeedError(ValueError):
    """单条记录不符合契约。消息要写给「正在对接的 AI Agent」看：说清哪个字段、该怎么改。"""


def _parse_price(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        raise FeedError("price 不能是布尔值；免费请填 0")
    if isinstance(v, (int, float)):
        if v < 0:
            raise FeedError("price 不能为负数")
        return float(v)
    s = str(v).strip().lower()
    if s in ("free", "免费", "0"):
        return 0.0
    nums = [float(m) for m in PRICE_RE.findall(s)]
    if not nums:
        raise FeedError(f"price 无法解析：{v!r}（请填人均澳元数字，免费填 0）")
    return min(nums)                 # "From $25" / "$25–$60" 取最低票价


def _as_list(v: Any) -> list:
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def parse_record(rec: dict, source: dict, cfg: dict) -> Event:
    """把一条契约记录转成 Event。违反契约时抛 FeedError（由调用方跳过并记录）。"""
    if not isinstance(rec, dict):
        raise FeedError("每条记录必须是 JSON 对象")
    title = str(rec.get("title") or "").strip()
    if not title:
        raise FeedError("缺少必填字段 title")
    url = str(rec.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        raise FeedError("缺少必填字段 url（活动详情页的 http(s) 链接）")
    start = parse_dt(rec.get("start"))
    if start is None:
        raise FeedError(f"start 缺失或无法解析：{rec.get('start')!r}（请用 ISO 8601，如 2026-11-14T19:30）")
    if start.tzinfo is not None:            # 统一成本地 naive 时间，与其余采集器一致
        start = start.astimezone().replace(tzinfo=None)

    e = Event()
    e.title = title
    e.description = str(rec.get("description") or "").strip()
    e.start = start
    end = parse_dt(rec.get("end"))
    if end is not None and end.tzinfo is not None:
        end = end.astimezone().replace(tzinfo=None)
    e.end = end
    e.duration_hours = rec.get("duration_hours")
    if e.duration_hours is None and end is not None and end > start:
        e.duration_hours = round((end - start).total_seconds() / 3600, 2)
    e.venue_name = str(rec.get("venue") or "").strip()
    e.suburb = str(rec.get("suburb") or "").strip()
    e.source_url = url
    e.booking_url = rec.get("booking_url") or url
    e.price_per_person = _parse_price(rec.get("price"))
    e.source_id = source["id"]
    e.evidence = [str(rec.get("source_name") or source["id"])]

    raw_cats = _as_list(rec.get("category")) + _as_list(rec.get("tags"))
    e.source_categories = [str(c) for c in raw_cats]
    e.category = normalize_category(raw_cats, source.get("category_hint", "experience"))

    # ---- 显式信号（优先级最高）----
    signals = rec.get("signals") or {}
    if not isinstance(signals, dict):
        raise FeedError("signals 必须是对象")
    for k, v in signals.items():
        if k not in SIGNAL_FIELDS:
            raise FeedError(f"signals 里有未知字段 {k!r}（可用字段见 docs/event.schema.json）")
        if k in ENUMS and v is not None and v not in ENUMS[k]:
            raise FeedError(f"signals.{k} 只能是 {sorted(ENUMS[k])} 之一，收到 {v!r}")
        setattr(e, k, v)
    if rec.get("ticket_status") is not None:
        if rec["ticket_status"] not in ENUMS["ticket_status"]:
            raise FeedError(f"ticket_status 只能是 {sorted(ENUMS['ticket_status'])} 之一")
        e.ticket_status = rec["ticket_status"]

    # ---- 推断层：只补 None ----
    if e.distance_km is None:
        try:
            lat = None if rec.get("lat") is None else float(rec["lat"])
            lng = None if rec.get("lng") is None else float(rec["lng"])
        except (TypeError, ValueError):
            raise FeedError("lat / lng 必须是数字") from None
        d, m, t = distance_and_transit(cfg, lat, lng)
        e.distance_km = d
        if e.transit_minutes is None:
            e.transit_minutes, e.transit_transfers = m, t

    sessions = sorted({d.date() for d in (parse_dt(x) for x in _as_list(rec.get("dates"))) if d})
    remaining = [d for d in sessions if d >= start.date()]
    if remaining and e.total_sessions is None:
        e.total_sessions = len(remaining)
        e.window_days = (remaining[-1] - remaining[0]).days
        if e.window_days > 180:
            e.is_permanent = True

    if e.evening_or_weekend is None:
        e.evening_or_weekend = start.weekday() >= 5 or start.hour >= 17
    for k, v in CATEGORY_PROFILES.get(e.category, {}).items():
        if getattr(e, k) is None:
            setattr(e, k, v)
    # 「可续摊」：没有明确信号时，离起点 6km 内视为餐饮可达（悉尼内城经验值；
    # 换城市或住得远的用户应让爬虫直接给 signals.nearby_food_options）
    if e.nearby_food_options is None and e.distance_km is not None and e.distance_km <= 6:
        e.nearby_food_options = True
    if e.media_mentions is None:
        e.media_mentions = len(e.evidence)

    e.merge_signal_defaults(source.get("defaults"))
    e.uid = make_uid(e.title, str(e.start), e.venue_name or source["id"])
    return e


# --------------------------------------------------------------------------
# 读取：文件 / 命令
# --------------------------------------------------------------------------
def load_records(text: str, origin: str) -> list[Any]:
    """接受三种形状：JSON 数组、{"events": [...]}、JSONL（每行一个对象）。"""
    text = text.strip()
    if not text:
        return []
    if text[0] in "[{":
        try:
            doc = json.loads(text)
            if isinstance(doc, list):
                return doc
            if isinstance(doc, dict) and isinstance(doc.get("events"), list):
                return doc["events"]
            if isinstance(doc, dict) and "title" in doc:
                return [doc]
        except json.JSONDecodeError:
            pass                      # 可能是以 { 开头的 JSONL，往下走
    out: list[Any] = []
    for i, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError as ex:
            raise FeedError(f"{origin} 第 {i} 行不是合法 JSON：{ex.msg}") from None
    return out


def _inbox_files(source: dict, cfg: dict) -> list[Path]:
    pattern = source.get("path", "data/inbox/*.json*")
    base = Path(cfg.get("_data_dir") or ".")
    full = pattern if Path(pattern).is_absolute() else str(base / pattern)
    files = [Path(p) for p in sorted(glob.glob(full))]
    max_age = source.get("max_age_hours")
    if max_age:
        cutoff = time.time() - float(max_age) * 3600
        stale = [p for p in files if p.stat().st_mtime < cutoff]
        if stale:
            log.warning("外部源 %s：%d 个文件超过 %s 小时未更新，已忽略（爬虫是否停了？）",
                        source["id"], len(stale), max_age)
        files = [p for p in files if p.stat().st_mtime >= cutoff]
    return files


def _run_command(source: dict, cfg: dict) -> str:
    cmd = source.get("command")
    if not cmd:
        raise FeedError("mode: command 需要配置 command")
    proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, encoding="utf-8",
                          timeout=int(source.get("timeout_seconds", 600)),
                          cwd=source.get("cwd") or cfg.get("_data_dir"))
    if proc.returncode != 0:
        raise RuntimeError(f"命令退出码 {proc.returncode}：{proc.stderr.strip()[-300:]}")
    return proc.stdout


def iter_raw(source: dict, cfg: dict) -> list[tuple[str, Any]]:
    """返回 [(出处, 原始记录)]。出处用于报错定位。"""
    mode = source.get("mode", "file")
    pairs: list[tuple[str, Any]] = []
    if mode == "file":
        for p in _inbox_files(source, cfg):
            for i, rec in enumerate(load_records(p.read_text(encoding="utf-8"), p.name), 1):
                pairs.append((f"{p.name}#{i}", rec))
    elif mode == "command":
        for i, rec in enumerate(load_records(_run_command(source, cfg), "stdout"), 1):
            pairs.append((f"stdout#{i}", rec))
    else:
        raise FeedError(f"未知 mode：{mode!r}（可选 file / command）")
    return pairs


@collector("external")
def external_collector(source: dict, cfg: dict) -> list[Event]:
    events: list[Event] = []
    bad = 0
    for where, rec in iter_raw(source, cfg):
        try:
            events.append(parse_record(rec, source, cfg))
        except FeedError as ex:
            bad += 1
            if bad <= 5:
                log.warning("外部源 %s：跳过 %s —— %s", source["id"], where, ex)
    if bad:
        log.warning("外部源 %s：共 %d 条不符合契约被跳过（python run.py validate-feed 可逐条查看）",
                    source["id"], bad)
    return events
