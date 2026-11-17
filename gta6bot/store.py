"""SQLite storage: items (news + videos), view snapshots, tracked channels, post log."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,              -- news | video
  url TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  summary TEXT DEFAULT '',
  title_ru TEXT DEFAULT '',
  summary_ru TEXT DEFAULT '',
  source TEXT DEFAULT '',
  source_type TEXT DEFAULT 'media', -- official|youtube|media|leak|community
  category TEXT DEFAULT 'news',     -- news|update|guide|video|leak
  image TEXT DEFAULT '',
  video_id TEXT DEFAULT '',
  channel_id TEXT DEFAULT '',
  channel_title TEXT DEFAULT '',
  published TEXT NOT NULL,
  first_seen TEXT NOT NULL,
  views INTEGER,
  vph REAL,
  recent_vph REAL,
  score REAL DEFAULT 0,
  verified INTEGER DEFAULT 0,
  tags TEXT DEFAULT '[]',
  posted_at TEXT
);
CREATE INDEX IF NOT EXISTS items_pub ON items(published);
CREATE TABLE IF NOT EXISTS snapshots (
  video_id TEXT, ts TEXT, views INTEGER, likes INTEGER, comments INTEGER,
  PRIMARY KEY (video_id, ts)
);
CREATE TABLE IF NOT EXISTS channels (
  channel_id TEXT PRIMARY KEY, title TEXT, official INTEGER DEFAULT 0,
  source TEXT DEFAULT 'seed', added TEXT, subscribers INTEGER
);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
"""


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def item_id(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()[:16]


class Store:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # ---- kv
    def get(self, k: str, default: str | None = None) -> str | None:
        r = self.db.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return r[0] if r else default

    def set(self, k: str, v: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (k, v))
        self.db.commit()

    # ---- channels
    def add_channel(self, cid: str, title: str, official=False, source="seed") -> bool:
        cur = self.db.execute(
            "INSERT OR IGNORE INTO channels(channel_id,title,official,source,added) VALUES (?,?,?,?,?)",
            (cid, title, int(official), source, iso(now())))
        self.db.commit()
        return cur.rowcount > 0

    def channels(self) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM channels ORDER BY added").fetchall()

    def is_official_channel(self, cid: str) -> bool:
        r = self.db.execute("SELECT official FROM channels WHERE channel_id=?", (cid,)).fetchone()
        return bool(r and r[0])

    # ---- items
    def upsert(self, it: dict) -> bool:
        """Insert new item; for existing, refresh mutable stats. Returns True if new."""
        it.setdefault("id", item_id(it["url"]))
        exists = self.db.execute("SELECT 1 FROM items WHERE id=? OR url=?", (it["id"], it["url"])).fetchone()
        if exists:
            if it.get("views") is not None:
                self.db.execute("UPDATE items SET views=? WHERE id=?", (it["views"], it["id"]))
                self.db.commit()
            return False
        cols = ["id", "kind", "url", "title", "summary", "source", "source_type", "category", "image",
                "video_id", "channel_id", "channel_title", "published", "first_seen", "views", "tags"]
        it.setdefault("first_seen", iso(now()))
        it["tags"] = json.dumps(it.get("tags", []), ensure_ascii=False)
        vals = [it.get(c) if it.get(c) is not None else ("" if c not in ("views",) else None) for c in cols]
        self.db.execute(f"INSERT INTO items({','.join(cols)}) VALUES ({','.join('?'*len(cols))})", vals)
        self.db.commit()
        return True

    def update(self, iid: str, **fields) -> None:
        if not fields:
            return
        sets = ",".join(f"{k}=?" for k in fields)
        self.db.execute(f"UPDATE items SET {sets} WHERE id=?", (*fields.values(), iid))
        self.db.commit()

    def recent(self, hours: float, kind: str | None = None) -> list[sqlite3.Row]:
        since = iso(datetime.fromtimestamp(now().timestamp() - hours * 3600, timezone.utc))
        q = "SELECT * FROM items WHERE published >= ?"
        args: list = [since]
        if kind:
            q += " AND kind=?"
            args.append(kind)
        return self.db.execute(q + " ORDER BY published DESC", args).fetchall()

    def all_items(self, limit=500) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM items ORDER BY published DESC LIMIT ?", (limit,)).fetchall()

    # ---- snapshots
    def snapshot(self, vid: str, views: int, likes=None, comments=None, ts: datetime | None = None):
        self.db.execute("INSERT OR REPLACE INTO snapshots VALUES (?,?,?,?,?)",
                        (vid, iso(ts or now()), views, likes, comments))
        self.db.commit()

    def snapshots(self, vid: str) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM snapshots WHERE video_id=? ORDER BY ts", (vid,)).fetchall()

    # ---- posting
    def posted_since(self, minutes: float) -> list[sqlite3.Row]:
        since = iso(datetime.fromtimestamp(now().timestamp() - minutes * 60, timezone.utc))
        return self.db.execute("SELECT * FROM items WHERE posted_at >= ? ORDER BY posted_at DESC",
                               (since,)).fetchall()
