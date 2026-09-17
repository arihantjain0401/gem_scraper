"""Probe phase (step 0): verify GeM board behavior before trusting the design.

Run once on the iMac: `python -m app.monitor.cli probe [--aggressive]`.
Each probe answers one design gate (A-F); the report is printed and saved
to output/probe_report_<date>.json, and the chosen delta sort (gate B) is
persisted to data/monitor_state.json for crawl.py.

Politeness: single session, >=2 s between probe POSTs, deep paging tested
once. --aggressive (P8) fires 5 rapid requests and carries ban risk.
"""

import json
import os
import re
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import OUTPUT_DIR, load_config
from app.monitor import detail
from app.monitor.crawl import DELTA_SORT_CANDIDATES, save_state
from app.scraper.locking import ScrapeLock
from app.scraper.session import EmptySearch, GemSession
from app.scraper.util import log

IST = ZoneInfo("Asia/Kolkata")

ALL_SORT_CANDIDATES = [
    "Bid-End-Date-Oldest",
    "Bid-End-Date-Newest",
    "Bid-Start-Date-Oldest",
    "Bid-Start-Date-Newest",
    "Bid-Number-Oldest",
    "Bid-Number-Newest",
    "Newest",
    "Oldest",
    "Relevance",
]

_BID_NO_RE = re.compile(r"GEM[/-]\d{4}[/-][A-Z]+[/-](\d+)", re.IGNORECASE)


def _post(session, config, page, sort=None, window=None, search_type=None):
    overrides = {"sort": sort} if sort else None
    if window is not None:
        overrides = dict(overrides or {})
        overrides["byEndDate"] = {"from": window[0], "to": window[1]}
    params = {"searchType": search_type} if search_type is not None else None
    try:
        return session.post_page_with_retry("", page, config,
                                            filt_overrides=overrides,
                                            param_overrides=params)
    except EmptySearch:
        return False, 0, []


def _sort_keys(docs, sort):
    """Extract sortable strings from page-1 docs for the given sort family."""
    if sort.startswith("Bid-End-Date"):
        return [str(d.get("final_end_date_sort", "") or "") for d in docs]
    if sort.startswith("Bid-Start-Date"):
        return [str(d.get("final_start_date_sort", "") or "") for d in docs]
    if sort.startswith("Bid-Number"):
        numbers = []
        for d in docs:
            bid_no = str(d.get("b_bid_number", "") or "")
            match = _BID_NO_RE.search(bid_no)
            numbers.append(int(match.group(1)) if match else 0)
        return numbers
    return [str(d.get("final_end_date_sort", "") or "") for d in docs]


