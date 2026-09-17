"""Crawl orchestration: baseline / delta / full / pulse runs.

Deliberately separate from app.scraper.fetch.fetch_keyword: that pager caps
at 200 pages and is keyword-shaped. The monitor pages the whole board with
its own completeness accounting (crawl_pages), crash resume, closed-marking,
and the delta convergence stop rule.

Priority rule: user keyword scrapes always win the ScrapeLock — monitor
crawls wait for it and record a 'skipped' row after their budget runs out.
"""

import json
import math
import os
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.monitor import db as monitor_db
from app.config import DATA_DIR, load_config
from app.scraper.locking import ScrapeLock
from app.scraper.session import EmptySearch, GemSession
from app.scraper.util import log

IST = ZoneInfo("Asia/Kolkata")

# Sorts that plausibly place the newest bids on page 1 (the probe verifies;
# the winner is persisted to data/monitor_state.json).
DELTA_SORT_CANDIDATES = [
    "Bid-End-Date-Newest",
    "Bid-Start-Date-Newest",
    "Bid-Number-Newest",
]

STATE_PATH = os.path.join(DATA_DIR, "monitor_state.json")


# ────────────────────────── helpers ──────────────────────────

def _utc_iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _start_of_today_ist():
    now = datetime.now(IST)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return _utc_iso(start.astimezone(timezone.utc))


def _start_of_week_ist():
    """Monday 00:00 of the current ISO week, IST, as UTC ISO."""
    now = datetime.now(IST)
    monday = now - timedelta(days=now.weekday())
    start = monday.replace(hour=0, minute=0, second=0, microsecond=0)
    return _utc_iso(start.astimezone(timezone.utc))


def load_state():
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(state):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)


def delta_sort(config):
    """The verified newest-first sort: probe state first, then config.json."""
    state = load_state()
    if state.get("delta_sort"):
        return state["delta_sort"]
    return config.get("monitor_delta_sort")


def _acquire_lock(wait_minutes):
    """Wait up to wait_minutes for the ScrapeLock (user scrapes first).

    Returns the held lock, or None when the budget runs out.
    """
    lock = ScrapeLock()
    deadline = time.monotonic() + wait_minutes * 60
    while True:
        if lock.acquire():
            return lock
        if time.monotonic() >= deadline:
            log("monitor: scrape lock still held by another process — skipping")
            return None
        time.sleep(30)


def _make_session(config):
    session = GemSession(config["user_agent"])
    session.refresh(timeout=config["timeout_seconds"])
    return session


def _fetch_page(session, config, page, sort, window=None):
    """One empty-keyword POST with the given sort/window. Returns (ok, num_found, docs).

    EmptySearch (HTTP 404) on an empty keyword means the board filter itself
    broke — surfaced as a failed page, never a silent zero.
    """
    overrides = {"sort": sort}
    if window:
        overrides["byEndDate"] = {
            "from": window.get("from", ""),
            "to": window.get("to", ""),
        }
    try:
        return session.post_page_with_retry("", page, config, filt_overrides=overrides)
    except EmptySearch:
        log("monitor: WARNING — empty-keyword page %d returned 404 (board filter?)" % page)
        return False, 0, []


def _resume_crawl_id(mode):
    """Latest crawl of this mode interrupted mid-run (finished_at NULL), else None.

    A crash leaves status='running' with finished_at NULL until init() marks
    it failed (also finished_at NULL) — both are resumable.
    """
    conn = monitor_db._connect()
    try:
        row = conn.execute(
            "SELECT id FROM crawls WHERE mode=? AND finished_at IS NULL "
            "ORDER BY id DESC LIMIT 1",
            (mode,),
        ).fetchone()
        return row["id"] if row else None
    finally:
        conn.close()


def _cutoff_iso(started_at_utc_iso, margin_hours):
    started = datetime.fromisoformat(started_at_utc_iso.replace("Z", "+00:00"))
    return _utc_iso(started - timedelta(hours=margin_hours))


