"""SQLite persistence for tasks, runs, and results (data/app.db).

Separate from seen_bids.sqlite, which remains the dedupe store for is_new.
WAL mode, row_factory=Row, one connection per operation (thread-safe).
"""

import json
import os
import sqlite3
from datetime import datetime, time as dtime, timedelta, timezone

from werkzeug.security import check_password_hash, generate_password_hash

from app.config import APP_DB_PATH, DATA_DIR
from app.scraper.mapping import split_ist

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
  id              TEXT PRIMARY KEY,
  name            TEXT NOT NULL,
  keywords        TEXT NOT NULL,
  schedule_hour   INTEGER NOT NULL,
  schedule_minute INTEGER NOT NULL,
  enabled         INTEGER NOT NULL DEFAULT 1,
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id     TEXT,
  mode        TEXT NOT NULL DEFAULT 'adhoc',
  keywords    TEXT NOT NULL,
  status      TEXT NOT NULL,
  started_at  TEXT NOT NULL,
  finished_at TEXT,
  error       TEXT,
  summary     TEXT,
  total_count INTEGER,
  new_count   INTEGER
);
CREATE INDEX IF NOT EXISTS idx_runs_started ON runs(started_at DESC);
CREATE TABLE IF NOT EXISTS logins (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  logged_in_at TEXT NOT NULL,
  success      INTEGER NOT NULL,
  ip           TEXT,
  user_agent   TEXT,
  device_label TEXT,
  username     TEXT
);
CREATE TABLE IF NOT EXISTS users (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  username      TEXT NOT NULL UNIQUE,
  display_name  TEXT NOT NULL,
  password_hash TEXT NOT NULL,
  role          TEXT NOT NULL DEFAULT "limited",
  created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS results (
  run_id       INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  bid_id       TEXT NOT NULL,
  bid_number   TEXT,
  end_date     TEXT,
  end_date_ist TEXT,
  end_time_ist TEXT,
  organization TEXT,
  location     TEXT,
  item         TEXT,
  quantity     TEXT,
  url          TEXT,
  is_new       INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (run_id, bid_id)
);
"""


def utcnow_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(APP_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init():
    """Create schema and mark stale 'running' rows as failed (server restarted mid-run)."""
    conn = connect()
    conn.executescript(SCHEMA)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(logins)")]
    if "username" not in cols:
        conn.execute("ALTER TABLE logins ADD COLUMN username TEXT")
    conn.execute(
        "UPDATE runs SET status='failed', finished_at=?, error=? WHERE status='running'",
        (utcnow_iso(), "interrupted by web server restart"),
    )
    conn.commit()
    conn.close()
    if not has_users():
        seed_admin_from_password_file()


# --- runs ---

def create_run(task_id=None, mode="adhoc", keywords=None):
    conn = connect()
    cur = conn.execute(
        "INSERT INTO runs (task_id, mode, keywords, status, started_at) VALUES (?,?,?,?,?)",
        (task_id, mode, json.dumps(keywords or []), "running", utcnow_iso()),
    )
    conn.commit()
    run_id = cur.lastrowid
    conn.close()
    return run_id


def finish_run(run_id, status, total_count=None, new_count=None, error=None):
    conn = connect()
    fields, values = ["status=?", "finished_at=?"], [status, utcnow_iso()]
    if total_count is not None:
        fields.append("total_count=?")
        values.append(total_count)
    if new_count is not None:
        fields.append("new_count=?")
        values.append(new_count)
    if error is not None:
        fields.append("error=?")
        values.append(error)
    values.append(run_id)
    conn.execute("UPDATE runs SET %s WHERE id=?" % ", ".join(fields), values)
    conn.commit()
    conn.close()


def create_finished_run(task_id=None, mode="adhoc", keywords=None, status="skipped", error=None):
    """Insert an already-finished run row (e.g. skipped because the lock was busy)."""
    run_id = create_run(task_id=task_id, mode=mode, keywords=keywords)
    finish_run(run_id, status, error=error)
    return run_id


def get_run(run_id):
    conn = connect()
    row = conn.execute(
        "SELECT r.*, t.name AS task_name FROM runs r "
        "LEFT JOIN tasks t ON t.id = r.task_id WHERE r.id=?",
        (run_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def set_summary(run_id, text):
    conn = connect()
    conn.execute("UPDATE runs SET summary=? WHERE id=?", (text, run_id))
    conn.commit()
    conn.close()


def list_runs(status=None, mode=None, limit=50, offset=0):
    conn = connect()
    where, values = [], []
    if status:
        where.append("r.status=?")
        values.append(status)
    if mode:
        where.append("r.mode=?")
        values.append(mode)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    total = conn.execute(
        "SELECT COUNT(*) AS n FROM runs r %s" % clause, values
    ).fetchone()["n"]
    rows = conn.execute(
        "SELECT r.*, t.name AS task_name FROM runs r "
        "LEFT JOIN tasks t ON t.id = r.task_id "
        "%s ORDER BY r.id DESC LIMIT ? OFFSET ?" % clause,
        values + [limit, offset],
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows], total


# --- results ---

def insert_results(run_id, rows17):
    """Persist the 7-column view of every 17-column row for one run."""
    conn = connect()
    conn.executemany(
        "INSERT OR REPLACE INTO results "
        "(run_id, bid_id, bid_number, end_date, end_date_ist, end_time_ist, "
        " organization, location, item, quantity, url, is_new) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [_result_tuple(run_id, r) for r in rows17],
    )
    conn.commit()
    conn.close()


def _result_tuple(run_id, row):
    end_date_ist, end_time_ist = split_ist(row["end_date"])
    return (
        run_id, row["bid_id"], row["bid_number"], row["end_date"],
        end_date_ist, end_time_ist,
        row["buyer_department"] or row["buyer_ministry"],
        "",
        row["title"] or row["category"],
        row["total_quantity"],
        row["url"],
        row["is_new"],
    )


def get_results(run_id, limit=200, offset=0):
    conn = connect()
    rows = conn.execute(
        "SELECT * FROM results WHERE run_id=? "
        "ORDER BY end_date_ist, end_time_ist, bid_number LIMIT ? OFFSET ?",
        (run_id, limit, offset),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def count_results(run_id):
    conn = connect()
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM results WHERE run_id=?", (run_id,)
    ).fetchone()["n"]
    conn.close()
    return n


# --- users ---

def has_users():
    conn = connect()
    n = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
    conn.close()
    return n > 0


def seed_admin_from_password_file():
    """One-time migration: create the admin from credentials/web_password.env."""
    from app.config import load_web_password
    password = load_web_password()
    if not password:
        return False
    create_user("arihant", "Arihant Jain", password, "admin")
    return True


def create_user(username, display_name, password, role="limited"):
    conn = connect()
    conn.execute(
        "INSERT INTO users (username, display_name, password_hash, role, created_at) "
        "VALUES (?,?,?,?,?)",
        (username, display_name, generate_password_hash(password), role, utcnow_iso()),
    )
    conn.commit()
    conn.close()


def get_user(username):
    conn = connect()
    row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    conn.close()
    return dict(row) if row else None


def verify_user(username, password):
    """Return the user dict when username+password match, else None."""
    user = get_user(username)
    if user and check_password_hash(user["password_hash"], password):
        return user
    return None


def list_users():
    conn = connect()
    rows = conn.execute(
        "SELECT id, username, display_name, role, created_at FROM users ORDER BY id"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_user(username):
    conn = connect()
    n = conn.execute("DELETE FROM users WHERE username=?", (username,)).rowcount
    conn.commit()
    conn.close()
    return n


def set_user_password(username, password):
    conn = connect()
    n = conn.execute(
        "UPDATE users SET password_hash=? WHERE username=?",
        (generate_password_hash(password), username),
    ).rowcount
    conn.commit()
    conn.close()
    return n


# --- login audit ---

def record_login(success, ip, user_agent, device_label, username=None):
    """Record a login attempt. Never stores the attempted password."""
    conn = connect()
    conn.execute(
        "INSERT INTO logins (logged_in_at, success, ip, user_agent, device_label, "
        "username) VALUES (?,?,?,?,?,?)",
        (utcnow_iso(), 1 if success else 0, ip or "", user_agent or "",
         device_label or "", username or ""),
    )
    conn.commit()
    conn.close()


def list_logins(limit=50):
    conn = connect()
    rows = conn.execute(
        "SELECT * FROM logins ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def task_ran_today(task_id):
    """True if the task has a scheduled run whose started_at is today (IST).

    Used by the in-app scheduler to decide whether the daily run has already
    fired (or was skipped) for the current IST day.
    """
    from zoneinfo import ZoneInfo
    ist = ZoneInfo("Asia/Kolkata")
    now_ist = datetime.now(ist)
    day_start_utc = datetime.combine(now_ist.date(), dtime.min,
                                    tzinfo=ist).astimezone(timezone.utc)
    conn = connect()
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM runs WHERE task_id=? AND mode='scheduled' "
        "AND started_at >= ?",
        (task_id, day_start_utc.strftime("%Y-%m-%dT%H:%M:%SZ")),
    ).fetchone()["n"]
    conn.close()
    return n > 0


def count_recent_failures(ip, minutes=15):
    """Failed login attempts from this IP in the last N minutes.

    The cutoff is computed in Python so it matches the stored ISO UTC format
    ('YYYY-MM-DDTHH:MM:SSZ') and compares correctly as a string.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    conn = connect()
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM logins WHERE ip=? AND success=0 "
        "AND logged_in_at >= ?",
        (ip, cutoff),
    ).fetchone()["n"]
    conn.close()
    return n


