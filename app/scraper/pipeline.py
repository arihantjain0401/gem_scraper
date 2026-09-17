"""Shared scrape orchestration used by the CLI, the scheduler, and the web app.

Preserves the original gem_scraper.py flow exactly: session refresh, per-keyword
pagination, cross-keyword dedupe keyed on bid_id, is_new via seen_bids.sqlite,
rows sorted by end_date, optional CSV output, mark seen.
"""

import os
from datetime import datetime

from app.config import OUTPUT_DIR, load_config
from app.scraper.csv_writer import write_csv as write_csv_rows
from app.scraper.dedup import load_seen, mark_seen
from app.scraper.fetch import fetch_keyword
from app.scraper.mapping import build_row
from app.scraper.session import GemSession
from app.scraper.util import log, unwrap


def run_scrape(keywords, progress_cb=None, write_csv=True):
    """Scrape every keyword.

    Returns {"rows": [17-column dicts], "new_count": int, "warnings": [str]}.
    """
    config = load_config()
    warnings = []

    log("=== GeM BidPlus scraper start ===")
    log("keywords: %s" % ", ".join(keywords))

    session = GemSession(config["user_agent"])
    session.refresh()
    log("session established (CSRF token obtained)")

    raw_docs = []
    for keyword in keywords:
        docs, kw_warnings = fetch_keyword(session, keyword, config, progress_cb=progress_cb)
        raw_docs.extend(docs)
        warnings.extend(kw_warnings)

    # Dedupe across keywords, keyed on bid_id (fall back to id).
    by_id = {}
    for doc in raw_docs:
        bid_id = str(unwrap(doc.get("b_id")) or unwrap(doc.get("id")))
        if bid_id and bid_id not in by_id:
            by_id[bid_id] = doc

    conn, seen = load_seen()
    rows = [build_row(doc, bid_id not in seen) for bid_id, doc in by_id.items()]
    rows.sort(key=lambda r: r["end_date"])

    full_path = new_path = None
    if write_csv:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        today = datetime.now().strftime("%Y-%m-%d")
        full_path = os.path.join(OUTPUT_DIR, "bids_%s.csv" % today)
        new_path = os.path.join(OUTPUT_DIR, "new_bids_%s.csv" % today)
        write_csv_rows(full_path, rows)
        write_csv_rows(new_path, [r for r in rows if r["is_new"] == 1])

    mark_seen(conn, list(by_id.keys()))
    conn.close()

    new_count = sum(1 for r in rows if r["is_new"] == 1)
    log("done: %d unique bids, %d new" % (len(rows), new_count))
    if full_path:
        log("full file -> %s" % full_path)
        log("new-only file -> %s" % new_path)
    for warning in warnings:
        log("  warning: %s" % warning)
    log("=== GeM BidPlus scraper end ===")

    return {"rows": rows, "new_count": new_count, "warnings": warnings}
