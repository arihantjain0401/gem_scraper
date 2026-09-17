"""SQLite persistence for the full-board monitor (data/monitor.db).

Separate from data/app.db on purpose: the monitor's ~50k-bid index plus a
daily events stream would dwarf the web app's data, and a separate file
keeps the web app and long crawls from contending on one database. WAL +
busy_timeout so the MCP server can read while a crawl writes. One
connection per operation (thread-safe), matching app.db's idiom.
"""

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone

from app.config import DATA_DIR, MONITOR_DB_PATH
from app.monitor.vitals import COLUMN_KEYS, diff_fields, fingerprint, to_vitals
from app.scraper.util import unwrap

SCHEMA = """
CREATE TABLE IF NOT EXISTS bids (
  b_id              TEXT PRIMARY KEY,
  bid_number        TEXT,
  parent_bid_id     TEXT,
  parent_bid_number TEXT,
  bid_type          TEXT,
  b_status          TEXT,
  buyer_status      TEXT,
  category_name     TEXT,
  bd_category_name  TEXT,
  ministry          TEXT,
  department        TEXT,
  total_quantity    TEXT,
  start_date        TEXT,
  end_date          TEXT,
  is_rc_bid         TEXT,
  is_global_tendering TEXT,
  is_high_value     TEXT,
  bid_schedule      TEXT,
  fingerprint       TEXT NOT NULL,
  first_seen        TEXT NOT NULL,
  last_seen         TEXT NOT NULL,
  last_changed      TEXT,
  is_active         INTEGER NOT NULL DEFAULT 1,
  detail_state      TEXT NOT NULL DEFAULT 'none',
  detail_fetched_at TEXT,
  detail_error      TEXT
);
CREATE INDEX IF NOT EXISTS idx_bids_active    ON bids(is_active, last_seen);
CREATE INDEX IF NOT EXISTS idx_bids_number    ON bids(bid_number);
CREATE INDEX IF NOT EXISTS idx_bids_end_date  ON bids(end_date);
CREATE INDEX IF NOT EXISTS idx_bids_department ON bids(department);

CREATE TABLE IF NOT EXISTS crawls (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  mode              TEXT NOT NULL,
  status            TEXT NOT NULL,
  started_at        TEXT NOT NULL,
  finished_at       TEXT,
  sort_used         TEXT,
  window            TEXT,
  num_found_start   INTEGER,
  num_found_end     INTEGER,
  pages_total       INTEGER,
  pages_fetched     INTEGER,
  pages_ok          INTEGER,
  convergence_pages INTEGER,
  docs_seen         INTEGER,
  new_count         INTEGER NOT NULL DEFAULT 0,
  modified_count    INTEGER NOT NULL DEFAULT 0,
  closed_count      INTEGER NOT NULL DEFAULT 0,
  reappeared_count  INTEGER NOT NULL DEFAULT 0,
  complete          INTEGER NOT NULL DEFAULT 0,
  error             TEXT,
  summary           TEXT
);
CREATE TABLE IF NOT EXISTS crawl_pages (
  crawl_id   INTEGER NOT NULL REFERENCES crawls(id) ON DELETE CASCADE,
  page       INTEGER NOT NULL,
  ok         INTEGER NOT NULL,
  docs       INTEGER NOT NULL,
  fetched_at TEXT NOT NULL,
  PRIMARY KEY (crawl_id, page)
);
CREATE TABLE IF NOT EXISTS events (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  crawl_id   INTEGER NOT NULL REFERENCES crawls(id),
  ts         TEXT NOT NULL,
  bid_id     TEXT NOT NULL,
  bid_number TEXT,
  kind       TEXT NOT NULL,
  fields     TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_ts  ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_bid ON events(bid_id);

CREATE TABLE IF NOT EXISTS counts (
  ts        TEXT PRIMARY KEY,
  num_found INTEGER NOT NULL,
  source    TEXT NOT NULL,
  crawl_id  INTEGER
);

CREATE TABLE IF NOT EXISTS bid_details (
  b_id        TEXT PRIMARY KEY REFERENCES bids(b_id),
  fetched_at  TEXT NOT NULL,
  ok          INTEGER NOT NULL,
  http_status INTEGER,
  url         TEXT,
  parsed      TEXT,
  raw_html    TEXT
);
"""


