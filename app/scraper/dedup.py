"""Seen-bid dedupe state, preserved verbatim from the original scraper.

seen_bids.sqlite lives at the project root and is shared by the CLI, the
scheduler, and the web app — "is_new" means "not seen by any previous run".
"""

import sqlite3
from datetime import datetime

from app.config import SEEN_DB_PATH


def load_seen():
    conn = sqlite3.connect(SEEN_DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS seen (bid_id TEXT PRIMARY KEY, first_seen TEXT)"
    )
    seen = {row[0] for row in conn.execute("SELECT bid_id FROM seen")}
    return conn, seen


def mark_seen(conn, bid_ids):
    today = datetime.now().strftime("%Y-%m-%d")
    conn.executemany(
        "INSERT OR IGNORE INTO seen (bid_id, first_seen) VALUES (?, ?)",
        [(bid_id, today) for bid_id in bid_ids],
    )
    conn.commit()
