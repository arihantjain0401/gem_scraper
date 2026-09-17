"""One-off: map where the gap lives across end-date strips (9 requests)."""
import sqlite3
import sys
import time
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import load_config
from app.scraper.session import GemSession

IST = ZoneInfo("Asia/Kolkata")
cfg = load_config()
s = GemSession(cfg["user_agent"])
s.refresh()
conn = sqlite3.connect("data/monitor.db")
today = datetime.now(IST).date()

print("%-12s %-12s %10s %10s %10s" % ("strip (IST)", "to", "geM_now", "db_active", "gap"))
total_gem = total_db = total_gap = 0
for i in range(10):
    start = today + timedelta(days=7 * i)
    end = today + timedelta(days=7 * (i + 1))
    frm = start.strftime("%d-%m-%Y")
    to = end.strftime("%d-%m-%Y")
    time.sleep(2)
    ok, n, docs = s.post_page_with_retry(
        "", 1, cfg,
        filt_overrides={"sort": "Bid-End-Date-Oldest",
                        "byEndDate": {"from": frm, "to": to}})
    utc_from = datetime.combine(start, dtime.min, tzinfo=IST) \
        .astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    utc_to = datetime.combine(end, dtime.min, tzinfo=IST) \
        .astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    db_n = conn.execute(
        "SELECT COUNT(*) FROM bids WHERE is_active=1 AND end_date >= ? AND end_date < ?",
        (utc_from, utc_to)).fetchone()[0]
    gap = n - db_n
    total_gem += n
    total_db += db_n
    total_gap += gap
    print("%-12s %-12s %10d %10d %10d" % (start.isoformat(), end.isoformat(), n, db_n, gap))

print("-" * 58)
print("%-12s %-12s %10d %10d %10d" % ("TOTAL", "", total_gem, total_db, total_gap))
conn.close()
