"""
SQLite storage. Single file, commits back to the repo so history survives
between GitHub Actions runs (Actions runners are ephemeral).

Snapshots store compressed text so the DB stays small enough for git.
"""
import json
import os
import sqlite3
import zlib
from contextlib import contextmanager
from datetime import datetime

DB_PATH = os.environ.get("WATCHDOG_DB", "data/watchdog.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS pages (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    group_name    TEXT NOT NULL,          -- the report/topic this row belongs to
    role          TEXT NOT NULL,          -- 'own' or 'competitor'
    label         TEXT,                   -- e.g. "Competitor 1"
    url           TEXT NOT NULL UNIQUE,
    css_selector  TEXT,
    preferred_tier TEXT,
    active        INTEGER DEFAULT 1,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id     INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    hash        TEXT NOT NULL,
    title       TEXT,
    word_count  INTEGER,
    blob        BLOB NOT NULL,            -- zlib-compressed extracted text
    meta        TEXT,                     -- json: headings, links, tier
    captured_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snap_page ON snapshots(page_id, captured_at DESC);

CREATE TABLE IF NOT EXISTS changes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id     INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    old_snap_id INTEGER,
    new_snap_id INTEGER,
    change_pct  REAL,
    added       INTEGER,
    removed     INTEGER,
    modified    INTEGER,
    detail      TEXT,                     -- json diff payload
    notified    INTEGER DEFAULT 0,
    detected_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chg_time ON changes(detected_at DESC);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT,
    finished_at TEXT,
    checked     INTEGER,
    changed     INTEGER,
    errors      INTEGER,
    log         TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


@contextmanager
def conn():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init():
    with conn() as c:
        c.executescript(SCHEMA)


def now():
    return datetime.utcnow().isoformat(timespec="seconds")


# ---------- settings ----------
def set_setting(key, value):
    with conn() as c:
        c.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )


def get_setting(key, default=None):
    with conn() as c:
        r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return json.loads(r["value"]) if r else default


# ---------- pages ----------
def upsert_page(group_name, role, label, url, css_selector=None):
    with conn() as c:
        c.execute(
            """INSERT INTO pages(group_name, role, label, url, css_selector, created_at)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(url) DO UPDATE SET
                 group_name=excluded.group_name,
                 role=excluded.role,
                 label=excluded.label,
                 css_selector=excluded.css_selector,
                 active=1""",
            (group_name, role, label, url.strip(), css_selector, now()),
        )


def get_pages(active_only=True):
    q = "SELECT * FROM pages"
    if active_only:
        q += " WHERE active=1"
    q += " ORDER BY group_name, role DESC, label"
    with conn() as c:
        return [dict(r) for r in c.execute(q).fetchall()]


def set_page_active(page_id, active):
    with conn() as c:
        c.execute("UPDATE pages SET active=? WHERE id=?", (1 if active else 0, page_id))


def delete_page(page_id):
    with conn() as c:
        c.execute("DELETE FROM pages WHERE id=?", (page_id,))


def set_preferred_tier(page_id, tier):
    with conn() as c:
        c.execute("UPDATE pages SET preferred_tier=? WHERE id=?", (tier, page_id))


def clear_all_pages():
    with conn() as c:
        c.execute("DELETE FROM pages")


# ---------- snapshots ----------
def latest_snapshot(page_id):
    with conn() as c:
        r = c.execute(
            "SELECT * FROM snapshots WHERE page_id=? ORDER BY captured_at DESC, id DESC LIMIT 1",
            (page_id,),
        ).fetchone()
    if not r:
        return None
    d = dict(r)
    d["text"] = zlib.decompress(d.pop("blob")).decode("utf-8")
    d["meta"] = json.loads(d["meta"] or "{}")
    return d


def save_snapshot(page_id, extracted, tier):
    meta = json.dumps({
        "headings": extracted["headings"],
        "links": extracted["links"],
        "meta_description": extracted["meta_description"],
        "tier": tier,
    })
    with conn() as c:
        cur = c.execute(
            """INSERT INTO snapshots(page_id, hash, title, word_count, blob, meta, captured_at)
               VALUES(?,?,?,?,?,?,?)""",
            (
                page_id,
                extracted["hash"],
                extracted["title"],
                extracted["word_count"],
                zlib.compress(extracted["text"].encode("utf-8"), 9),
                meta,
                now(),
            ),
        )
        return cur.lastrowid


def prune_snapshots(page_id, keep=15):
    """Keep the DB git-friendly."""
    with conn() as c:
        c.execute(
            """DELETE FROM snapshots WHERE page_id=? AND id NOT IN
               (SELECT id FROM snapshots WHERE page_id=?
                ORDER BY captured_at DESC, id DESC LIMIT ?)""",
            (page_id, page_id, keep),
        )


# ---------- changes ----------
def record_change(page_id, old_id, new_id, d):
    payload = json.dumps({
        "added": d["added"][:200],
        "removed": d["removed"][:200],
        "modified": d["modified"][:200],
        "unified": d["unified"][:60000],
    })
    with conn() as c:
        cur = c.execute(
            """INSERT INTO changes(page_id, old_snap_id, new_snap_id, change_pct,
                                   added, removed, modified, detail, detected_at)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (page_id, old_id, new_id, d["change_pct"],
             d["counts"]["added"], d["counts"]["removed"], d["counts"]["modified"],
             payload, now()),
        )
        return cur.lastrowid


def mark_notified(change_ids):
    if not change_ids:
        return
    with conn() as c:
        c.executemany(
            "UPDATE changes SET notified=1 WHERE id=?", [(i,) for i in change_ids]
        )


def recent_changes(limit=100):
    with conn() as c:
        return [dict(r) for r in c.execute(
            """SELECT ch.*, p.url, p.label, p.group_name, p.role
               FROM changes ch JOIN pages p ON p.id=ch.page_id
               ORDER BY ch.detected_at DESC, ch.id DESC LIMIT ?""", (limit,)
        ).fetchall()]


def change_detail(change_id):
    with conn() as c:
        r = c.execute(
            """SELECT ch.*, p.url, p.label, p.group_name, p.role
               FROM changes ch JOIN pages p ON p.id=ch.page_id WHERE ch.id=?""",
            (change_id,),
        ).fetchone()
    if not r:
        return None
    d = dict(r)
    d["detail"] = json.loads(d["detail"] or "{}")
    return d


# ---------- runs ----------
def start_run():
    with conn() as c:
        return c.execute(
            "INSERT INTO runs(started_at) VALUES(?)", (now(),)
        ).lastrowid


def finish_run(run_id, checked, changed, errors, log):
    with conn() as c:
        c.execute(
            """UPDATE runs SET finished_at=?, checked=?, changed=?, errors=?, log=?
               WHERE id=?""",
            (now(), checked, changed, errors, log[:20000], run_id),
        )


def recent_runs(limit=20):
    with conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()]
