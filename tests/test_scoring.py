"""固定锚点评分引擎的不变式测试。不依赖 pytest，可直接：

    python tests/test_scoring.py
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from scout.models import Event  # noqa: E402
from scout.scoring import passes_threshold, score_event  # noqa: E402

RUBRIC = yaml.safe_load((ROOT / "rubric.yaml").read_text(encoding="utf-8"))


def _make_strong_event() -> Event:
    e = Event(title="首次登陆悉尼的高分活动", category="film",
              start=datetime.now() + timedelta(days=5))
    e.first_time_in_city = True
    e.window_days = 2
    e.media_mentions = 9
    e.noise_level = "quiet"
    e.has_seating = True
    e.photogenic = True
    e.evening_or_weekend = True
    e.nearby_food_options = True
    e.interaction = "co_create"
    e.price_per_person = 5
    e.duration_hours = 2
    e.transit_minutes = 10
    e.transit_transfers = 0
    return e


class TestScoringInvariants(unittest.TestCase):
    def test_score_is_independent_of_batch(self):
        """同一活动放进"强批次"还是"弱批次"打分，结果必须完全一致 ——
        score_event(event, rubric) 的签名里没有批次/排名参数，结构上不可能相对化。
        """
        target_in_weak_batch = _make_strong_event()
        target_in_strong_batch = _make_strong_event()

        weak_peers = [Event(title=f"弱活动{i}") for i in range(3)]
        strong_peers = [_make_strong_event() for _ in range(5)]

        for peer in weak_peers:
            score_event(peer, RUBRIC)
        score_event(target_in_weak_batch, RUBRIC)

        for peer in strong_peers:
            score_event(peer, RUBRIC)
        score_event(target_in_strong_batch, RUBRIC)

        self.assertEqual(target_in_weak_batch.total, target_in_strong_batch.total)
        self.assertEqual(target_in_weak_batch.scores, target_in_strong_batch.scores)

    def test_missing_signals_use_conservative_defaults_and_lower_completeness(self):
        e = Event(title="信号缺失活动", category="experience", start=datetime.now() + timedelta(days=3))
        score_event(e, RUBRIC)
        self.assertLess(e.completeness, 1.0)
        for dim in ("freshness", "scarcity", "buzz", "interactivity", "value", "transit"):
            self.assertTrue(e.scores[dim]["missing"])

    def test_floor_veto_blocks_high_total(self):
        e = _make_strong_event()
        e.has_seating = False
        e.photogenic = False
        e.nearby_food_options = False
        e.noise_level = "loud"          # date_vibe 只剩 evening_or_weekend 一项 = 2 分，< 下限 5
        score_event(e, RUBRIC)
        ok, reason = passes_threshold(e, RUBRIC)
        self.assertFalse(ok)
        self.assertIn("低于下限", reason)

    def test_solo_oriented_modifier_applies_fixed_discount(self):
        baseline = _make_strong_event()
        score_event(baseline, RUBRIC)

        solo = _make_strong_event()
        solo.solo_oriented = True
        score_event(solo, RUBRIC)

        expected = round(baseline.total * RUBRIC["modifiers"]["solo_oriented_multiplier"], 1)
        self.assertEqual(solo.total, expected)


if __name__ == "__main__":
    unittest.main()
