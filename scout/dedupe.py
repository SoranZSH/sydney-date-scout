"""去重：标题模糊匹配 × 时间接近 × 场地一致；合并后把各来源汇入 evidence。

`media_mentions` 缺失时，用合并到的独立来源数近似顶替
（对应 README 已知边界：媒体源越多，热度分越准）。
"""
from __future__ import annotations

import difflib

from .models import Event

TIME_CLOSE_HOURS = 24


def _title_key(t: str) -> str:
    return "".join(ch.lower() for ch in t if ch.isalnum() or ch.isspace()).strip()


def _title_similar(a: str, b: str, threshold: float) -> bool:
    return difflib.SequenceMatcher(None, _title_key(a), _title_key(b)).ratio() >= threshold


def _time_close(a: Event, b: Event) -> bool:
    if a.start is None or b.start is None:
        return a.start is None and b.start is None
    return abs((a.start - b.start).total_seconds()) <= TIME_CLOSE_HOURS * 3600


def _venue_consistent(a: Event, b: Event) -> bool:
    if a.venue_name and b.venue_name:
        return _title_similar(a.venue_name, b.venue_name, 0.8)
    return True  # 场地信息缺失时不作为否决条件


def _richness(e: Event) -> int:
    return sum(1 for v in vars(e).values() if v not in (None, "", [], {}))


def dedupe_events(events: list[Event], cfg: dict) -> list[Event]:
    threshold = cfg.get("dedupe_threshold", 0.82)
    clusters: list[list[Event]] = []

    for ev in events:
        for cluster in clusters:
            rep = cluster[0]
            if (_title_similar(ev.title, rep.title, threshold)
                    and _time_close(ev, rep) and _venue_consistent(ev, rep)):
                cluster.append(ev)
                break
        else:
            clusters.append([ev])

    merged: list[Event] = []
    for cluster in clusters:
        canonical = max(cluster, key=_richness)
        evidence: list[str] = []
        for e in cluster:
            for src in e.evidence:
                if src not in evidence:
                    evidence.append(src)
        canonical.evidence = evidence
        if canonical.media_mentions is None and len(evidence) > 1:
            canonical.media_mentions = len(evidence)
        merged.append(canonical)
    return merged