def utcnow_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _connect():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(MONITOR_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init():
    """Create the schema and mark stale 'running' crawls failed.

    "Stale" = no crawl_pages activity in the last 10 minutes — a live crawl
    writes a page row every ~1.5 s, so another process starting up (MCP
    server, CLI) never clobbers it. A crashed crawl goes quiet and is
    correctly marked (and stays resumable: finished_at stays NULL).
    """
    conn = _connect()
    try:
        with conn:
            conn.executescript(SCHEMA)
            cutoff = (datetime.now(timezone.utc) - timedelta(minutes=10)) \
                .strftime("%Y-%m-%dT%H:%M:%SZ")
            conn.execute(
                "UPDATE crawls SET status='failed', error='marked failed at startup' "
                "WHERE status='running' AND finished_at IS NULL AND NOT EXISTS ("
                "  SELECT 1 FROM crawl_pages p WHERE p.crawl_id = crawls.id "
                "  AND p.fetched_at >= ?)",
                (cutoff,),
            )
    finally:
        conn.close()


# ────────────────────────── crawl lifecycle ──────────────────────────

def create_crawl(mode, sort_used=None, window=None, started_at=None):
    conn = _connect()
    try:
        with conn:
            cur = conn.execute(
                "INSERT INTO crawls (mode, status, started_at, sort_used, window) "
                "VALUES (?, 'running', ?, ?, ?)",
                (mode, started_at or utcnow_iso(),
                 sort_used, json.dumps(window) if window is not None else None),
            )
            return cur.lastrowid
    finally:
        conn.close()


def finish_crawl(crawl_id, status, error=None, summary=None, **kwargs):
    """Finalize a crawl row. kwargs may carry any numeric column (counts, pages, ...)."""
    conn = _connect()
    try:
        with conn:
            sets = ["status=?", "finished_at=?"]
            values = [status, utcnow_iso()]
            for key in ("num_found_start", "num_found_end", "pages_total",
                        "pages_fetched", "pages_ok", "convergence_pages",
                        "docs_seen", "new_count", "modified_count",
                        "closed_count", "reappeared_count", "complete"):
                if key in kwargs:
                    sets.append("%s=?" % key)
                    values.append(kwargs[key])
            if error is not None:
                sets.append("error=?")
                values.append(error)
            if summary is not None:
                sets.append("summary=?")
                values.append(summary)
            values.append(crawl_id)
            conn.execute(
                "UPDATE crawls SET %s WHERE id=?" % ", ".join(sets), values
            )
    finally:
        conn.close()


def record_page(crawl_id, page, ok, docs):
    conn = _connect()
    try:
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO crawl_pages (crawl_id, page, ok, docs, fetched_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (crawl_id, page, 1 if ok else 0, docs, utcnow_iso()),
            )
    finally:
        conn.close()


def get_crawl_pages(crawl_id):
    """Return {page: (ok, docs)} for resume/completeness accounting."""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT page, ok, docs FROM crawl_pages WHERE crawl_id=?", (crawl_id,)
        ).fetchall()
        return {r["page"]: (r["ok"], r["docs"]) for r in rows}
    finally:
        conn.close()


def latest_crawl(mode=None):
    conn = _connect()
    try:
        if mode:
            row = conn.execute(
                "SELECT * FROM crawls WHERE mode=? ORDER BY id DESC LIMIT 1", (mode,)
            ).fetchone()
        else:
            row = conn.execute("SELECT * FROM crawls ORDER BY id DESC LIMIT 1").fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_crawl_row(crawl_id):
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM crawls WHERE id=?", (crawl_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def running_crawl():
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM crawls WHERE status='running' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def completed_crawl_since(mode, cutoff_utc_iso):
    """True when a completed/partial crawl of this mode started at/after the cutoff."""
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT 1 FROM crawls WHERE mode=? AND status IN ('completed','partial') "
            "AND started_at >= ? LIMIT 1",
            (mode, cutoff_utc_iso),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


# ────────────────────────── ingest & diff ──────────────────────────

