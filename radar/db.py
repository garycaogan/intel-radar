"""SQLite store: every item ever seen, metric snapshots, topic memory and daily briefs."""

from __future__ import annotations

import json
import os
import sqlite3
import time

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id TEXT PRIMARY KEY,           -- source:ext_id
    source TEXT NOT NULL,          -- bili, youtube, x, github, hf, hn, bili_hot, douyin_hot
    kind TEXT NOT NULL,            -- video, tweet, repo, model, space, story, hotword
    title TEXT, text TEXT, url TEXT, thumb TEXT,
    author TEXT, author_id TEXT,
    published_at REAL,
    first_seen REAL NOT NULL,
    last_seen REAL NOT NULL,
    metrics TEXT,                  -- latest metrics json
    extra TEXT,                    -- source-specific json
    queries TEXT                   -- json list of queries that surfaced it
);
CREATE INDEX IF NOT EXISTS items_source ON items(source, last_seen);
CREATE TABLE IF NOT EXISTS snaps (
    item_id TEXT NOT NULL, ts REAL NOT NULL, metrics TEXT NOT NULL,
    PRIMARY KEY (item_id, ts)
);
CREATE TABLE IF NOT EXISTS authors (
    id TEXT PRIMARY KEY,           -- platform:author_id
    name TEXT, followers INTEGER, updated REAL, extra TEXT
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT NOT NULL, started REAL NOT NULL, finished REAL,
    status TEXT, stats TEXT
);
CREATE TABLE IF NOT EXISTS topics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT NOT NULL,
    section TEXT NOT NULL,         -- video, x, repo, ...
    name TEXT NOT NULL,
    terms TEXT,                    -- json list
    label TEXT,                    -- 新起 / 升温 / 持续 / 回落
    thread_id INTEGER,             -- the first topic row of this thread
    first_day TEXT,
    payload TEXT
);
CREATE INDEX IF NOT EXISTS topics_day ON topics(day, section);
CREATE TABLE IF NOT EXISTS briefs (
    day TEXT PRIMARY KEY, created REAL NOT NULL, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS deeps (           -- radar.deep: origin dates and more hits per video topic
    day TEXT PRIMARY KEY, created REAL NOT NULL, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS probes (
    key TEXT PRIMARY KEY,          -- platform:term:day
    created REAL, payload TEXT
);
"""


def connect() -> sqlite3.Connection:
    config.ensure_dirs()
    fresh = not config.DB_PATH.exists()
    con = sqlite3.connect(config.DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    if fresh:
        try:
            os.chmod(config.DB_PATH, 0o600)
        except OSError:
            pass
    return con


def _j(v) -> str:
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"))


def upsert_items(con: sqlite3.Connection, items: list[dict], now: float | None = None) -> int:
    """Insert or refresh items and record a metric snapshot for each. Returns how many were new."""
    now = now or time.time()
    new = 0
    for it in items:
        row = con.execute("SELECT queries, first_seen FROM items WHERE id=?", (it["id"],)).fetchone()
        queries = set(json.loads(row["queries"] or "[]")) if row else set()
        queries.update(q for q in [it.get("query"), *(it.get("queries") or [])] if q)
        if row is None:
            new += 1
            con.execute(
                "INSERT INTO items(id,source,kind,title,text,url,thumb,author,author_id,published_at,"
                "first_seen,last_seen,metrics,extra,queries) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (it["id"], it["source"], it["kind"], it.get("title"), it.get("text"), it.get("url"),
                 it.get("thumb"), it.get("author"), it.get("author_id"), it.get("published_at"),
                 now, now, _j(it.get("metrics") or {}), _j(it.get("extra") or {}), _j(sorted(queries))))
        else:
            con.execute(
                "UPDATE items SET title=?, text=COALESCE(?, text), url=?, thumb=COALESCE(?, thumb), author=?,"
                " author_id=?, published_at=COALESCE(?, published_at), last_seen=?, metrics=?, extra=?, queries=?"
                " WHERE id=?",
                (it.get("title"), it.get("text"), it.get("url"), it.get("thumb"), it.get("author"),
                 it.get("author_id"), it.get("published_at"), now, _j(it.get("metrics") or {}),
                 _j(it.get("extra") or {}), _j(sorted(queries)), it["id"]))
        if it.get("metrics"):
            con.execute("INSERT OR REPLACE INTO snaps(item_id, ts, metrics) VALUES(?,?,?)",
                        (it["id"], round(now), _j(it["metrics"])))
    con.commit()
    return new


def load_recent(con: sqlite3.Connection, since: float) -> list[dict]:
    """Items seen since ``since``, in collector shape (used to re-analyze without re-collecting)."""
    out = []
    for r in con.execute("SELECT * FROM items WHERE last_seen>=?", (since,)):
        queries = json.loads(r["queries"] or "[]")
        out.append({"id": r["id"], "source": r["source"], "kind": r["kind"], "title": r["title"], "text": r["text"],
                    "url": r["url"], "thumb": r["thumb"], "author": r["author"], "author_id": r["author_id"],
                    "published_at": r["published_at"], "metrics": json.loads(r["metrics"] or "{}"),
                    "extra": json.loads(r["extra"] or "{}"), "queries": queries, "query": queries[0] if queries else ""})
    return out


def first_seen(con: sqlite3.Connection, ids: list[str]) -> dict[str, float]:
    if not ids:
        return {}
    out = {}
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        q = "SELECT id, first_seen FROM items WHERE id IN (%s)" % ",".join("?" * len(chunk))
        out.update({r["id"]: r["first_seen"] for r in con.execute(q, chunk)})
    return out


def previous_snap(con: sqlite3.Connection, item_id: str, before: float, min_gap: float = 6 * 3600):
    """The newest snapshot at least ``min_gap`` older than ``before``: (ts, metrics) or None."""
    r = con.execute("SELECT ts, metrics FROM snaps WHERE item_id=? AND ts<=? ORDER BY ts DESC LIMIT 1",
                    (item_id, before - min_gap)).fetchone()
    return (r["ts"], json.loads(r["metrics"])) if r else None


def author_followers(con: sqlite3.Connection, key: str, max_age: float = 3 * 86400):
    r = con.execute("SELECT followers, updated FROM authors WHERE id=?", (key,)).fetchone()
    if r and r["updated"] and time.time() - r["updated"] < max_age:
        return r["followers"]
    return None


def save_author(con: sqlite3.Connection, key: str, name: str, followers: int | None, extra: dict | None = None):
    con.execute("INSERT OR REPLACE INTO authors(id,name,followers,updated,extra) VALUES(?,?,?,?,?)",
                (key, name, followers, time.time(), _j(extra or {})))
    con.commit()


def probe_get(con, key: str, max_age: float = 20 * 3600):
    r = con.execute("SELECT created, payload FROM probes WHERE key=?", (key,)).fetchone()
    if r and time.time() - r["created"] < max_age:
        return json.loads(r["payload"])
    return None


def probe_put(con, key: str, payload: dict):
    con.execute("INSERT OR REPLACE INTO probes(key, created, payload) VALUES(?,?,?)", (key, time.time(), _j(payload)))
    con.commit()


def recent_topics(con: sqlite3.Connection, section: str, before_day: str, days: int = 30) -> list[dict]:
    rows = con.execute(
        "SELECT id, day, name, terms, label, thread_id, first_day FROM topics WHERE section=? AND day<? "
        "AND day>=date(?, ?) ORDER BY day DESC", (section, before_day, before_day, f"-{days} days")).fetchall()
    seen, out = set(), []
    for r in rows:   # newest row per thread
        tid = r["thread_id"] or r["id"]
        if tid in seen:
            continue
        seen.add(tid)
        out.append({"thread_id": tid, "day": r["day"], "name": r["name"], "terms": json.loads(r["terms"] or "[]"),
                    "label": r["label"], "first_day": r["first_day"] or r["day"]})
    return out


def thread_days(con: sqlite3.Connection, thread_id: int) -> list[str]:
    return [r["day"] for r in con.execute(
        "SELECT DISTINCT day FROM topics WHERE thread_id=? OR id=? ORDER BY day", (thread_id, thread_id))]


def save_topic(con, day: str, section: str, name: str, terms: list[str], label: str,
               thread_id: int | None, first_day: str | None, payload: dict) -> int:
    con.execute("DELETE FROM topics WHERE day=? AND section=? AND name=?", (day, section, name))
    cur = con.execute(
        "INSERT INTO topics(day,section,name,terms,label,thread_id,first_day,payload) VALUES(?,?,?,?,?,?,?,?)",
        (day, section, name, _j(terms), label, thread_id, first_day or day, _j(payload)))
    if thread_id is None:
        con.execute("UPDATE topics SET thread_id=id WHERE id=?", (cur.lastrowid,))
    con.commit()
    return thread_id or cur.lastrowid


def save_brief(con, day: str, payload: dict):
    con.execute("INSERT OR REPLACE INTO briefs(day, created, payload) VALUES(?,?,?)", (day, time.time(), _j(payload)))
    con.commit()


def save_deep(con, day: str, payload: dict):
    con.execute("INSERT OR REPLACE INTO deeps(day, created, payload) VALUES(?,?,?)", (day, time.time(), _j(payload)))
    con.commit()


def load_deep(con, day: str) -> dict | None:
    r = con.execute("SELECT payload FROM deeps WHERE day=?", (day,)).fetchone()
    return json.loads(r["payload"]) if r else None


def brief_days(con) -> list[str]:
    return [r["day"] for r in con.execute("SELECT day FROM briefs ORDER BY day DESC")]
