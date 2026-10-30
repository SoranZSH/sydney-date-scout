"""事件（活动）数据模型。

字段命名与 sources.yaml 里 `defaults` / `signals` 的 key 保持一致，
采集器只需把源数据映射成这些字段即可参与打分。
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import datetime
from typing import Any, Optional


@dataclass
class Event:
    # ---------- 基础信息（采集层填充）----------
    uid: str = ""
    title: str = ""
    description: str = ""
    category: str = ""                     # film/performance/exhibition/festival/market/
                                            # food/venue/experience/sport/nature/sale/daytrip/esports/anime
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    venue_name: str = ""
    suburb: str = ""
    source_url: str = ""
    booking_url: Optional[str] = None
    source_id: str = ""                    # 采集自哪个 sources.yaml 条目

    # ---------- 价格 / 时长 ----------
    price_per_person: Optional[float] = None
    duration_hours: Optional[float] = None

    # ---------- 稀缺性 ----------
    window_days: Optional[int] = None
    total_sessions: Optional[int] = None
    ticket_status: Optional[str] = None    # available | limited | sold_out

    # ---------- 新鲜感 ----------
    first_time_in_city: bool = False
    is_first_edition: bool = False
    new_opening_days: Optional[int] = None
    is_annual_repeat: bool = False
    has_new_content: bool = False
    is_permanent: bool = False

    # ---------- 讨论热度 ----------
    media_mentions: Optional[int] = None
    social_signal: Optional[int] = None

    # ---------- 约会氛围 ----------
    noise_level: Optional[str] = None      # quiet | moderate | loud
    has_seating: Optional[bool] = None
    photogenic: Optional[bool] = None
    evening_or_weekend: Optional[bool] = None
    nearby_food_options: Optional[bool] = None

    # ---------- 互动性 ----------
    interaction: Optional[str] = None      # co_create/hands_on/light_participate/spectate_shared/spectate_silent/none

    # ---------- 交通 ----------
    transit_minutes: Optional[float] = None
    transit_transfers: Optional[int] = None
    walk_minutes: Optional[float] = None
    distance_km: Optional[float] = None

    # ---------- 降权 / 排除 ----------
    solo_oriented: bool = False
    exclude_reason: Optional[str] = None   # verify 阶段写入，非 None 即剔除

    # ---------- 去重合并证据 ----------
    evidence: list[str] = field(default_factory=list)   # 独立来源 id 列表，去重时合并

    # ---------- 打分 / 方案（下游阶段写入）----------
    scores: dict[str, Any] = field(default_factory=dict)
    total: float = 0.0
    completeness: float = 0.0
    audience: list[str] = field(default_factory=list)    # 「适合对象」标签
    plan: dict[str, Any] = field(default_factory=dict)   # 约会方案

    def merge_signal_defaults(self, defaults: dict[str, Any]) -> None:
        """采集器可传入源级别的 `defaults`（如场馆固定噪音水平），
        只填充事件里尚未设置（None/False/""）的字段，不覆盖真实抓到的数据。"""
        valid = {f.name for f in fields(self)}
        for k, v in (defaults or {}).items():
            if k not in valid:
                continue
            cur = getattr(self, k)
            if cur in (None, "", False, []):
                setattr(self, k, v)

    def as_payload(self) -> dict[str, Any]:
        """给 store 落库 / explain 命令用的可 JSON 序列化视图。"""
        d = {
            "uid": self.uid, "title": self.title, "description": self.description,
            "category": self.category,
            "start": self.start.isoformat() if self.start else None,
            "end": self.end.isoformat() if self.end else None,
            "venue_name": self.venue_name, "suburb": self.suburb,
            "source_url": self.source_url, "booking_url": self.booking_url,
            "price_per_person": self.price_per_person, "duration_hours": self.duration_hours,
            "total": self.total, "completeness": self.completeness,
            "scores": self.scores, "audience": self.audience, "plan": self.plan,
            "evidence": self.evidence,
        }
        return d