def _count_drift(pct):
    """Compare DB active count with the latest numFound; returns a warning or None."""
    count = monitor_db.latest_count()
    if not count or not count["num_found"]:
        return None
    active = monitor_db.count_active()
    if not active:
        return None
    delta = abs(active - count["num_found"]) / count["num_found"] * 100
    if delta > pct:
        return "WARNING: DB/GeM count drift %.1f%% (%d active vs numFound %d)" % (
            delta, active, count["num_found"])
    return None


def _band_pages(num_found):
    """Day-of-week band of the board (fallback delta when no newest-first sort).

    Every page of the board gets revisited weekly even without a reliable
    sort; the weekly full crawl still reconciles in between.
    """
    total_pages = math.ceil(num_found / 10) if num_found else 0
    if not total_pages:
        return []
    band_width = math.ceil(total_pages / 7)
    day = datetime.now(IST).weekday()  # Monday = 0
    first = day * band_width + 1
    last = min((day + 1) * band_width, total_pages)
    return list(range(first, last + 1))


# ────────────────────────── board crawl engine ──────────────────────────

def _run_board_crawl(config, mode, sort, wait_minutes, resume=False,
                     convergence_stop=None, band=False, time_budget_hours=None,
                     crawl_id_override=None):
    """Shared engine for baseline/full (linear) and delta (convergence/band).

    Returns the crawl row id, or None when skipped (lock held).
    crawl_id_override: a pre-created crawl row (MCP trigger) — the engine
    finishes this exact row instead of creating a new one.
    """
    lock = _acquire_lock(wait_minutes)
    if lock is None:
        row_id = crawl_id_override or monitor_db.create_crawl(mode, sort_used=sort)
        monitor_db.finish_crawl(
            row_id, status="skipped", summary="scrape lock held by another process",
        )
        return None
    crawl_id = None
    try:
        session = _make_session(config)
        ok, num_found, _ = _fetch_page(session, config, 1, sort)
        if not ok:
            crawl_id = crawl_id_override or monitor_db.create_crawl(mode, sort_used=sort)
            monitor_db.finish_crawl(crawl_id, status="failed",
                                    error="GeM unreachable (page 1 failed after retries)")
            log("monitor: %s crawl FAILED — GeM unreachable" % mode)
            return crawl_id

        total_pages = math.ceil(num_found / 10) if num_found else 0
        cap = config["monitor_max_pages_full"]
        if total_pages > cap:
            log("monitor: WARNING — %d pages exceeds cap %d, truncating"
                % (total_pages, cap))
            total_pages = cap

        crawl_id = _resume_crawl_id(mode) if resume else None
        if crawl_id is None:
            crawl_id = crawl_id_override or monitor_db.create_crawl(mode, sort_used=sort)
        started_at = monitor_db.get_crawl_row(crawl_id)["started_at"]
        done_pages = monitor_db.get_crawl_pages(crawl_id) if resume else {}

        if mode == "delta":
            if band:
                pages = _band_pages(num_found)
            else:
                pages = list(range(1, min(total_pages, config["monitor_max_pages_delta"]) + 1))
        else:
            pages = list(range(1, total_pages + 1))
        pages = [p for p in pages if not done_pages.get(p, (0, 0))[0]]

        deadline = (time.monotonic() + time_budget_hours * 3600
                    if time_budget_hours else None)
        totals = {"new": 0, "modified": 0, "reappeared": 0, "docs": 0,
                  "fetched": len(done_pages),
                  "ok": sum(1 for v in done_pages.values() if v[0])}
        quiet_pages = 0

        for i, page in enumerate(pages):
            if deadline and time.monotonic() > deadline:
                log("monitor: WARNING — time budget exceeded at page %d, stopping" % page)
                break
            ok, _, page_docs = _fetch_page(session, config, page, sort)
            page_events = 0
            if ok:
                n, m, r, d = monitor_db.apply_page(crawl_id, page, page_docs)
                totals["new"] += n
                totals["modified"] += m
                totals["reappeared"] += r
                totals["docs"] += d
                totals["ok"] += 1
                page_events = n + m + r
            else:
                monitor_db.record_page(crawl_id, page, ok=False, docs=0)
            totals["fetched"] += 1

            if convergence_stop is not None:
                quiet_pages = quiet_pages + 1 if page_events == 0 else 0
                if (totals["fetched"] >= config["monitor_min_delta_pages"]
                        and quiet_pages >= convergence_stop):
                    log("monitor: convergence after %d quiet pages (page %d)"
                        % (quiet_pages, page))
                    break
            if i < len(pages) - 1:
                time.sleep(config["request_delay_seconds"])

        # Second pass over failed pages (once), then final accounting.
        known = monitor_db.get_crawl_pages(crawl_id)
        failed = [p for p in pages if not known.get(p, (0, 0))[0]]
        if failed:
            log("monitor: retrying %d failed page(s)" % len(failed))
            for page in failed:
                ok, _, page_docs = _fetch_page(session, config, page, sort)
                if ok:
                    n, m, r, d = monitor_db.apply_page(crawl_id, page, page_docs)
                    totals["new"] += n
                    totals["modified"] += m
                    totals["reappeared"] += r
                    totals["docs"] += d
                    totals["ok"] += 1
                else:
                    monitor_db.record_page(crawl_id, page, ok=False, docs=0)

        ok_end, num_found_end, _ = _fetch_page(session, config, 1, sort)
        if not ok_end:
            num_found_end = num_found

        complete = 0
        if mode in ("baseline", "full"):
            complete = 1 if (totals["fetched"] >= total_pages
                             and totals["ok"] >= total_pages) else 0

        closed = 0
        if complete:
            closed = monitor_db.mark_closed(
                crawl_id,
                _cutoff_iso(started_at, config["monitor_closed_margin_hours"]),
            )

        if totals["ok"] == 0:
            status = "failed"
        elif complete or mode == "delta":
            status = "completed"
        else:
            status = "partial"

        summary = []
        drift = _count_drift(config["monitor_pulse_drift_pct"])
        if drift:
            summary.append(drift)
        if totals["fetched"] < total_pages:
            summary.append("fetched %d/%d pages" % (totals["fetched"], total_pages))
        if not complete and mode in ("baseline", "full"):
            summary.append("no closed-marking (crawl not complete)")

        monitor_db.finish_crawl(
            crawl_id,
            status=status,
            num_found_start=num_found,
            num_found_end=num_found_end,
            pages_total=total_pages,
            pages_fetched=totals["fetched"],
            pages_ok=totals["ok"],
            convergence_pages=quiet_pages if convergence_stop is not None else None,
            docs_seen=totals["docs"],
            new_count=totals["new"],
            modified_count=totals["modified"],
            closed_count=closed,
            reappeared_count=totals["reappeared"],
            complete=complete,
            summary="; ".join(summary) or None,
        )
        monitor_db.insert_count(num_found_end, source=mode, crawl_id=crawl_id)
        log("monitor: %s crawl %d done (%s) — %d pages ok, %d new, %d modified, %d closed"
            % (mode, crawl_id, status, totals["ok"], totals["new"],
               totals["modified"], closed))
        return crawl_id
    except (RuntimeError, OSError) as err:
        row_id = crawl_id or crawl_id_override \
            or monitor_db.create_crawl(mode, sort_used=sort)
        monitor_db.finish_crawl(row_id, status="failed", error=str(err))
        log("monitor: %s crawl FAILED: %s" % (mode, err))
        return row_id
    finally:
        lock.release()


