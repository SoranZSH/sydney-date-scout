"""Track B：基于行为数据的个性化（v1.3.0）。

目标：用「看了哪条、点了哪条链接、点过 👍/👎」这些行为信号，缓慢形成每个
用户自己的偏好画像。产品开源/发布后，每个用户的数据库都在自己机器上，
各自的推荐体系各自演化，互不干扰。

刻意保守的三条设计约束：
1. **只调批内排序，不改分数、不改阈值、不改剔除**。个性化加成最多把一个
   类别在排序里抬/压几名 —— rubric 的跨批次可比性和「不达标不推」底线不受影响。
2. **慢**：亲和度是平滑比率（加了伪计数），要几十次行为才会产生可感知的
   偏移，单次误点不会毁掉一个类别。
3. **可解释**：每个类别的亲和度可以直接打印出来看（run.py 里有入口）。

信号权重：👍=+2，复制方案=+1.5，点开链接=+1，查看详情=+0.3，👎=-2。
（复制约会方案≈「我真要发给对方」，是比点链接更强的意向信号，v1.3.1 加入。）
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from .store import Store

# 行为种类 → 正/负向权重
SIGNAL_WEIGHTS = {
    "like": 2.0,
    "copy_plan": 1.5,
    "open_link": 1.0,
    "view": 0.3,
    "dislike": -2.0,
}
PSEUDO_COUNT = 6.0      # 平滑伪计数：行为少时亲和度趋近 0（中立）
MAX_RANK_BONUS = 4.0    # 排序加成上限（相当于总分 ±4 的排序位移，只用于排序）


def category_affinity(store: "Store") -> dict[str, float]:
    """每个类别的亲和度 ∈ [-1, 1]。0 = 中立/数据不足。"""
    pos: dict[str, float] = {}
    neg: dict[str, float] = {}
    for row in store.behavior_counts():
        cat, kind, cnt = row["category"], row["kind"], row["cnt"]
        if not cat:
            continue
        w = SIGNAL_WEIGHTS.get(kind, 0.0)
        if w >= 0:
            pos[cat] = pos.get(cat, 0.0) + w * cnt
        else:
            neg[cat] = neg.get(cat, 0.0) + (-w) * cnt
    out: dict[str, float] = {}
    for cat in set(pos) | set(neg):
        p, n = pos.get(cat, 0.0), neg.get(cat, 0.0)
        out[cat] = (p - n) / (p + n + PSEUDO_COUNT)
    return out


def rank_bonuses(store: "Store", enabled: bool = True) -> dict[str, float]:
    """类别 → 排序加成（只加在排序键上，绝不写回 event.total）。"""
    if not enabled:
        return {}
    return {cat: round(MAX_RANK_BONUS * aff, 2)
            for cat, aff in category_affinity(store).items() if abs(aff) > 0.01}
