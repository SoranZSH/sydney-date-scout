"""地理工具：起点坐标、球面距离、公交耗时估算。

原先写在 City of Sydney 采集器内部；外部爬虫接口（collectors/external.py）
同样需要「给一个场馆经纬度 → 算出距离与交通分」，所以抽成独立模块，
任何数据源都能复用。

交通耗时是**估算**，不是真实行程查询 —— 接入 Transport for NSW 的
Trip Planner API 后应替换 `estimate_transit`（见 README「已知边界」）。
"""
from __future__ import annotations

import math
from typing import Optional

# 起点坐标表，key 用小写 suburb 名。不在表里的 suburb 可在 config.yaml 用
# `home_coords: [lat, lng]` 直接指定（优先级更高，也是换城市时的做法）。
SUBURB_COORDS: dict[str, tuple[float, float]] = {
    "surry hills": (-33.8845, 151.2118),
    "sydney": (-33.8688, 151.2093),
    "cbd": (-33.8688, 151.2093),
    "newtown": (-33.8960, 151.1795),
    "redfern": (-33.8931, 151.2043),
    "darlinghurst": (-33.8790, 151.2189),
    "pyrmont": (-33.8700, 151.1950),
    "chippendale": (-33.8886, 151.1985),
    "glebe": (-33.8796, 151.1856),
    "ultimo": (-33.8797, 151.1975),
    "barangaroo": (-33.8637, 151.2010),
    "the rocks": (-33.8599, 151.2090),
    "burwood": (-33.8774, 151.1037),
    "chatswood": (-33.7969, 151.1803),
    "parramatta": (-33.8150, 151.0011),
    "bondi": (-33.8915, 151.2767),
}
DEFAULT_HOME = SUBURB_COORDS["sydney"]


def home_coords(cfg: dict) -> tuple[float, float]:
    """起点坐标：config 的 `home_coords` > `home_suburb` 查表 > 悉尼市中心。"""
    hc = cfg.get("home_coords")
    if isinstance(hc, (list, tuple)) and len(hc) == 2:
        return float(hc[0]), float(hc[1])
    return SUBURB_COORDS.get(str(cfg.get("home_suburb", "")).strip().lower(), DEFAULT_HOME)


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    r = 6371.0
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = (math.sin((la2 - la1) / 2) ** 2
         + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2)
    return 2 * r * math.asin(math.sqrt(h))


def estimate_transit(distance_km: float) -> tuple[float, int]:
    """由直线距离估算公交门到门耗时与换乘次数。

    系数取悉尼市区经验值：起步 12 分钟（步行到站 + 候车），此后每公里约 3.2 分钟。
    宁可估得保守一点，让交通维度不至于虚高。
    """
    minutes = round(12 + distance_km * 3.2)
    if distance_km <= 3:
        transfers = 0
    elif distance_km <= 10:
        transfers = 1
    elif distance_km <= 25:
        transfers = 2
    else:
        transfers = 3
    return float(minutes), transfers


def distance_and_transit(cfg: dict, lat: Optional[float], lng: Optional[float]
                         ) -> tuple[Optional[float], Optional[float], Optional[int]]:
    """场馆经纬度 → (距离 km, 公交分钟, 换乘次数)；缺坐标时全部返回 None。"""
    if lat is None or lng is None:
        return None, None, None
    d = round(haversine_km(home_coords(cfg), (float(lat), float(lng))), 1)
    m, t = estimate_transit(d)
    return d, m, t