# ────────────────────────── run modes ──────────────────────────

def run_baseline(config, resume=False, crawl_id=None):
    """First full snapshot of the whole board (linear, default sort)."""
    log("monitor: starting baseline crawl")
    return _run_board_crawl(
        config, mode="baseline", sort="Bid-End-Date-Oldest",
        wait_minutes=config["monitor_lock_wait_minutes_full"],
        resume=resume,
        time_budget_hours=config["monitor_full_max_hours"],
        crawl_id_override=crawl_id,
    )


def run_full(config, resume=False, crawl_id=None):
    """Weekly full crawl: reconciles silent changes, marks disappeared, resolves details."""
    if monitor_db.completed_crawl_since("full", _start_of_week_ist()):
        log("monitor: full crawl already completed this week — exiting")
        return None
    log("monitor: starting full crawl")
    crawl_id = _run_board_crawl(
        config, mode="full", sort="Bid-End-Date-Oldest",
        wait_minutes=config["monitor_lock_wait_minutes_full"],
        resume=resume,
        time_budget_hours=config["monitor_full_max_hours"],
        crawl_id_override=crawl_id,
    )
    if crawl_id is not None and config.get("monitor_resolve_disappeared", True):
        _resolve_disappeared(config, crawl_id)
    return crawl_id