def _row_vitals(row):
    return {key: row[col] or "" for key, col in COLUMN_KEYS.items()}


def _insert_event(conn, crawl_id, now, bid_id, bid_number, kind, fields=None):
    conn.execute(
        "INSERT INTO events (crawl_id, ts, bid_id, bid_number, kind, fields) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (crawl_id, now, bid_id, bid_number, kind,
         json.dumps(fields) if fields else None),
    )


def apply_page(crawl_id, page, docs):
    """Upsert-diff one page of docs in a single transaction.

    Returns (new_count, modified_count, reappeared_count, doc_count).
    """
    now = utcnow_iso()
    new = modified = reappeared = 0
    conn = _connect()
    try:
        with conn:
            for doc in docs:
                bid_id = unwrap(doc.get("b_id")) or unwrap(doc.get("id"))
                if not bid_id:
                    continue
                vitals = to_vitals(doc)
                fp = fingerprint(vitals)
                row = conn.execute(
                    "SELECT * FROM bids WHERE b_id=?", (bid_id,)
                ).fetchone()
                if row is None:
                    conn.execute(
                        "INSERT INTO bids (b_id, %s, fingerprint, first_seen, "
                        "last_seen, is_active, detail_state) "
                        "VALUES (?, %s, ?, ?, ?, 1, 'none')"
                        % (", ".join(COLUMN_KEYS.values()),
                           ", ".join("?" for _ in COLUMN_KEYS)),
                        [bid_id] + [vitals[k] for k in COLUMN_KEYS]
                        + [fp, now, now],
                    )
                    _insert_event(conn, crawl_id, now, bid_id,
                                  vitals["b_bid_number"], "new")
                    new += 1
                elif row["fingerprint"] == fp:
                    if row["is_active"]:
                        conn.execute(
                            "UPDATE bids SET last_seen=? WHERE b_id=?", (now, bid_id)
                        )
                    else:
                        conn.execute(
                            "UPDATE bids SET last_seen=?, last_changed=?, is_active=1 "
                            "WHERE b_id=?",
                            (now, now, bid_id),
                        )
                        _insert_event(conn, crawl_id, now, bid_id,
                                      vitals["b_bid_number"], "reappeared")
                        reappeared += 1
                else:
                    diffs = diff_fields(_row_vitals(row), vitals)
                    conn.execute(
                        "UPDATE bids SET %s, fingerprint=?, last_seen=?, "
                        "last_changed=? WHERE b_id=?"
                        % ", ".join("%s=?" % c for c in COLUMN_KEYS.values()),
                        [vitals[k] for k in COLUMN_KEYS]
                        + [fp, now, now, bid_id],
                    )
                    _insert_event(conn, crawl_id, now, bid_id,
                                  vitals["b_bid_number"], "modified", diffs)
                    modified += 1
            conn.execute(
                "INSERT OR REPLACE INTO crawl_pages (crawl_id, page, ok, docs, fetched_at) "
                "VALUES (?, ?, 1, ?, ?)",
                (crawl_id, page, len(docs), now),
            )
        return new, modified, reappeared, len(docs)
    finally:
        conn.close()


def mark_closed(crawl_id, cutoff_utc_iso):
    """Mark active bids not seen since the cutoff as closed (complete crawls only).

    Returns the number of bids closed. Called only when the crawl is
    complete — absence is otherwise meaningless.
    """
    now = utcnow_iso()
    closed = 0
    conn = _connect()
    try:
        with conn:
            rows = conn.execute(
                "SELECT b_id, bid_number FROM bids WHERE is_active=1 AND last_seen < ?",
                (cutoff_utc_iso,),
            ).fetchall()
            for r in rows:
                conn.execute(
                    "UPDATE bids SET is_active=0 WHERE b_id=?", (r["b_id"],)
                )
                _insert_event(conn, crawl_id, now, r["b_id"],
                              r["bid_number"], "closed")
                closed += 1
        return closed
    finally:
        conn.close()


def count_active():
    conn = _connect()
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM bids WHERE is_active=1"
        ).fetchone()[0]
    finally:
        conn.close()


# ────────────────────────── numFound counts ──────────────────────────

