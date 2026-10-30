"""v1.2.0 行为测试：约会适配闸门、社区场地降权、系列级防重复、品类多样性。

不依赖 pytest，可直接：

    python tests/test_date_fit.py
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from scout.models import Event  # noqa: E402
from scout.scoring import score_event  # noqa: E402
from scout.store import Store, series_key  # noqa: E402
from scout.verify import verify_event  # noqa: E402

RUBRIC = yaml.safe_load((ROOT / "rubric.yaml").read_text(encoding="utf-8"))
CFG = {"categories": ["experience", "market", "exhibition"], "lookahead_days": 21,
       "require_price": True, "budget_per_person_aud": 120}


def _ok_event(**kw) -> Event:
    e = Event(title="Pottery for two", category="experience",
              start=datetime.now() + timedelta(days=5),
              source_url="https://example.com/e", price_per_person=30,
              venue_name="Studio A", suburb="Newtown")
    for k, v in kw.items():
        setattr(e, k, v)
    return e


class TestDateGate(unittest.TestCase):
    def test_kids_age_range_excluded(self):
        for title in ("Maker & Creator 8-12yrs", "Singing and stories, 0 to 5 years",
                      "School holidays: Pixel art maker day 10-15yrs"):
            e = verify_event(_ok_event(title=title), CFG)
            self.assertIsNotNone(e.exclude_reason, title)
            self.assertIn("不适合约会", e.exclude_reason)

    def test_volunteer_and_seniors_excluded(self):
        for title in ("Sydney City Farm new volunteer induction session",
                      "Wesley School for Seniors: Open Week",
                      "OzHarvest Market Volunteer Training"):
            e = verify_event(_ok_event(title=title), CFG)
            self.assertIsNotNone(e.exclude_reason, title)

    def test_family_and_community_categories_hard_excluded(self):
        for cat in ("family", "community"):
            e = verify_event(_ok_event(category=cat), CFG)
            self.assertIsNotNone(e.exclude_reason, cat)

    def test_normal_date_event_passes(self):
        e = verify_event(_ok_event(), CFG)
        self.assertIsNone(e.exclude_reason)

    def test_custom_exclude_keywords(self):
        cfg = dict(CFG, exclude_keywords=["pub crawl"])
        e = verify_event(_ok_event(title="CBD Pub Crawl Night"), cfg)
        self.assertIsNotNone(e.exclude_reason)

    def test_community_venue_flag_and_downweight(self):
        e = verify_event(_ok_event(venue_name="Pyrmont Community Centre"), CFG)
        self.assertIsNone(e.exclude_reason)       # 不剔除
        self.assertTrue(e.community_venue)        # 但要降权
        scored = score_event(e, RUBRIC)
        twin = score_event(_ok_event(venue_name="Studio A"), RUBRIC)
        factor = RUBRIC["modifiers"]["community_venue_multiplier"]
        self.assertAlmostEqual(scored.total, round(twin.total * factor, 1), delta=0.11)


class TestSeriesResend(unittest.TestCase):
    def _store(self) -> Store:
        self.tmp = tempfile.TemporaryDirectory()
        return Store(Path(self.tmp.name) / "t.db")

    def test_weekly_class_blocked_within_cooldown(self):
        """同一门课下一场时间变了（uid 变了），系列冷却仍应拦住它。"""
        store = self._store()
        first = _ok_event(title="Creative writing class",
                          venue_name="Cliff Noble Community Centre")
        first.uid = "uid-week1"
        store.create_batch([first], "1.2.0")

        nxt = _ok_event(title="Creative writing class",
                        venue_name="Cliff Noble Community Centre",
                        start=datetime.now() + timedelta(days=12))
        nxt.uid = "uid-week2"                     # 新场次 = 新 uid，旧逻辑放行
        self.assertFalse(store.should_send(nxt, resend_after_days=30))

    def test_series_lifetime_cap(self):
        store = self._store()
        for i in range(3):
            e = _ok_event(title="Paddington Markets", venue_name="")
            e.uid = f"uid-{i}"
            store.create_batch([e], "1.2.0", at=datetime.now() - timedelta(days=200 - i * 60))
        e = _ok_event(title="Paddington Markets", venue_name="")
        e.uid = "uid-new"
        self.assertFalse(store.should_send(e, resend_after_days=30, series_max_sends=3))
        self.assertTrue(store.should_send(e, resend_after_days=30, series_max_sends=99))

    def test_dislike_blocks_forever(self):
        store = self._store()
        store.add_feedback("Soapbox orators", "The Domain", "dislike")
        e = _ok_event(title="Soapbox orators", venue_name="The Domain")
        e.uid = "uid-x"
        self.assertFalse(store.should_send(e, resend_after_days=0, series_max_sends=99))

    def test_fresh_event_passes(self):
        store = self._store()
        e = _ok_event(title="Vivid light walk", venue_name="Circular Quay")
        e.uid = "uid-v"
        self.assertTrue(store.should_send(e, resend_after_days=30))

    def test_series_key_normalises(self):
        self.assertEqual(series_key("Guided Walks", "The  Domain"),
                         series_key("guided walks", "the  domain"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