def run_delta(config, resume=False, crawl_id=None):
    """Daily incremental crawl: convergence under the verified newest-first sort."""
    if monitor_db.completed_crawl_since("delta", _start_of_today_ist()):
        log("monitor: delta crawl already completed today — exiting")
        return None
    sort = delta_sort(config)
    if sort:
        log("monitor: starting delta crawl (convergence, sort=%s)" % sort)
        return _run_board_crawl(
            config, mode="delta", sort=sort,
            wait_minutes=config["monitor_lock_wait_minutes_delta"],
            resume=resume,
            convergence_stop=config["monitor_convergence_pages"],
            crawl_id_override=crawl_id,
        )
    log("monitor: no verified delta sort — running band fallback")
    return _run_board_crawl(
        config, mode="delta", sort="Bid-End-Date-Oldest",
        wait_minutes=config["monitor_lock_wait_minutes_delta"],
        resume=resume,
        band=True,
        crawl_id_override=crawl_id,
    )


def run_pulse(config):
    """One page-1 request: record numFound, warn on drift. Try-once lock."""
    lock = ScrapeLock()
    if not lock.acquire():
        log("monitor: pulse skipped (scrape lock held)")
        return
    try:
        session = _make_session(config)
        ok, num_found, _ = _fetch_page(session, config, 1, "Bid-End-Date-Oldest")
        if not ok:
            log("monitor: pulse FAILED (GeM unreachable)")
            return
        prev = monitor_db.latest_count()
        monitor_db.insert_count(num_found, source="pulse")
        log("monitor: pulse numFound=%d" % num_found)
        if prev and prev["num_found"]:
            delta = abs(num_found - prev["num_found"]) / prev["num_found"] * 100
            if delta > config["monitor_pulse_drift_pct"]:
                log("monitor: WARNING — numFound drifted %.1f%% (%d -> %d)"
                    % (delta, prev["num_found"], num_found))
        drift = _count_drift(config["monitor_pulse_drift_pct"])
        if drift:
            log("monitor: %s" % drift)
    finally:
        lock.release()


def _resolve_disappeared(config, crawl_id):
    """After a full crawl closes bids, fetch details for up to N of them to
    capture the final status (Awarded/Closed/Cancelled). The on-demand
    pattern applied to the highest-value closed bids — never a bulk crawl."""
    from app.monitor import detail
    cap = config.get("monitor_resolve_disappeared_cap", 300)
    conn = monitor_db._connect()
    try:
        rows = conn.execute(
            "SELECT bid_id FROM events WHERE crawl_id=? AND kind='closed' LIMIT ?",
            (crawl_id, cap),
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        return
    log("monitor: resolving disappeared details for %d closed bids" % len(rows))
    for r in rows:
        try:
            detail.fetch_and_cache(r["bid_id"], config, force=True)
        except Exception as err:
            log("monitor: detail resolve failed for %s: %s" % (r["bid_id"], err))


if __name__ == "__main__":  # pragma: no cover — the thin CLI lives in cli.py
    cfg = load_config()
    monitor_db.init()
    run_baseline(cfg)