def insert_count(num_found, source, crawl_id=None):
    conn = _connect()
    try:
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO counts (ts, num_found, source, crawl_id) "
                "VALUES (?, ?, ?, ?)",
                (utcnow_iso(), num_found, source, crawl_id),
            )
    finally:
        conn.close()


def latest_count():
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM counts ORDER BY ts DESC LIMIT 1").fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ────────────────────────── detail cache ──────────────────────────

def get_bid(b_id):
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM bids WHERE b_id=?", (b_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_detail(b_id):
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM bid_details WHERE b_id=?", (b_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def set_detail(b_id, ok, http_status, url, parsed, raw_html=None):
    """Cache a detail fetch; raw_html is stored only on parse failure."""
    now = utcnow_iso()
    conn = _connect()
    try:
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO bid_details "
                "(b_id, fetched_at, ok, http_status, url, parsed, raw_html) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (b_id, now, 1 if ok else 0, http_status, url,
                 json.dumps(parsed) if parsed else None, raw_html),
            )
            conn.execute(
                "UPDATE bids SET detail_state=?, detail_fetched_at=?, detail_error=NULL "
                "WHERE b_id=?",
                ("fetched" if ok else "failed", now, b_id),
            )
    finally:
        conn.close()


def detail_cache_stats():
    conn = _connect()
    try:
        fetched = conn.execute(
            "SELECT COUNT(*) FROM bid_details"
        ).fetchone()[0]
        ok = conn.execute(
            "SELECT COUNT(*) FROM bid_details WHERE ok=1"
        ).fetchone()[0]
        return fetched, ok, fetched - ok
    finally:
        conn.close()


# ────────────────────────── MCP tool queries ──────────────────────────

def search_bids(q=None, status="ongoing", organization=None, department=None,
                category=None, end_after=None, end_before=None,
                limit=50, offset=0):
    """Filter the local index. Returns (total, rows). No GeM call."""
    where, values = [], []
    if status in ("ongoing", "closed"):
        where.append("is_active=%d" % (1 if status == "ongoing" else 0))
    if q:
        where.append(
            "(bid_number LIKE ? OR category_name LIKE ? OR bd_category_name LIKE ? "
            "OR ministry LIKE ? OR department LIKE ? OR b_status LIKE ?)"
        )
        like = "%%%s%%" % q
        values += [like] * 6
    if organization:
        where.append("(ministry LIKE ? OR department LIKE ?)")
        like = "%%%s%%" % organization
        values += [like, like]
    if department:
        where.append("department LIKE ?")
        values.append("%%%s%%" % department)
    if category:
        where.append("(category_name LIKE ? OR bd_category_name LIKE ?)")
        like = "%%%s%%" % category
        values += [like, like]
    if end_after:
        where.append("end_date >= ?")
        values.append(end_after)
    if end_before:
        where.append("end_date <= ?")
        values.append(end_before)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    conn = _connect()
    try:
        total = conn.execute(
            "SELECT COUNT(*) FROM bids %s" % clause, values
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT * FROM bids %s ORDER BY end_date ASC LIMIT ? OFFSET ?" % clause,
            values + [limit, offset],
        ).fetchall()
        return total, [dict(r) for r in rows]
    finally:
        conn.close()


def get_changes(since_utc_iso, kinds=None, limit=100):
    where = ["ts >= ?"]
    values = [since_utc_iso]
    if kinds:
        marks = ", ".join("?" for _ in kinds)
        where.append("kind IN (%s)" % marks)
        values += list(kinds)
    clause = "WHERE " + " AND ".join(where)
    conn = _connect()
    try:
        total = conn.execute(
            "SELECT COUNT(*) FROM events %s" % clause, values
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT * FROM events %s ORDER BY ts DESC LIMIT ?" % clause,
            values + [limit],
        ).fetchall()
        return total, [dict(r) for r in rows]
    finally:
        conn.close()


def churn_since(since_utc_iso):
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT kind, COUNT(*) AS n FROM events WHERE ts >= ? GROUP BY kind",
            (since_utc_iso,),
        ).fetchall()
        return {r["kind"]: r["n"] for r in rows}
    finally:
        conn.close()
