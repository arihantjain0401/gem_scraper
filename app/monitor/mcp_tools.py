"""The 7 MCP tools over the monitor DB (the primary programmatic surface).

Same registration idiom as Bento's register_files_tools(registry): the
server builds a ToolRegistry and calls register_monitor_tools(registry,
config). Handlers never raise — every error returns fail(...).

Only get_bid_detail (with refresh, or an expired/missing cache entry) hits
GeM. Everything else is served from data/monitor.db — that is the point.
"""

import threading
from datetime import datetime, timedelta, timezone

from app.monitor import db as monitor_db
from app.config import BID_URL
from app.monitor import crawl, detail
from app.monitor.mcp_contract import fail, ok
from app.monitor.registry import ToolDefinition
from app.scraper.mapping import split_ist

# In-process crawl guard: only one monitor crawl thread at a time.
_CRAWL_LOCK = threading.Lock()
_CURRENT = {"crawl_id": None, "mode": None, "thread": None}


def _utc_iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_since(since):
    """Accept ISO UTC or '24h'/'7d'/'1d' style strings; return UTC ISO cutoff."""
    if isinstance(since, str) and since.endswith(("h", "d")):
        try:
            amount = int(since[:-1])
            unit = timedelta(hours=amount) if since.endswith("h") else timedelta(days=amount)
            return _utc_iso(datetime.now(timezone.utc) - unit)
        except ValueError:
            pass
    return since


def _format_bid(row):
    title = row["category_name"] or ""
    if row["bd_category_name"] and row["bd_category_name"] not in title:
        title = "%s (%s)" % (title, row["bd_category_name"])
    end_date_ist, end_time_ist = split_ist(row["end_date"])
    return {
        "b_id": row["b_id"],
        "bid_number": row["bid_number"],
        "title": title,
        "category": row["bd_category_name"],
        "ministry": row["ministry"],
        "department": row["department"],
        "end_date": row["end_date"],
        "end_date_ist": end_date_ist,
        "end_time_ist": end_time_ist,
        "quantity": row["total_quantity"],
        "b_status": row["b_status"],
        "is_active": bool(row["is_active"]),
        "first_seen": row["first_seen"],
        "last_seen": row["last_seen"],
        "url": BID_URL + str(row["b_id"]),
    }


# ────────────────────────── handlers ──────────────────────────

def _search_bids(params):
    total, rows = monitor_db.search_bids(
        q=params.get("q"),
        status=params.get("status", "ongoing"),
        organization=params.get("organization"),
        department=params.get("department"),
        category=params.get("category"),
        end_after=params.get("end_after"),
        end_before=params.get("end_before"),
        limit=min(int(params.get("limit", 50)), 500),
        offset=max(int(params.get("offset", 0)), 0),
    )
    return ok({"count": total, "bids": [_format_bid(r) for r in rows]})


def _get_changes(params):
    since = _parse_since(params.get("since", "24h"))
    kinds = params.get("kinds")
    if isinstance(kinds, str):
        kinds = [k.strip() for k in kinds.split(",") if k.strip()]
    total, rows = monitor_db.get_changes(
        since, kinds=kinds, limit=min(int(params.get("limit", 100)), 1000))
    return ok({
        "count": total,
        "since": since,
        "events": [
            {
                "id": r["id"], "ts": r["ts"], "bid_id": r["bid_id"],
                "bid_number": r["bid_number"], "kind": r["kind"],
                "fields": r["fields"],  # JSON string of {field: [old, new]}
            }
            for r in rows
        ],
    })


def _get_bid_detail(params):
    b_id = str(params.get("b_id", ""))
    if not b_id:
        return fail("b_id is required")
    try:
        result = detail.fetch_and_cache(
            b_id, _config(), force=bool(params.get("refresh", False)))
        return ok(result)
    except Exception as err:
        return fail(err)