def _median(values):
    values = sorted(v for v in values if v)
    if not values:
        return None
    return values[len(values) // 2]


def run_probe(config, aggressive=False):
    lock = ScrapeLock()
    if not lock.acquire():
        log("probe: scrape lock held by another process — aborting (retry later)")
        return 1
    report = {"ran_at": datetime.now(IST).isoformat(), "probes": {}, "gates": {}}
    try:
        session = GemSession(config["user_agent"])

        # P1 — handshake
        try:
            session.refresh(timeout=config["timeout_seconds"])
            report["probes"]["P1_handshake"] = {"ok": True, "token_found": True}
            log("P1 handshake OK (token found)")
        except (RuntimeError, OSError) as err:
            report["probes"]["P1_handshake"] = {"ok": False, "error": str(err)}
            log("P1 handshake FAILED: %s" % err)
            _print_report(report)
            return 1

        # P2 — empty keyword, standard filter
        ok, num, docs = _post(session, config, 1)
        report["probes"]["P2_empty_keyword"] = {
            "ok": ok, "num_found": num, "docs_on_page1": len(docs),
            "first_docs": [
                {"b_id": d.get("b_id"), "b_bid_number": d.get("b_bid_number"),
                 "start": d.get("final_start_date_sort"),
                 "end": d.get("final_end_date_sort"),
                 "b_status": d.get("b_status"),
                 "b_is_inactive": d.get("b_is_inactive")}
                for d in (docs or [])[:3]
            ],
        }
        log("P2 empty keyword: ok=%s numFound=%s docs=%d" % (ok, num, len(docs or [])))

        # P3 — sort candidates (>=2 s apart), compare newest-vs-oldest ordering
        sort_results = {}
        for sort in ALL_SORT_CANDIDATES:
            time.sleep(2)
            ok, n, sdocs = _post(session, config, 1, sort=sort)
            sort_results[sort] = {
                "ok": ok, "num_found": n, "docs": len(sdocs or []),
                "keys_page1": _sort_keys(sdocs or [], sort)[:10],
            }
            log("P3 sort %-24s ok=%s numFound=%s docs=%d"
                % (sort, ok, n, len(sdocs or [])))
        report["probes"]["P3_sorts"] = sort_results

        # P4 — byEndDate windows (two common date formats)
        today = datetime.now(IST).date()
        window_results = {}
        for fmt, frm, to in (
            ("ISO", today.isoformat(), (today + timedelta(days=14)).isoformat()),
            ("DD-MM-YYYY", today.strftime("%d-%m-%Y"),
             (today + timedelta(days=14)).strftime("%d-%m-%Y")),
        ):
            time.sleep(2)
            ok, n, sdocs = _post(session, config, 1, window=(frm, to))
            window_results[fmt] = {
                "ok": ok, "num_found": n, "docs": len(sdocs or []),
                "sample_ends": [str(d.get("final_end_date_sort", ""))
                                for d in (sdocs or [])[:3]],
            }
            log("P4 window %-10s from=%s to=%s ok=%s numFound=%s"
                % (fmt, frm, to, ok, n))
        report["probes"]["P4_windows"] = window_results

        # P5 — deep paging (riskiest probe; run once)
        deep = {}
        total_pages = (num // 10 + 5) if num else 0
        for page in sorted({1000, 2000, total_pages}):
            if page <= 0:
                continue
            time.sleep(2)
            ok, n, sdocs = _post(session, config, page)
            deep[page] = {"ok": ok, "num_found": n, "docs": len(sdocs or [])}
            log("P5 deep page %d: ok=%s docs=%d" % (page, ok, len(sdocs or [])))
        report["probes"]["P5_deep_paging"] = deep

        # P6 — detail pages (3 ids from P2 docs)
        detail_results = []
        for d in (docs or [])[:3]:
            bid_id = d.get("b_id") or d.get("id")
            if not bid_id:
                continue
            url = detail.DETAIL_URL + str(bid_id)
            try:
                status, html = detail._http_get(url, config)
                parsed = detail.parse_detail_html(html)
                detail_results.append({
                    "bid_id": bid_id, "http_status": status, "bytes": len(html),
                    "tables": len(parsed["key_values"]),
                    "location_found": bool(parsed["location"]),
                    "corrigenda": len(parsed["corrigenda"]),
                    "critical_dates": len(parsed["critical_dates"]),
                })
                os.makedirs(OUTPUT_DIR, exist_ok=True)
                with open(os.path.join(OUTPUT_DIR, "probe_detail_%s.html" % bid_id),
                          "w", encoding="utf-8") as fh:
                    fh.write(html)
                log("P6 detail %s: status=%d bytes=%d corrigenda=%d location=%s"
                    % (bid_id, status, len(html), len(parsed["corrigenda"]),
                       bool(parsed["location"])))
            except (RuntimeError, OSError) as err:
                detail_results.append({"bid_id": bid_id, "error": str(err)})
                log("P6 detail %s FAILED: %s" % (bid_id, err))
            time.sleep(config["monitor_detail_delay_seconds"])
        report["probes"]["P6_details"] = detail_results

        # P7 — numFound drift (~30 s apart) + latency
        latencies = []
        t0 = time.monotonic()
        ok, n1, _ = _post(session, config, 1)
        latencies.append(time.monotonic() - t0)
        time.sleep(30)
        t0 = time.monotonic()
        ok2, n2, _ = _post(session, config, 1)
        latencies.append(time.monotonic() - t0)
        report["probes"]["P7_drift"] = {
            "num_found_1": n1, "num_found_2": n2, "delta": n2 - n1 if n1 and n2 else None,
            "latency_s": [round(x, 2) for x in latencies],
        }
        log("P7 drift: %s -> %s (latency %ss)" % (n1, n2, latencies))

        # P8 — aggressive burst (opt-in)
        if aggressive:
            burst = []
            for i in range(5):
                t0 = time.monotonic()
                ok, n, sdocs = _post(session, config, 1)
                burst.append({"ok": ok, "num_found": n,
                              "latency_s": round(time.monotonic() - t0, 2)})
            report["probes"]["P8_aggressive"] = burst
            log("P8 aggressive burst done: %s" % burst)

        # ── gates ──
        gates = _evaluate_gates(report)
        report["gates"] = gates
        save_state({"gates": gates, "delta_sort": gates.get("delta_sort")})
        _print_report(report)
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        path = os.path.join(
            OUTPUT_DIR, "probe_report_%s.json" % datetime.now(IST).strftime("%Y%m%d"))
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)
        log("probe report saved to %s" % path)
        return 0 if gates.get("A") else 2
    finally:
        lock.release()


def _evaluate_gates(report):
    gates = {}
    p2 = report["probes"].get("P2_empty_keyword", {})
    gates["A_full_board"] = bool(p2.get("ok") and p2.get("num_found", 0) >= 10000
                                 and p2.get("docs_on_page1", 0) > 0)

    sorts = report["probes"].get("P3_sorts", {})
    baseline = sorts.get("Bid-End-Date-Oldest", {}).get("num_found")
    stable = {}
    for sort, res in sorts.items():
        if res.get("ok") and baseline and abs(res.get("num_found", 0) - baseline) <= baseline * 0.05:
            stable[sort] = res
    delta_sort = None
    for candidate in DELTA_SORT_CANDIDATES:
        res = stable.get(candidate)
        oldest = sorts.get(candidate.replace("-Newest", "-Oldest"), {})
        if not res or not oldest:
            continue
        new_keys = [k for k in res.get("keys_page1", []) if k]
        old_keys = [k for k in oldest.get("keys_page1", []) if k]
        if not new_keys or not old_keys:
            continue
        if isinstance(new_keys[0], int):  # bid-number family
            if max(new_keys) > max(old_keys):
                delta_sort = candidate
                break
        else:
            if max(new_keys) > max(old_keys):  # ISO strings sort lexicographically
                delta_sort = candidate
                break
    gates["B_delta_sort"] = delta_sort
    gates["delta_sort"] = delta_sort

    windows = report["probes"].get("P4_windows", {})
    gates["C_windows"] = any(
        w.get("ok") and baseline and w.get("num_found", 0) and w["num_found"] < baseline * 0.9
        for w in windows.values()
    )

    deep = report["probes"].get("P5_deep_paging", {})
    gates["D_deep_paging"] = any(
        v.get("ok") and v.get("docs", 0) > 0 for k, v in deep.items() if k > 100
    )

    details = report["probes"].get("P6_details", [])
    gates["E_detail_parsing"] = any(
        d.get("http_status") == 200 and d.get("tables", 0) > 0 for d in details
    )
    return gates


def _print_report(report):
    print()
    print("=" * 62)
    print(" PROBE GATE REPORT")
    print("=" * 62)
    for name, result in report["gates"].items():
        if name == "delta_sort":
            print("  delta sort selected : %s" % (result or "(none -> band fallback)"))
        else:
            print("  %-20s: %s" % (name, "PASS" if result else "FAIL"))
    print("=" * 62)


if __name__ == "__main__":  # pragma: no cover
    cfg = load_config()
    run_probe(cfg)
