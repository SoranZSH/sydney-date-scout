"""离线样例采集器：从本地 YAML 文件读事件，供 --dry-run / 无网络环境跑通全链路。"""
from __future__ import annotations

from pathlib import Path

import yaml

from ..models import Event
from . import collector
from ._common import parse_flat_event


@collector("fixtures")
def fixtures_collector(source: dict, cfg: dict) -> list[Event]:
    path = Path(cfg["_base_dir"]) / source["path"]
    if not path.exists():
        raise FileNotFoundError(f"fixtures 源 {source['id']} 指向的文件不存在: {path}")
    with open(path, "r", encoding="utf-8") as f:
        items = yaml.safe_load(f) or []
    return [parse_flat_event(item, source) for item in items]