def _get_stats(params):
    active = monitor_db.count_active()
    conn = monitor_db._connect()
    try:
        total = conn.execute("SELECT COUNT(*) FROM bids").fetchone()[0]
    finally:
        conn.close()
    count = monitor_db.latest_count()
    week_ago = _utc_iso(datetime.now(timezone.utc) - timedelta(days=7))
    churn = monitor_db.churn_since(week_ago)
    fetched, cache_ok, cache_failed = monitor_db.detail_cache_stats()
    running = (_CURRENT["thread"] is not None and _CURRENT["thread"].is_alive()) \
        or monitor_db.running_crawl() is not None
    return ok({
        "active_bids": active,
        "total_bids": total,
        "last_num_found": count["num_found"] if count else None,
        "num_found_at": count["ts"] if count else None,
        "num_found_source": count["source"] if count else None,
        "churn_7d": {
            "new": churn.get("new", 0),
            "modified": churn.get("modified", 0),
            "closed": churn.get("closed", 0),
            "reappeared": churn.get("reappeared", 0),
        },
        "last_crawls": [
            {
                "id": c["id"], "mode": c["mode"], "status": c["status"],
                "started_at": c["started_at"], "finished_at": c["finished_at"],
                "pages_ok": c["pages_ok"], "pages_total": c["pages_total"],
                "complete": bool(c["complete"]),
                "new": c["new_count"], "modified": c["modified_count"],
                "closed": c["closed_count"],
                "summary": c["summary"],
            }
            for c in _recent_crawls(3)
        ],
        "detail_cache": {"fetched": fetched, "ok": cache_ok, "failed": cache_failed},
        "running": running,
    })


