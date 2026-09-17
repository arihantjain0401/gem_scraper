"""Thin CLI for humans and launchd: `python -m app.monitor.cli <cmd>`.

Every command calls core functions only — no logic lives here. The MCP
server is the primary programmatic surface; this CLI is the entry point
for the launchd jobs and for debugging on the iMac.
"""

import argparse
import json
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.monitor import db as monitor_db
from app.config import load_config
from app.monitor import crawl, detail, probe
from app.scraper.util import log

IST = ZoneInfo("Asia/Kolkata")


def _cmd_probe(args):
    return probe.run_probe(args.config, aggressive=args.aggressive)


def _cmd_baseline(args):
    crawl.run_baseline(args.config, resume=args.resume)
    return 0


def _cmd_delta(args):
    return 0 if crawl.run_delta(args.config, resume=args.resume) is not None else 0


def _cmd_full(args):
    cfg = dict(args.config)
    if args.no_resolve_disappeared:
        cfg["monitor_resolve_disappeared"] = False
    return 0 if crawl.run_full(cfg, resume=args.resume) is not None else 0


def _cmd_pulse(args):
    crawl.run_pulse(args.config)
    return 0


def _cmd_detail(args):
    result = detail.fetch_and_cache(args.b_id, args.config, force=args.refresh)
    print(json.dumps(result, indent=2, default=str))
    return 0 if result.get("ok") else 1


def _cmd_status(args):
    count = monitor_db.latest_count()
    active = monitor_db.count_active()
    conn = monitor_db._connect()
    try:
        total = conn.execute("SELECT COUNT(*) FROM bids").fetchone()[0]
    finally:
        conn.close()
    fetched, cache_ok, cache_failed = monitor_db.detail_cache_stats()

    print("=== GeM monitor status (IST) ===")
    print("bids:      %d active / %d total" % (active, total))
    if count:
        print("numFound:  %d at %s (%s)" % (
            count["num_found"],
            datetime.fromisoformat(count["ts"].replace("Z", "+00:00"))
            .astimezone(IST).strftime("%Y-%m-%d %H:%M"),
            count["source"]))
        if active:
            drift = abs(active - count["num_found"]) / count["num_found"] * 100
            print("drift:     %.1f%% (DB vs numFound)" % drift)
    for label, days in (("24h", 1), ("7d", 7)):
        since = (datetime.now(IST) - timedelta(days=days)) \
            .astimezone(IST).strftime("%Y-%m-%dT%H:%M:%S%z")
        churn = monitor_db.churn_since(_utc_from_ist(since))
        print("events %s: %s" % (label, churn or {}))
    print("detail cache: %d fetched (%d ok, %d failed)"
          % (fetched, cache_ok, cache_failed))
    for c in _recent(5):
        print("  crawl #%d %-9s %-9s %s pages_ok=%s/%s complete=%s new=%s mod=%s closed=%s"
              % (c["id"], c["mode"], c["status"],
                 _ist_short(c["started_at"]), c["pages_ok"], c["pages_total"],
                 c["complete"], c["new_count"], c["modified_count"],
                 c["closed_count"]))
        if c["summary"]:
            print("           %s" % c["summary"])
    state = crawl.load_state()
    if state:
        print("probe state: delta_sort=%s gates=%s"
              % (state.get("delta_sort"), state.get("gates", {})))
    return 0


def _utc_from_ist(ist_iso):
    dt = datetime.fromisoformat(ist_iso)
    return dt.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ist_short(utc_iso):
    if not utc_iso:
        return "-"
    try:
        return datetime.fromisoformat(utc_iso.replace("Z", "+00:00")) \
            .astimezone(IST).strftime("%m-%d %H:%M")
    except ValueError:
        return utc_iso


def _recent(n):
    conn = monitor_db._connect()
    try:
        rows = conn.execute(
            "SELECT * FROM crawls ORDER BY id DESC LIMIT ?", (n,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _cmd_serve(args):
    from app.monitor import mcp_server
    mcp_server.main()
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="app.monitor.cli",
                                     description="GeM full-board monitor")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("probe", help="P1-P8 probes + design gates")
    p.add_argument("--aggressive", action="store_true",
                   help="P8 rapid-burst test (ban risk)")
    p.set_defaults(fn=_cmd_probe)

    p = sub.add_parser("baseline", help="first full snapshot of the board")
    p.add_argument("--resume", action="store_true")
    p.set_defaults(fn=_cmd_baseline)

    p = sub.add_parser("delta", help="daily incremental crawl")
    p.add_argument("--resume", action="store_true")
    p.set_defaults(fn=_cmd_delta)

    p = sub.add_parser("full", help="weekly full reconciliation crawl")
    p.add_argument("--resume", action="store_true")
    p.add_argument("--no-resolve-disappeared", action="store_true")
    p.set_defaults(fn=_cmd_full)

    sub.add_parser("pulse", help="one numFound check").set_defaults(fn=_cmd_pulse)

    p = sub.add_parser("detail", help="fetch + cache one bid's detail page")
    p.add_argument("b_id")
    p.add_argument("--refresh", action="store_true")
    p.set_defaults(fn=_cmd_detail)

    sub.add_parser("status", help="monitor DB status in IST") \
        .set_defaults(fn=_cmd_status)

    sub.add_parser("serve", help="run the MCP HTTP server (launchd entry)") \
        .set_defaults(fn=_cmd_serve)

    args = parser.parse_args(argv)
    config = load_config()
    args.config = config
    if args.cmd != "probe":
        monitor_db.init()
    try:
        return args.fn(args)
    except Exception as err:
        log("monitor: command '%s' FAILED: %s" % (args.cmd, err))
        return 1


if __name__ == "__main__":
    sys.exit(main())
