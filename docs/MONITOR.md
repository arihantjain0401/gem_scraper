# GeM Full-Board Monitor — tool contract + ops runbook

> **Current deployment (2026-09-01):** the monitor is parked for further
> work/testing. Installed jobs: `com.gemscraper.monitor.sweep` (nightly
> 03:00 IST full sweep at 0.6s — closes the gap and reconciles) and the
> website `com.gemscraper.web` (untouched). The MCP server is stopped
> (`.venv/bin/python -m app.monitor.cli serve` starts it again; the
> api/delta/full/pulse plists were removed). `config.json`:
> `request_delay_seconds: 0.6`, `monitor_resolve_disappeared: false`
> (detail pages are JS shells until gate E is solved). The rest of this
> doc describes the full design, including the tiered delta agreed but
> not yet built.

The monitor snapshots the entire GeM active board (~42k bids) into
`data/monitor.db`, diffs snapshots into a change stream, and exposes
everything through an MCP HTTP server — Bento's exact wire convention — so
Bento (or any app) can consume it with a routing-table entry and zero
changes on this side. The existing keyword web app (`com.gemscraper.web`,
:8050) is untouched.

## Architecture

```
launchd (IST wall clock)                     MCP HTTP server :8105 (waitress)
  monitor.delta  daily 05:15  ─┐                  app/monitor/mcp_server.py
  monitor.full   Sun   03:00  ─┼──► app/monitor/  app/monitor/mcp_tools.py (7 tools)
  monitor.pulse  08:00/14:00/20:00 ─┘  core: crawl/db/vitals/detail/probe
  monitor.api    RunAtLoad+KeepAlive ───────────► data/monitor.db (SQLite WAL)
CLI: .venv/bin/python -m app.monitor.cli {probe,baseline,delta,full,pulse,detail,status,serve}
```

- **Priority rule:** user keyword scrapes always win the `ScrapeLock` —
  delta waits ≤10 min, full ≤60 min, pulse try-once, then exit 0 `skipped`.
- **Crawl modes:** `baseline` (full snapshot), `delta` (daily
  convergence crawl under `Bid-End-Date-Newest` — probe gate B verified
  2026-09-01), `full` (weekly reconciliation: closed-marking + disappeared
  resolution), `pulse` (one numFound check).
- **Change detection:** no last-modified field exists in the listing — a
  sha256 fingerprint of the vitals detects in-place modifications;
  appearance/disappearance of `b_id`s detects new/closed. Closed-marking
  happens only on complete full/baseline crawls (cutoff = start − 6h).
- **Probe (2026-09-01):** gates A-D PASS, E FAIL (detail pages are JS
  shells — see docs/FUTURE_TASKS.md). Report:
  `output/probe_report_20260901.json`; delta sort persisted in
  `data/monitor_state.json`.

## MCP wire contract (identical to Bento's HttpMCPDispatcher)

- `GET  /tools[?tag=]` → `[{name, description, input_schema, tags}]`
- `POST /call` body `{"tool": "<name>", "params": {...}}` →
  `{"success": true, "result": {"ok": true, "data": {...}}}` (inner
  envelope from `app/monitor/mcp_contract.py`; failures are
  `{"success": true, "result": {"ok": false, "error": "..."}}`)
- unknown tool → HTTP 404; `GET /health` → `{"status", "tools_count"}`
- **Auth:** `X-API-Key` header must match `MCP_API_KEY` from
  `credentials/mcp_api_key.env`; **unset = open** (Bento convention).
  Set the key before exposing the port beyond Tailscale.

### Tools

| Tool | Params | Notes |
|---|---|---|
| `search_bids` | q, status (ongoing/closed/all), organization, department, category, end_after, end_before, limit, offset | SQLite only — zero GeM calls |
| `get_changes` | since (ISO or "24h"/"7d"), kinds, limit | events: new/modified/closed/reappeared; `fields` = {key: [old, new]} |
| `get_bid_detail` | b_id, refresh | cached 24h (fail cache 6h); on-demand GeM fetch; `surface: "js_shell"` until gate E is solved; always includes stored vitals |
| `get_stats` | — | active/total bids, last numFound, 7d churn, last crawls, detail-cache, running flag |
| `trigger_delta` / `trigger_full` | — | background thread, pollable crawl_id; busy → `{"status": "busy"}` |
| `get_crawl_status` | crawl_id? | running flag + current/last crawl rows |

### Future Bento integration (documented only — no work done yet)

In Bento: add `GEM_URL` env (`http://100.115.236.19:8105`), add the 7 tool
names to `backend/execution/tool_routing.py`, set a shared `MCP_API_KEY`.
`HttpMCPDispatcher` needs no other changes.

## Ops runbook (on the iMac)

```bash
cd ~/gem-scraper
.venv/bin/python -m app.monitor.cli status          # health in IST
.venv/bin/python -m app.monitor.cli probe           # re-verify gates (read-only)
.venv/bin/python -m app.monitor.cli baseline        # full snapshot (~1h at 0.6s)
.venv/bin/python -m app.monitor.cli delta --resume  # daily incremental (+resume)
.venv/bin/python -m app.monitor.cli full --resume   # weekly reconciliation
.venv/bin/python -m app.monitor.cli pulse           # one numFound check
.venv/bin/python -m app.monitor.cli detail <b_id> --refresh
.venv/bin/python -m app.monitor.cli serve           # MCP server :8105 (manual)
.venv/bin/python scripts/install_monitor_launchd.py # 4 launchd jobs (idempotent)
tail ~/Library/Logs/gemscraper-monitor-*.out.log    # crawl/api logs
```

- **Resume:** `--resume` continues the latest interrupted crawl of the same
  mode from `crawl_pages` (crash-safe; no KeepAlive on crawl jobs on
  purpose).
- **Catch-up:** delta skips if one already completed today (IST); full
  skips if one completed this ISO week; both are also enforced by the MCP
  triggers.
- **Count drift:** DB-active vs numFound compared after every crawl and
  pulse; >2% → WARNING in logs + `crawls.summary`.
- **Config** (`config.json`, all optional, merged over defaults):
  `monitor_max_pages_delta` 400, `monitor_max_pages_full` 6000,
  `monitor_convergence_pages` 5, `monitor_min_delta_pages` 10,
  `monitor_resolve_disappeared` true (cap 300), `monitor_detail_fresh_seconds`
  86400, `monitor_detail_delay_seconds` 2.0, `monitor_pulse_drift_pct` 2.0,
  `monitor_lock_wait_minutes_delta` 10 / `_full` 60,
  `monitor_full_max_hours` 4, `monitor_closed_margin_hours` 6,
  `monitor_delta_sort` (probe writes it to `data/monitor_state.json`).

## DB schema (data/monitor.db, WAL)

`bids` (one row per bid, raw vitals + fingerprint + first/last_seen +
is_active + detail state) · `crawls` (mode/status/pages/counts/complete) ·
`crawl_pages` (per-page ok, for resume) · `events` (new/modified/closed/
reappeared + field deltas) · `counts` (numFound time series) ·
`bid_details` (on-demand fetch cache).
