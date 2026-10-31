"""v1.3.0 双轨学习测试：Track A 内容判别模型 + Track B 个性化。

不依赖 pytest，可直接：

    python tests/test_learning.py
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scout import datefit  # noqa: E402
from scout.personalize import MAX_RANK_BONUS, category_affinity, rank_bonuses  # noqa: E402
from scout.store import Store  # noqa: E402

GOOD = [
    "Vivid light walk along the harbour", "Rooftop jazz night with cocktails",
    "Pottery class for two in Newtown", "Outdoor cinema under the stars",
    "Wine and cheese tasting evening", "Sunset kayak tour Sydney harbour",
    "Live music at intimate laneway bar", "Night noodle markets street food",
    "Candlelight concert string quartet", "Art after hours gallery evening",
    "Harbour dinner cruise twilight", "Salsa dancing beginners social night",
    "Chocolate dessert degustation", "Open air theatre in the gardens",
    "Whale watching coastal cruise", "Cocktail masterclass for couples",
]
BAD = [
    "Volunteer induction session city farm", "Storytime for toddlers at library",
    "School holidays robot maze kids", "Seniors gentle exercise morning",
    "Community chats neighbourhood centre", "Homework help club students",
    "Playgroup for babies and parents", "Citizenship ceremony information",
    "Youth creative lab teens", "Family fun day face painting",
    "Crochet club weekly meetup", "English conversation class migrants",
    "Working bee garden volunteers", "Kids pixel art maker day",
    "Over 55s social morning tea", "Justice of the peace service",
]


class TestDatefit(unittest.TestCase):
    def _trained(self):
        samples = ([({"title": t, "category": "experience"}, 1) for t in GOOD]
                   + [({"title": t, "category": "experience"}, 0) for t in BAD])
        return datefit.train(samples)

    def test_separates_good_from_bad(self):
        m = self._trained()
        self.assertGreaterEqual(m["train_acc"], 0.9)
        good_p = datefit.predict(m, {"title": "Harbour sunset wine cruise"})
        bad_p = datefit.predict(m, {"title": "Toddlers storytime volunteers library"})
        self.assertGreater(good_p, bad_p)

    def test_deterministic(self):
        m1, m2 = self._trained(), self._trained()
        self.assertEqual(m1["weights"], m2["weights"])
        self.assertEqual(m1["bias"], m2["bias"])

    def test_save_load_roundtrip(self):
        m = self._trained()
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "models" / "datefit.json"
            datefit.save_model(m, p)
            cfg = {"_data_dir": t, "_base_dir": t}
            loaded = datefit.load_model(cfg)
            self.assertIsNotNone(loaded)
            x = {"title": "Rooftop jazz cocktails"}
            self.assertAlmostEqual(datefit.predict(m, x), datefit.predict(loaded, x), places=6)

    def test_disabled_by_config(self):
        with tempfile.TemporaryDirectory() as t:
            cfg = {"_data_dir": t, "_base_dir": t, "datefit": {"enabled": False}}
            self.assertIsNone(datefit.load_model(cfg))


class TestPersonalize(unittest.TestCase):
    def _store(self) -> Store:
        self.tmp = tempfile.TemporaryDirectory()
        return Store(Path(self.tmp.name) / "t.db")

    def test_affinity_direction_and_bounds(self):
        store = self._store()
        for _ in range(8):
            store.log_behavior("open_link", "展览A", "Gallery", category="exhibition")
        store.add_feedback("展览A", "Gallery", "like", category="exhibition")
        for _ in range(4):
            store.add_feedback("讲座B", "Hall", "dislike", category="experience")
        aff = category_affinity(store)
        self.assertGreater(aff["exhibition"], 0)
        self.assertLess(aff["experience"], 0)
        for a in aff.values():
            self.assertTrue(-1 <= a <= 1)
        for b in rank_bonuses(store).values():
            self.assertLessEqual(abs(b), MAX_RANK_BONUS)

    def test_slow_by_design(self):
        """单次行为不应产生明显偏移（平滑伪计数在起作用）。"""
        store = self._store()
        store.log_behavior("view", "某活动", "Somewhere", category="market")
        aff = category_affinity(store)
        self.assertLess(abs(aff.get("market", 0)), 0.1)

    def test_disabled_returns_empty(self):
        store = self._store()
        store.add_feedback("x", "y", "like", category="food")
        self.assertEqual(rank_bonuses(store, enabled=False), {})

    def test_training_samples_join(self):
        store = self._store()
        from scout.models import Event
        from scout.scoring import score_event
        import yaml
        rubric = yaml.safe_load((ROOT / "rubric.yaml").read_text(encoding="utf-8"))
        e = Event(title="Pottery for two", description="hands-on wheel class",
                  category="experience", venue_name="Studio A")
        e.uid = "uid-1"
        store.save_event(score_event(e, rubric))
        store.add_feedback("Pottery for two", "Studio A", "like",
                           uid="uid-1", category="experience")
        samples = store.training_samples()
        self.assertEqual(len(samples), 1)
        fields, label = samples[0]
        self.assertEqual(label, 1)
        self.assertIn("hands-on", fields.get("description", ""))


if __name__ == "__main__":
    unittest.main(verbosity=1)
