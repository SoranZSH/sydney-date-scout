"""SQLite 存储：事件缓存、推送批次与历史、运行间隔守卫、跨批次 resend 判断。

「批次(batch)」是 GUI 的核心概念：每次真正产生推荐就落一条 batch，
被推荐的活动挂在该 batch 下。于是：
    当前推荐 = 最新一个 batch 的活动
    历史推荐 = 按 batch 倒序浏览
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .models import Event

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    uid TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    rubric_version TEXT NOT NULL,
    item_count INTEGER NOT NULL,
    top_score REAL
);
CREATE TABLE IF NOT EXISTS sent_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER,
    uid TEXT NOT NULL,
    title TEXT NOT NULL,
    total REAL NOT NULL,
    rubric_version TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    payload TEXT,
    sent_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    series_key TEXT NOT NULL,
    uid TEXT,
    title TEXT,
    category TEXT,
    verdict TEXT NOT NULL,              -- like | dislike
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS behavior (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,                 -- view | open_link（like/dislike 记在 feedback）
    series_key TEXT NOT NULL,
    uid TEXT,
    title TEXT,
    category TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sent_uid ON sent_history(uid);
CREATE INDEX IF NOT EXISTS idx_sent_batch ON sent_history(batch_id);
CREATE INDEX IF NOT EXISTS idx_feedback_series ON feedback(series_key);
"""


def _fingerprint(e: Event) -> str:
    # 时间或价格变动即视为「新版本」，会重置 resend 冷却。
    return f"{e.start.isoformat() if e.start else ''}|{e.price_per_person}"


def _norm(text: str) -> str:
    return "".join(ch.lower() for ch in (text or "") if ch.isalnum() or ch.isspace()).strip()


def series_key(title: str, venue: str) -> str:
    """同一「系列」的稳定标识：标题 + 场地（都归一化后哈希）。

    v1.2.0 修复的核心 bug：uid 用「标题+开始时间」生成，每周重复的课程
    下一场时间一变就是新 uid，30 天冷却形同虚设（同一门写作课三个月被推 7 次）。
    冷却与推送次数上限都应该按「系列」算，与某一场的具体时间无关。
    """
    return hashlib.sha1(f"{_norm(title)}|{_norm(venue)}".encode("utf-8")).hexdigest()[:16]


