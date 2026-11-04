"""外部爬虫接口测试：契约解析、信号推断、收件箱/命令两种交货方式、插件加载。

不依赖 pytest，可直接：

    python tests/test_external.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from scout.collectors import REGISTRY, get_collector, load_plugins  # noqa: E402
from scout.collectors.external import (SIGNAL_FIELDS, FeedError, load_records,  # noqa: E402
                                       normalize_category, parse_record)
from scout.config import merge_local_sources  # noqa: E402
from scout.geo import home_coords  # noqa: E402
from scout.scoring import passes_threshold, score_event  # noqa: E402
from scout.verify import verify_event  # noqa: E402

SRC = {"id": "test_feed", "category_hint": "experience"}
CFG = {"home_suburb": "Surry Hills", "categories": ["performance", "experience", "food", "market"],
       "lookahead_days": 21, "budget_per_person_aud": 80, "max_distance_km": 40}
RUBRIC = yaml.safe_load((ROOT / "rubric.yaml").read_text(encoding="utf-8"))


def _next_saturday(hour: int) -> datetime:
    now = datetime.now()
    days = (5 - now.weekday()) % 7 or 7
    return (now + timedelta(days=days)).replace(hour=hour, minute=30, second=0, microsecond=0)


def good_record(**over) -> dict:
    start = _next_saturday(19)
    rec = {
        "title": "Harbour jazz night", "start": start.isoformat(),
        "end": (start + timedelta(hours=2)).isoformat(),
        "url": "https://example.com/e/1", "venue": "Example Hall", "suburb": "Barangaroo",
        "lat": -33.8637, "lng": 151.2010, "price": "From $35", "category": ["Music"],
        "description": "Outdoor seats facing the harbour.",
        "dates": [start.date().isoformat(), (start + timedelta(days=1)).date().isoformat()],
    }
    rec.update(over)
    return rec


class TestContract(unittest.TestCase):
    def test_minimal_record_parses(self):
        e = parse_record({"title": "A", "start": "2030-01-01T19:00", "url": "https://x.io"}, SRC, CFG)
        self.assertEqual(e.title, "A")
        self.assertEqual(e.category, "experience")          # 没给类别 → category_hint

    def test_required_fields_have_actionable_errors(self):
        for rec, field in [({"start": "2030-01-01", "url": "https://x.io"}, "title"),
                           ({"title": "A", "url": "https://x.io"}, "start"),
                           ({"title": "A", "start": "2030-01-01"}, "url")]:
            with self.assertRaises(FeedError) as cm:
                parse_record(rec, SRC, CFG)
            self.assertIn(field, str(cm.exception))

    def test_price_forms(self):
        for raw, want in [(0, 0.0), ("Free", 0.0), ("From $25", 25.0), ("$25 – $60", 25.0), (None, None)]:
            self.assertEqual(parse_record(good_record(price=raw), SRC, CFG).price_per_person, want)
        with self.assertRaises(FeedError):
            parse_record(good_record(price="ask at door"), SRC, CFG)

    def test_unknown_signal_rejected(self):
        with self.assertRaises(FeedError):
            parse_record(good_record(signals={"vibes": 10}), SRC, CFG)
        with self.assertRaises(FeedError):
            parse_record(good_record(signals={"noise_level": "deafening"}), SRC, CFG)

    def test_schema_and_code_agree_on_signals(self):
        schema = json.loads((ROOT / "docs" / "event.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(set(schema["properties"]["signals"]["properties"]), SIGNAL_FIELDS)


class TestInference(unittest.TestCase):
    def test_category_normalisation_prefers_exclusion(self):
        self.assertEqual(normalize_category(["Music"], "x"), "performance")
        self.assertEqual(normalize_category(["Markets & Fairs"], "x"), "market")
        # 「家庭音乐会」宁可判 family 被排除，也不能当普通演出推荐
        self.assertEqual(normalize_category(["Family", "Music"], "x"), "family")
        self.assertEqual(normalize_category(["Something odd"], "experience"), "experience")

    def test_geo_and_profile_fill_only_missing(self):
        e = parse_record(good_record(signals={"has_seating": False}), SRC, CFG)
        self.assertIsNotNone(e.distance_km)
        self.assertIsNotNone(e.transit_minutes)
        self.assertEqual(e.noise_level, "moderate")          # 来自 performance 画像
        self.assertFalse(e.has_seating)                      # 显式信号不被画像覆盖
        self.assertTrue(e.evening_or_weekend)
        self.assertAlmostEqual(e.duration_hours, 2.0)

    def test_home_coords_override(self):
        self.assertEqual(home_coords({"home_coords": [1, 2]}), (1.0, 2.0))

    def test_rich_record_reaches_threshold(self):
        e = parse_record(good_record(), SRC, CFG)
        verify_event(e, CFG)
        self.assertIsNone(e.exclude_reason)
        score_event(e, RUBRIC)
        ok, why = passes_threshold(e, RUBRIC)
        self.assertTrue(ok, f"{e.total} {why}")

    def test_kids_event_is_gated(self):
        e = parse_record(good_record(title="Kids science day (ages 5-10)", category="Family"), SRC, CFG)
        verify_event(e, CFG)
        self.assertIsNotNone(e.exclude_reason)


class TestDelivery(unittest.TestCase):
    def test_load_records_shapes(self):
        r = good_record()
        self.assertEqual(len(load_records(json.dumps([r, r]), "t")), 2)
        self.assertEqual(len(load_records(json.dumps({"events": [r]}), "t")), 1)
        self.assertEqual(len(load_records(json.dumps(r) + "\n" + json.dumps(r) + "\n", "t")), 2)
        with self.assertRaises(FeedError):
            load_records('{"title": "a"}\nnot json', "t")

    def test_inbox_mode_reads_files_skips_bad_and_stale(self):
        with tempfile.TemporaryDirectory() as d:
            inbox = Path(d) / "data" / "inbox"
            inbox.mkdir(parents=True)
            (inbox / "a.jsonl").write_text(
                json.dumps(good_record()) + "\n" + json.dumps({"title": "no url"}) + "\n",
                encoding="utf-8")
            old = inbox / "old.json"
            old.write_text(json.dumps([good_record(title="stale")]), encoding="utf-8")
            past = time.time() - 10 * 86400
            import os
            os.utime(old, (past, past))
            src = {"id": "inbox", "type": "external", "mode": "file",
                   "path": "data/inbox/*.json*", "max_age_hours": 96}
            got = get_collector("external")(src, {**CFG, "_data_dir": d})
            self.assertEqual([e.title for e in got], ["Harbour jazz night"])

    def test_command_mode_runs_example_scraper(self):
        src = {"id": "cmd", "type": "external", "mode": "command",
               "command": f'"{sys.executable}" "{ROOT / "examples" / "example_scraper.py"}"'}
        got = get_collector("external")(src, {**CFG, "_data_dir": str(ROOT)})
        self.assertEqual(len(got), 3)

    def test_plugin_autoload(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "_skipped.py").write_text("raise SystemExit('should not load')", encoding="utf-8")
            Path(d, "zz_test_plugin.py").write_text(
                "from scout.collectors import collector\n"
                "@collector('zz_test_plugin')\n"
                "def c(source, cfg):\n    return []\n", encoding="utf-8")
            Path(d, "broken.py").write_text("import does_not_exist\n", encoding="utf-8")
            loaded = load_plugins([Path(d)])
            self.assertEqual(loaded, ["zz_test_plugin.py"])
            self.assertIn("zz_test_plugin", REGISTRY)


class TestLocalSources(unittest.TestCase):
    def test_merge_overrides_by_id_and_appends_new(self):
        base = [{"id": "demo", "type": "fixtures", "enabled": True},
                {"id": "inbox", "type": "external", "enabled": True}]
        local = [{"id": "demo", "enabled": False},
                 {"id": "mine", "type": "my_plugin", "enabled": True},
                 {"id": "no_type"}]                      # 新 id 缺 type：忽略
        got = merge_local_sources(base, local)
        self.assertEqual([s["id"] for s in got], ["demo", "inbox", "mine"])
        self.assertFalse(got[0]["enabled"])
        self.assertEqual(got[0]["type"], "fixtures")     # 未覆盖的字段保留
        self.assertTrue(base[0]["enabled"])              # 不改动原列表


if __name__ == "__main__":
    unittest.main(verbosity=1)
