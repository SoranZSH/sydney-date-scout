"""固定锚点评分引擎。

不变式（Invariants）
--------------------
1. score_event(event, rubric) 的输入只有「单个活动」和「固定锚点表」。
   函数签名里没有活动列表、没有平均分、没有排名 —— 相对化在结构上不可能发生。
2. 同一 rubric.version 下，同一活动在任何日期、任何批次得到的分数完全一致。
3. 缺失信号使用 rubric 中的保守默认分，并计入 completeness 惩罚。
"""
from __future__ import annotations

from typing import Any, Optional

from .models import Event

DIMENSIONS = ["freshness", "scarcity", "buzz", "date_vibe", "interactivity", "value", "transit"]


class DimScore:
    __slots__ = ("score", "reason", "missing")

    def __init__(self, score: float, reason: str, missing: bool = False):
        self.score = max(0.0, min(10.0, float(score)))
        self.reason = reason
        self.missing = missing

    def as_dict(self) -> dict:
        return {"score": round(self.score, 1), "reason": self.reason, "missing": self.missing}


def _band_max(bands: list[dict], value: float) -> Optional[int]:
    for b in bands:
        if value <= b["max"]:
            return b["score"]
    return None


def _band_min(bands: list[dict], value: float) -> Optional[int]:
    for b in bands:
        if value >= b["min"]:
            return b["score"]
    return None


# --------------------------------------------------------------------------
# 各维度
# --------------------------------------------------------------------------
def score_freshness(e: Event, r: dict) -> DimScore:
    a = r["dimensions"]["freshness"]["anchors"]
    if e.first_time_in_city:
        return DimScore(a["first_time_in_city"], "首次登陆悉尼/澳洲")
    if e.is_first_edition:
        return DimScore(a["first_edition"], "首届举办")
    if e.new_opening_days is not None:
        if e.new_opening_days <= 30:
            return DimScore(a["new_opening_days_le_30"], f"开业第 {e.new_opening_days} 天")
        if e.new_opening_days <= 90:
            return DimScore(a["new_opening_days_le_90"], f"开业第 {e.new_opening_days} 天")
    if e.is_annual_repeat:
        if e.has_new_content:
            return DimScore(a["annual_repeat_new_content"], "年度活动，本届有全新内容")
        return DimScore(a["annual_repeat_same"], "年度活动，内容基本沿用")
    if e.is_permanent:
        if e.has_new_content:
            return DimScore(a["permanent_with_new_content"], "常设项目，近期有新增内容")
        return DimScore(a["permanent_no_change"], "常设项目，无更新")
    return DimScore(r["modifiers"]["unknown_defaults"]["freshness"], "新鲜度信号缺失，按保守值计", True)


def score_scarcity(e: Event, r: dict) -> DimScore:
    d = r["dimensions"]["scarcity"]
    cands: list[tuple[int, str]] = []
    if e.window_days is not None:
        s = _band_max(d["anchors_window_days"], e.window_days)
        cands.append((s, f"窗口期 {e.window_days} 天"))
    if e.total_sessions is not None:
        s = _band_max(d["anchors_sessions"], e.total_sessions)
        cands.append((s, f"剩余 {e.total_sessions} 场"))
    if not cands:
        return DimScore(r["modifiers"]["unknown_defaults"]["scarcity"], "稀缺性信号缺失，按保守值计", True)
    # 取更严格（更高稀缺）的一项作为该维度得分
    score, reason = max(cands, key=lambda t: t[0])
    bonus = d["ticket_status_bonus"].get(e.ticket_status, 0)
    if bonus:
        reason += "，余票紧张 +1"
    return DimScore(score + bonus, reason)


def score_buzz(e: Event, r: dict) -> DimScore:
    d = r["dimensions"]["buzz"]
    cands: list[tuple[int, str]] = []
    if e.media_mentions is not None:
        cands.append((_band_min(d["anchors_media_mentions"], e.media_mentions),
                      f"{e.media_mentions} 家独立媒体报道"))
    if e.social_signal is not None:
        cands.append((_band_min(d["anchors_social_signal"], e.social_signal),
                      f"社媒信号量 ~{e.social_signal}"))
    if not cands:
        return DimScore(r["modifiers"]["unknown_defaults"]["buzz"], "热度信号缺失，按保守值计", True)
    score, reason = max(cands, key=lambda t: t[0])
    return DimScore(score, reason)