def _recent_crawls(n):
    conn = monitor_db._connect()
    try:
        rows = conn.execute(
            "SELECT * FROM crawls ORDER BY id DESC LIMIT ?", (n,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _trigger(mode, params):
    if _CURRENT["thread"] is not None and _CURRENT["thread"].is_alive():
        return ok({"crawl_id": _CURRENT["crawl_id"], "status": "busy"})
    with _CRAWL_LOCK:
        if _CURRENT["thread"] is not None and _CURRENT["thread"].is_alive():
            return ok({"crawl_id": _CURRENT["crawl_id"], "status": "busy"})
        if monitor_db.running_crawl() is not None:
            return ok({"crawl_id": None, "status": "crawl_running_elsewhere"})
        cfg = _config()
        # Catch-up check up front so we never leave a dangling 'running' row.
        if mode == "delta" and monitor_db.completed_crawl_since(
                "delta", crawl._start_of_today_ist()):
            return ok({"crawl_id": None, "status": "already_done_today"})
        if mode == "full" and monitor_db.completed_crawl_since(
                "full", crawl._start_of_week_ist()):
            return ok({"crawl_id": None, "status": "already_done_this_week"})

        # The row is created now so the caller gets a pollable crawl_id;
        # the crawl engine finishes this exact row (crawl_id_override).
        crawl_id = monitor_db.create_crawl(mode)
        _CURRENT["crawl_id"] = crawl_id
        _CURRENT["mode"] = mode

        def _worker():
            try:
                run = crawl.run_delta if mode == "delta" else crawl.run_full
                result = run(cfg, resume=False, crawl_id=crawl_id)
                if result is None:
                    monitor_db.finish_crawl(
                        crawl_id, status="skipped",
                        summary="already completed for this period")
            except Exception as err:
                monitor_db.finish_crawl(crawl_id, status="failed", error=str(err))
            finally:
                _CURRENT["thread"] = None
                _CURRENT["crawl_id"] = None

        thread = threading.Thread(target=_worker, daemon=True)
        _CURRENT["thread"] = thread
        thread.start()
        return ok({"crawl_id": crawl_id, "status": "started"})


def _trigger_delta(params):
    return _trigger("delta", params)


def _trigger_full(params):
    return _trigger("full", params)


def _get_crawl_status(params):
    crawl_id = params.get("crawl_id")
    running = (_CURRENT["thread"] is not None and _CURRENT["thread"].is_alive()) \
        or monitor_db.running_crawl() is not None
    if crawl_id:
        conn = monitor_db._connect()
        try:
            row = conn.execute("SELECT * FROM crawls WHERE id=?", (crawl_id,)).fetchone()
        finally:
            conn.close()
        if row is None:
            return fail("crawl %s not found" % crawl_id)
        return ok({"running": running, "crawl": dict(row)})
    return ok({
        "running": running,
        "current": {"crawl_id": _CURRENT["crawl_id"], "mode": _CURRENT["mode"]}
        if running else None,
        "last": monitor_db.latest_crawl(),
    })


# ────────────────────────── registration ──────────────────────────

_CONFIG = None


def _config():
    return _CONFIG


def register_monitor_tools(registry, config):
    """Register the 7 tools on the given ToolRegistry (Bento idiom)."""
    global _CONFIG
    _CONFIG = config
    registry.register(ToolDefinition(
        name="search_bids",
        description="Search the local full-board GeM bid index (SQLite; no GeM call).",
        input_schema={
            "type": "object",
            "properties": {
                "q": {"type": "string", "description": "Free-text across bid number, category, ministry, department"},
                "status": {"type": "string", "enum": ["ongoing", "closed", "all"], "description": "Lifecycle filter (default ongoing)"},
                "organization": {"type": "string"},
                "department": {"type": "string"},
                "category": {"type": "string"},
                "end_after": {"type": "string", "description": "ISO end_date >= this (UTC ISO)"},
                "end_before": {"type": "string", "description": "ISO end_date <= this (UTC ISO)"},
                "limit": {"type": "integer", "maximum": 500},
                "offset": {"type": "integer"},
            },
        },
        handler=_search_bids,
        tags=["monitor", "read"],
    ))
    registry.register(ToolDefinition(
        name="get_changes",
        description="Change events since a cutoff: new / modified / closed / reappeared bids.",
        input_schema={
            "type": "object",
            "properties": {
                "since": {"type": "string", "description": "ISO UTC or '24h'/'7d'"},
                "kinds": {"type": "array", "items": {"type": "string", "enum": ["new", "modified", "closed", "reappeared"]}},
                "limit": {"type": "integer"},
            },
        },
        handler=_get_changes,
        tags=["monitor", "read"],
    ))
    registry.register(ToolDefinition(
        name="get_bid_detail",
        description="Full detail for one bid: cached copy, or on-demand GeM fetch + parse + cache.",
        input_schema={
            "type": "object",
            "required": ["b_id"],
            "properties": {
                "b_id": {"type": "string"},
                "refresh": {"type": "boolean", "description": "Force a fresh GeM fetch"},
            },
        },
        handler=_get_bid_detail,
        tags=["monitor", "gem"],
    ))
    registry.register(ToolDefinition(
        name="get_stats",
        description="Monitor health: active/total bids, last numFound, 7-day churn, last crawls, cache stats.",
        input_schema={"type": "object", "properties": {}},
        handler=_get_stats,
        tags=["monitor", "read"],
    ))
    registry.register(ToolDefinition(
        name="trigger_delta",
        description="Start a delta (incremental) crawl in the background.",
        input_schema={"type": "object", "properties": {}},
        handler=_trigger_delta,
        tags=["monitor", "control"],
    ))
    registry.register(ToolDefinition(
        name="trigger_full",
        description="Start a full reconciliation crawl in the background (hours).",
        input_schema={"type": "object", "properties": {}},
        handler=_trigger_full,
        tags=["monitor", "control"],
    ))
    registry.register(ToolDefinition(
        name="get_crawl_status",
        description="Current/last crawl status, or one specific crawl by id.",
        input_schema={
            "type": "object",
            "properties": {"crawl_id": {"type": "integer"}},
        },
        handler=_get_crawl_status,
        tags=["monitor", "read"],
    ))
