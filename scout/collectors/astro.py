"""天文 / 自然景观采集器：读取 sources.yaml 里内嵌的静态 `events` 列表（每年手动更新）。"""
from __future__ import annotations

from ..models import Event
from . import collector
from ._common import parse_flat_event


@collector("astro")
def astro_collector(source: dict, cfg: dict) -> list[Event]:
    out: list[Event] = []
    for item in source.get("events", []):
        item = {**item, "category": item.get("category", source.get("category_hint", "nature"))}
        out.append(parse_flat_event(item, source))
    return out