class Store:
    def __init__(self, db_path: str | Path):
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self._migrate()
        self.db.commit()

    def _migrate(self) -> None:
        """给早期版本建的库补上后加的列，避免升级后打不开旧数据。"""
        cols = {r["name"] for r in self.db.execute("PRAGMA table_info(sent_history)")}
        for col in ("batch_id INTEGER", "payload TEXT", "series_key TEXT"):
            name = col.split()[0]
            if name not in cols:
                self.db.execute(f"ALTER TABLE sent_history ADD COLUMN {col}")

        fb_cols = {r["name"] for r in self.db.execute("PRAGMA table_info(feedback)")}
        if fb_cols and "category" not in fb_cols:
            self.db.execute("ALTER TABLE feedback ADD COLUMN category TEXT")

        # v1.2.0：给历史推送记录回填 series_key，这样「同一系列最多推 N 次」
        # 从升级那一刻起就把过去 22 批次的重复历史算进去，而不是从零开始。
        rows = self.db.execute(
            "SELECT id, title, payload FROM sent_history WHERE series_key IS NULL"
        ).fetchall()
        for r in rows:
            venue = ""
            if r["payload"]:
                try:
                    venue = json.loads(r["payload"]).get("venue_name") or ""
                except (json.JSONDecodeError, AttributeError):
                    venue = ""
            self.db.execute("UPDATE sent_history SET series_key=? WHERE id=?",
                            (series_key(r["title"], venue), r["id"]))

    def close(self) -> None:
        self.db.close()

    # ---------- 运行间隔守卫 ----------
    def record_run(self, at: Optional[datetime] = None) -> None:
        self.db.execute("INSERT INTO runs (started_at) VALUES (?)",
                        ((at or datetime.now()).isoformat(),))
        self.db.commit()

    def last_run_at(self) -> Optional[datetime]:
        row = self.db.execute("SELECT started_at FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        return datetime.fromisoformat(row["started_at"]) if row else None

    # ---------- 事件缓存（explain 用）----------
    def save_event(self, e: Event) -> None:
        now = datetime.now().isoformat()
        fp = _fingerprint(e)
        payload = json.dumps(e.as_payload(), ensure_ascii=False)
        existing = self.db.execute("SELECT uid FROM events WHERE uid=?", (e.uid,)).fetchone()
        if existing:
            self.db.execute(
                "UPDATE events SET payload=?, fingerprint=?, last_seen=? WHERE uid=?",
                (payload, fp, now, e.uid),
            )
        else:
            self.db.execute(
                "INSERT INTO events (uid, payload, fingerprint, first_seen, last_seen) VALUES (?,?,?,?,?)",
                (e.uid, payload, fp, now, now),
            )
        self.db.commit()

    # ---------- 跨批次去重 / resend_after_days ----------
    def should_send(self, e: Event, resend_after_days: int,
                    series_max_sends: int = 3) -> bool:
        """三道闸，任何一道不过就不再推：

        1. 用户点过「不再推这类」的系列 —— 永久不推。
        2. 系列级冷却与上限：同一系列（标题+场地）在 resend_after_days 内
           只推一次，累计最多推 series_max_sends 次。周期性课程/市集每场
           时间都不同，按 uid 永远查不到历史，必须按系列拦。
        3. uid 级：同一场活动时间/价格没变就遵守冷却（保底，兼容旧数据）。
        """
        skey = series_key(e.title, e.venue_name)

        if self.db.execute(
            "SELECT 1 FROM feedback WHERE series_key=? AND verdict='dislike' LIMIT 1",
            (skey,),
        ).fetchone():
            return False

        series_rows = self.db.execute(
            "SELECT sent_at FROM sent_history WHERE series_key=? ORDER BY sent_at DESC",
            (skey,),
        ).fetchall()
        if series_rows:
            if series_max_sends and len(series_rows) >= series_max_sends:
                return False
            last = datetime.fromisoformat(series_rows[0]["sent_at"])
            if (datetime.now() - last).days < resend_after_days:
                return False

        row = self.db.execute(
            "SELECT fingerprint, sent_at FROM sent_history WHERE uid=? ORDER BY sent_at DESC LIMIT 1",
            (e.uid,),
        ).fetchone()
        if not row:
            return True
        if row["fingerprint"] != _fingerprint(e):
            return True  # 时间/价格变动，重置冷却
        last_sent = datetime.fromisoformat(row["sent_at"])
        return (datetime.now() - last_sent).days >= resend_after_days

    # ---------- 用户反馈（v1.2.0）----------
    def add_feedback(self, title: str, venue: str, verdict: str,
                     uid: Optional[str] = None, category: Optional[str] = None) -> None:
        self.db.execute(
            "INSERT INTO feedback (series_key, uid, title, category, verdict, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (series_key(title, venue), uid, title, category, verdict,
             datetime.now().isoformat()),
        )
        self.db.commit()

    def feedback_for(self, title: str, venue: str) -> Optional[str]:
        """返回该系列最近一次反馈（like/dislike），没有则 None。"""
        row = self.db.execute(
            "SELECT verdict FROM feedback WHERE series_key=? ORDER BY id DESC LIMIT 1",
            (series_key(title, venue),),
        ).fetchone()
        return row["verdict"] if row else None

    # ---------- 行为日志 / 学习数据（v1.3.0）----------
    def log_behavior(self, kind: str, title: str, venue: str,
                     uid: Optional[str] = None, category: Optional[str] = None) -> None:
        self.db.execute(
            "INSERT INTO behavior (kind, series_key, uid, title, category, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (kind, series_key(title, venue), uid, title, category,
             datetime.now().isoformat()),
        )
        self.db.commit()

    def behavior_counts(self) -> list[sqlite3.Row]:
        """类别 × 行为种类 的计数（feedback 的 like/dislike 也并进来），
        给 personalize.category_affinity 用。"""
        return self.db.execute(
            "SELECT category, kind, COUNT(*) AS cnt FROM ("
            "  SELECT category, kind FROM behavior"
            "  UNION ALL"
            "  SELECT category, verdict AS kind FROM feedback"
            ") GROUP BY category, kind",
        ).fetchall()

    def training_samples(self) -> list[tuple[dict, int]]:
        """给 datefit.train 用：每个系列取最近一次 👍/👎 作标签，
        特征字段优先从 events 缓存取完整 payload，取不到就只用标题。"""
        rows = self.db.execute(
            "SELECT f.series_key, f.title, f.uid, f.category, f.verdict,"
            "       e.payload AS epayload "
            "FROM feedback f LEFT JOIN events e ON e.uid = f.uid "
            "ORDER BY f.id",
        ).fetchall()
        latest: dict[str, tuple[dict, int]] = {}
        for r in rows:
            fields: dict = {"title": r["title"] or "", "category": r["category"] or ""}
            if r["epayload"]:
                try:
                    p = json.loads(r["epayload"])
                    fields.update(
                        description=p.get("description") or "",
                        category=p.get("category") or fields["category"],
                        venue_name=p.get("venue_name") or "",
                        price_per_person=p.get("price_per_person"),
                    )
                except json.JSONDecodeError:
                    pass
            latest[r["series_key"]] = (fields, 1 if r["verdict"] == "like" else 0)
        return list(latest.values())

    # ---------- 批次 ----------
    def create_batch(self, events: list[Event], rubric_version: str,
                     at: Optional[datetime] = None) -> int:
        """把一批推荐落库，返回 batch_id。"""
        ts = (at or datetime.now()).isoformat()
        cur = self.db.execute(
            "INSERT INTO batches (created_at, rubric_version, item_count, top_score) VALUES (?,?,?,?)",
            (ts, rubric_version, len(events), events[0].total if events else None),
        )
        batch_id = int(cur.lastrowid)
        for e in events:
            self.db.execute(
                "INSERT INTO sent_history "
                "(batch_id, uid, title, total, rubric_version, fingerprint, payload, sent_at, series_key) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (batch_id, e.uid, e.title, e.total, rubric_version, _fingerprint(e),
                 json.dumps(e.as_payload(), ensure_ascii=False), ts,
                 series_key(e.title, e.venue_name)),
            )
        self.db.commit()
        return batch_id

    def list_batches(self, limit: int = 100) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT id, created_at, rubric_version, item_count, top_score "
            "FROM batches ORDER BY created_at DESC LIMIT ?", (limit,),
        ).fetchall()

    def latest_batch_id(self) -> Optional[int]:
        row = self.db.execute("SELECT id FROM batches ORDER BY created_at DESC LIMIT 1").fetchone()
        return int(row["id"]) if row else None

    def batch_events(self, batch_id: int) -> list[dict[str, Any]]:
        """取某批次里的活动，返回可直接渲染的 dict 列表（按分数降序）。"""
        rows = self.db.execute(
            "SELECT title, total, payload FROM sent_history WHERE batch_id=? ORDER BY total DESC",
            (batch_id,),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            if r["payload"]:
                out.append(json.loads(r["payload"]))
            else:                                    # 早期版本没存 payload，降级展示
                out.append({"title": r["title"], "total": r["total"], "scores": {}, "plan": {}})
        return out

    def score_history(self, limit: int = 50) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT uid, title, total, rubric_version, sent_at FROM sent_history "
            "ORDER BY sent_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
