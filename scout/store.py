"""SQLite 存储：事件缓存、推送批次与历史、运行间隔守卫、跨批次 resend 判断。

「批次(batch)」是 GUI 的核心概念：每次真正产生推荐就落一条 batch，
被推荐的活动挂在该 batch 下。于是：
    当前推荐 = 最新一个 batch 的活动
    历史推荐 = 按 batch 倒序浏览
"""
from __future__ import annotations

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
CREATE INDEX IF NOT EXISTS idx_sent_uid ON sent_history(uid);
CREATE INDEX IF NOT EXISTS idx_sent_batch ON sent_history(batch_id);
"""


def _fingerprint(e: Event) -> str:
    # 时间或价格变动即视为「新版本」，会重置 resend 冷却。
    return f"{e.start.isoformat() if e.start else ''}|{e.price_per_person}"


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
        for col in ("batch_id INTEGER", "payload TEXT"):
            name = col.split()[0]
            if name not in cols:
                self.db.execute(f"ALTER TABLE sent_history ADD COLUMN {col}")

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
    def should_send(self, e: Event, resend_after_days: int) -> bool:
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
                "(batch_id, uid, title, total, rubric_version, fingerprint, payload, sent_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (batch_id, e.uid, e.title, e.total, rubric_version, _fingerprint(e),
                 json.dumps(e.as_payload(), ensure_ascii=False), ts),
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
