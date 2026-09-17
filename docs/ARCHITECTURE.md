# Architecture

## Machines and paths

- The project lives on the **iMac** at `/Users/amitkumarjain/gem-scraper`.
- It is also mounted on the MacBook Air at `/Volumes/amitkumarjain/gem-scraper`
  (SMB, same bytes). **Edit through the mount, execute only on the iMac via SSH**
  (`ssh amitkumarjain@100.115.236.19`). The venv's shebangs and paths are
  iMac-native — never run `.venv/bin/python` through the mount.
- The web UI is browsed at `http://100.115.236.19:8050` (Tailscale). Port 8050
  because macOS AirPlay occupies 5000. No auth — only reachable on the private
  Tailscale network (see FUTURE_TASKS).

## Components

```
browser ──HTTP──> waitress (app/web/serve.py, 0.0.0.0:8050)
                    └─ Flask (app/web/webapp.py)
                         ├─ POST /api/scrape ──> background.py (daemon thread)
                         │      └─ pipeline.run_scrape ──> scraper/ (GeM handshake)
                         ├─ POST /runs/<id>/save-sheet ──> sheets.py ──> Google Sheets API
                         └─ POST /runs/<id>/summarize ──> llm.py ──> api.deepseek.com

web process also runs (same launchd job):
  ├─ in-app scheduler (app/scheduler/inapp.py) — fires due tasks every 30 s
  │      └─ pipeline.run_scrape (same code path as ad-hoc runs)
  └─ ngrok supervisor (app/web/tunnel.py) — child process `ngrok start gemsite`
         └─ public internet <──> ngrok cloud <──> localhost:8050
```

- **app/scraper/** — engine, stdlib only: `session.py` (CSRF handshake + paged
  POSTs), `fetch.py` (pagination + progress callback), `mapping.py` (17-col row
  + 7-col view + IST split), `dedup.py` (`seen_bids.sqlite`), `csv_writer.py`,
  `locking.py` (flock), `pipeline.py` (shared orchestration), `cli.py`.
- **app/web/** — `serve.py` (launchd entry), `webapp.py` (routes), `background.py`
  (run thread + progress), `sheets.py`, `llm.py`.
- **app/db.py** — `data/app.db` (WAL): `tasks`, `runs`, `results`. Separate from
  `seen_bids.sqlite`, which is the dedupe store shared by every run type.
- **app/launchd_ctl.py** — writes plists, bootstrap/bootout helpers.

## Concurrency

Only one scrape at a time, enforced two ways:
1. `fcntl.flock` on `data/scrape.lock` — cross-process (web thread vs. launchd
   task vs. CLI). Dies with the process, so no stale locks.
2. In-process busy flag in `app/web/background.py` — the scrape page gets a 409
   instead of a queued run; the launchd task records a `skipped` run and exits 1.

## launchd (user domain, `~/Library/LaunchAgents/`) — ONE job

| Label | Runs | Schedule | Logs |
|---|---|---|---|
| `com.gemscraper.web` | `app/web/serve.py` → website + in-app scheduler + ngrok supervisor | RunAtLoad + KeepAlive | `~/Library/Logs/gemscraper-web.{out,err}.log` (ngrok appends to `gemscraper-ngrok.out.log`) |

The in-app scheduler fires tasks from the `tasks` table (daily HH:MM, IST,
catch-up on recovery). Edit keywords = `UPDATE tasks` only. Delete = row
disabled (`enabled=0`, history kept). The old per-task and standalone-ngrok
launchd jobs were removed on consolidation (2026-08-31).

## Data flow of one run

1. Run row created (`status=running`, keywords snapshot).
2. `pipeline.run_scrape`: session refresh → per-keyword pagination (1.5 s
   delay, 3 retries, 200-page cap) → cross-keyword dedupe → `is_new` from
   `seen_bids.sqlite` → rows sorted by end date.
3. `insert_results`: the 7-column view is stored in `results` with IST
   date/time precomputed. Web runs skip CSVs; scheduled + CLI runs also write
   `output/bids_<date>.csv` and `output/new_bids_<date>.csv` (17 columns).
4. `mark_seen` commits the new bid ids.
5. Run row finished (`completed` / `failed` / `skipped`). On server start, any
   stale `running` row is marked failed ("interrupted by web server restart").
6. Scheduled runs (`mode='scheduled'`) call `sheets.auto_save_run` on
   completion — a new `bids-*` tab appears in the target spreadsheet without
   any user action. Ad-hoc runs require the manual Save button.

## Auth and the access log

- Login is username + password against the `users` table (werkzeug password
  hashes, role `admin` or `limited`). The old single-password file
  (`credentials/web_password.env`) was migrated: it seeds the `arihant` admin
  on first init when the users table is empty. No users at all = open mode.
- Role enforcement: `ADMIN_ENDPOINTS` in `app/web/webapp.py` bounces non-admin
  requests (history, scheduler, access-log, summarize, save-sheet, task
  actions, connect-google) back to the home page; templates hide the same
  features. Limited users keep `/`, `/api/scrape`, `/api/run/<id>/status`, and
  `/runs/<id>`.
- Sessions are Flask cookies signed with a persistent secret in
  `data/secret_key` (survives restarts), 7-day TTL.
- Every login attempt — success or failure — is recorded in the `logins` table
  (`/access-log` page): time, username, result, IP, device label, user agent.
  Attempted passwords are never stored.
- Client IP: waitress strips the standard `X-Forwarded-*` headers, so the ngrok
  endpoint injects `X-Client-Ip: ${conn.client_ip}` via a traffic policy
  (ngrok.yml), and `_client_ip()` reads that first.

## ngrok

`com.gemscraper.ngrok` runs `ngrok start gemsite` (KeepAlive). The endpoint is
defined in `~/Library/Application Support/ngrok/ngrok.yml` (v3, upstream 8050,
traffic policy adds `X-Client-Ip`). The current public URL can be read from
`http://127.0.0.1:4040/api/tunnels` or `app/launchd_ctl.py public_url()`.
Free plan: random subdomain (can change when the tunnel restarts) and a
browser click-through warning page.

## Secrets

- **DeepSeek API key** — `credentials/deepseek.env` (chmod 600, gitignored),
  read by `app/config.py` `load_deepseek_key()` at summarize time. Never
  embedded in code, never logged. Summary text is stored in `runs.summary`
  (UI only). (Originally the UI asked for a fresh key per use; changed to a
  stored key at the user's request on 2026-08-31.)
- **Google service account** — `credentials/gcp-service-account.json`
  (chmod 600, dedicated SA, never committed). The spreadsheet is shared with
  the SA email directly; the SA has no project roles.

## Failure handling

- GeM CSRF/session expiry mid-run → refresh + retry (3x, backoff); failed pages
  are skipped and surfaced as warnings.
- Sheets 403 → "share with <sa email>", 429 → quota retry, missing file →
  setup hint. LLM 401 → "invalid key", 402/429 → billing/quota. Never a Python
  traceback in the UI, never the key echoed.
- Malformed dates degrade to empty cells; empty runs show "No bids matched"
  and the Save button stays hidden.