def score_date_vibe(e: Event, r: dict) -> DimScore:
    cl = r["dimensions"]["date_vibe"]["checklist"]
    checks = {
        "conversation_friendly": (e.noise_level in ("quiet", "moderate")) if e.noise_level else None,
        "has_seating": e.has_seating,
        "photogenic": e.photogenic,
        "evening_or_weekend": e.evening_or_weekend,
        "nearby_food_options": e.nearby_food_options,
    }
    known = {k: v for k, v in checks.items() if v is not None}
    if len(known) < 3:
        return DimScore(r["modifiers"]["unknown_defaults"]["date_vibe"], "氛围信号不足，按保守值计", True)
    total = sum(cl[k] for k, v in known.items() if v)
    hit = [k for k, v in known.items() if v]
    label = {"conversation_friendly": "可交谈", "has_seating": "有座位",
             "photogenic": "出片", "evening_or_weekend": "时段合适",
             "nearby_food_options": "可续摊"}
    reason = "满足：" + "、".join(label[k] for k in hit) if hit else "氛围项全部未满足"
    return DimScore(total, reason, missing=len(known) < 5)


def score_interactivity(e: Event, r: dict) -> DimScore:
    a = r["dimensions"]["interactivity"]["anchors"]
    if e.interaction and e.interaction in a:
        zh = {"co_create": "双人协作完成", "hands_on": "动手参与",
              "light_participate": "可走动/试吃/选择", "spectate_shared": "共同观看且可交流",
              "spectate_silent": "需安静观看", "none": "无互动"}
        return DimScore(a[e.interaction], zh[e.interaction])
    return DimScore(r["modifiers"]["unknown_defaults"]["interactivity"], "互动形式未知，按保守值计", True)


def score_value(e: Event, r: dict) -> DimScore:
    price = e.price_per_person
    hours = e.duration_hours
    if price is None or not hours:
        return DimScore(r["modifiers"]["unknown_defaults"]["value"], "价格或时长缺失，按保守值计", True)
    per_hour = price / hours
    s = _band_max(r["dimensions"]["value"]["anchors_aud_per_hour"], per_hour)
    return DimScore(s, f"A${price:.0f} / {hours:g}h ≈ A${per_hour:.0f} 每小时")


def score_transit(e: Event, r: dict) -> DimScore:
    d = r["dimensions"]["transit"]
    if e.transit_minutes is None:
        return DimScore(r["modifiers"]["unknown_defaults"]["transit"], "通勤数据缺失，按保守值计", True)
    transfers = e.transit_transfers if e.transit_transfers is not None else 1
    score = 0
    for band in d["anchors"]:
        if e.transit_minutes <= band["max_minutes"] and transfers <= band["max_transfers"]:
            score = band["score"]
            break
    reason = f"公交 {e.transit_minutes} 分钟 / 换乘 {transfers} 次"
    wp = d["walk_penalty"]
    if e.walk_minutes is not None and e.walk_minutes > wp["over_minutes"]:
        score -= wp["penalty"]
        reason += f"，末端步行 {e.walk_minutes} 分钟 -{wp['penalty']}"
    return DimScore(score, reason)


SCORERS = {
    "freshness": score_freshness,
    "scarcity": score_scarcity,
    "buzz": score_buzz,
    "date_vibe": score_date_vibe,
    "interactivity": score_interactivity,
    "value": score_value,
    "transit": score_transit,
}

ZH = {"freshness": "新鲜感", "scarcity": "稀缺性", "buzz": "讨论热度",
      "date_vibe": "约会氛围", "interactivity": "互动性",
      "value": "性价比", "transit": "交通便利度"}


def score_event(event: Event, rubric: dict) -> Event:
    """对单个活动打分。除 event 与 rubric 外不接受任何上下文。"""
    weights = rubric["weights"]
    detail: dict[str, Any] = {}
    weighted = 0.0
    missing = 0
    for dim in DIMENSIONS:
        ds = SCORERS[dim](event, rubric)
        detail[dim] = ds.as_dict()
        detail[dim]["label"] = ZH[dim]
        detail[dim]["weight"] = weights[dim]
        weighted += ds.score * weights[dim]
        missing += int(ds.missing)

    total = weighted * 10.0                      # 0–100
    mods: dict[str, float] = {}
    if event.solo_oriented:
        mods["solo_oriented"] = rubric["modifiers"]["solo_oriented_multiplier"]
    if event.community_venue:
        # 社区中心/图书馆场地：活动本身合法，但约会观感打折（v1.2.0）
        mods["community_venue"] = rubric["modifiers"].get("community_venue_multiplier", 1.0)
    if mods:
        detail["_modifier"] = mods
        for m in mods.values():
            total *= m

    event.scores = detail
    event.total = round(total, 1)
    event.completeness = round(1 - missing / len(DIMENSIONS), 2)
    return event


def passes_threshold(event: Event, rubric: dict) -> tuple[bool, str]:
    th = rubric["thresholds"]
    if event.completeness < th["min_completeness"]:
        return False, f"信息完整度 {event.completeness:.0%} < {th['min_completeness']:.0%}"
    for dim, floor in th["floors"].items():
        if event.scores[dim]["score"] < floor:
            return False, f"{ZH[dim]} {event.scores[dim]['score']} 低于下限 {floor}"
    if event.total < th["recommend_total"]:
        return False, f"总分 {event.total} < 阈值 {th['recommend_total']}"
    return True, "通过"