# --- tasks ---

def create_task(task_id, name, keywords, hour, minute):
    now = utcnow_iso()
    conn = connect()
    conn.execute(
        "INSERT INTO tasks (id, name, keywords, schedule_hour, schedule_minute, "
        "enabled, created_at, updated_at) VALUES (?,?,?,?,?,1,?,?)",
        (task_id, name, json.dumps(keywords), hour, minute, now, now),
    )
    conn.commit()
    conn.close()


def get_task(task_id):
    conn = connect()
    row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_tasks(include_disabled=True):
    conn = connect()
    clause = "" if include_disabled else "WHERE enabled=1"
    tasks = [dict(r) for r in conn.execute(
        "SELECT t.*, "
        "  (SELECT r.id FROM runs r WHERE r.task_id = t.id "
        "   ORDER BY r.id DESC LIMIT 1) AS last_run_id "
        "FROM tasks t %s ORDER BY t.id" % clause
    ).fetchall()]
    last = {}
    run_ids = [t["last_run_id"] for t in tasks if t["last_run_id"]]
    if run_ids:
        placeholders = ",".join("?" * len(run_ids))
        for r in conn.execute(
            "SELECT id, status, started_at, finished_at, error, total_count, new_count "
            "FROM runs WHERE id IN (%s)" % placeholders, run_ids
        ):
            last[r["id"]] = dict(r)
    conn.close()
    for t in tasks:
        t["last_run"] = last.get(t["last_run_id"])
    return tasks


def update_task_keywords(task_id, keywords):
    conn = connect()
    conn.execute(
        "UPDATE tasks SET keywords=?, updated_at=? WHERE id=?",
        (json.dumps(keywords), utcnow_iso(), task_id),
    )
    conn.commit()
    conn.close()


def disable_task(task_id):
    conn = connect()
    conn.execute(
        "UPDATE tasks SET enabled=0, updated_at=? WHERE id=?",
        (utcnow_iso(), task_id),
    )
    conn.commit()
    conn.close()


def enable_task(task_id, hour, minute):
    """Re-enable a disabled task with a new schedule (CLI recreate path)."""
    conn = connect()
    conn.execute(
        "UPDATE tasks SET enabled=1, schedule_hour=?, schedule_minute=?, updated_at=? "
        "WHERE id=?",
        (hour, minute, utcnow_iso(), task_id),
    )
    conn.commit()
    conn.close()
