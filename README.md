# GeM Scraper

## Claude Code session archive

Redacted summaries of local Claude Code terminal and VS Code sessions are
stored under `session-summaries/YYYY-MM-DD/`. The raw transcripts stay on the
Mac and are not committed. `scripts/export_claude_sessions.py` creates the
summaries, while `scripts/sync_claude_sessions.sh` commits and pushes them to
this repository. The local macOS LaunchAgent runs the sync daily at 8:00 PM
in the Mac's current timezone. This repository is public, so review summaries
before sharing sensitive project details in Claude Code.


Web app that scrapes ongoing tender bids from GeM BidPlus (`bidplus.gem.gov.in`)
by keyword, shows the results in a table, saves them as a new tab in a Google
Spreadsheet, and optionally produces an AI summary. Daily keyword runs are
scheduled with launchd.

Everything runs on the **iMac** (`amitkumarjain`). The website is reachable
privately on the Tailscale network at **http://100.115.236.19:8050**, and
publicly through the **ngrok tunnel** (public URL changes when the tunnel
restarts — check it with the ngrok dashboard or the app logs).

The site has username + password login with two roles:

- **admin** (`arihant` / Arihant Jain) — everything: AI summary, scheduler,
  history, access log, save-to-sheet, Google connection
- **limited** — extract and view results only

Users are managed with `scripts/manage_users.py` (add / list / password /
delete). Every login attempt (success or failure, with username) is logged
with client IP and device details — see the Access log page.

## Quick links

| Page | Purpose |
|---|---|
| `/login` | Username + password gate |
| `/` | Enter keywords and extract bids (all roles) |
| `/runs/<id>` | Results table (7 columns); Save to Google Sheet + Summarize with AI are admin-only |
| `/history` | All past runs (admin only) |
| `/scheduler` | Scheduled tasks — run now, edit keywords, delete (admin only) |
| `/access-log` | Who logged in: time, username, success/failed, IP, device (admin only) |

## Result columns

Bid Number · End Date · Time · Organization · Location · Item · Quantity
(End Date / Time are IST; Location has no source in the GeM API yet — see
`docs/FUTURE_TASKS.md`.)

## How it works

- **Scraping** — `app/scraper/` does the GeM BidPlus handshake (CSRF token),
  paginated keyword search, dedupe against `seen_bids.sqlite`, and CSV output.
  Politeness: 1.5 s between pages, 30 s timeout, 3 retries.
- **Web UI** — Flask + waitress (`app/web/`), served by launchd on port 8050.
  Scrapes run in a background thread with live progress; only one scrape runs
  at a time (cross-process file lock in `data/scrape.lock`).
- **Scheduling** — one thread inside the web process (`app/scheduler/inapp.py`)
  checks every 30 s whether a task's HH:MM has arrived (IST) and fires it.
  Missed runs are caught up when the app comes back. Task keywords + schedule
  live in `data/app.db`. **Scheduled runs auto-save their results to Google
  Sheets** when they finish (ad-hoc runs stay manual — the Save button).
- **Single launchd job** — `com.gemscraper.web` is the only launchd job: it
  runs the website, the in-app scheduler, and the ngrok tunnel as a supervised
  child process (`app/web/tunnel.py`).
- **Google Sheets** — gspread with a dedicated service account
  (`credentials/gcp-service-account.json`). The Save button creates a new tab
  named `bids-YYYYMMDD-HHMM` in the configured spreadsheet.
- **AI summary** — optional DeepSeek call (`app/web/llm.py`). The API key is
  stored once in `credentials/deepseek.env` (chmod 600, gitignored); the button
  in the UI works with one click. The summary is shown in the web UI only and
  is never written to sheets.
- **Public access** — an ngrok tunnel (`com.gemscraper.ngrok`) exposes the app
  on a public ngrok-free URL. Login is required; login attempts are audited
  (`logins` table) with the real client IP via ngrok's `X-Client-Ip` header
  injection. Free-tier note: browsers see ngrok's click-through warning page.

## Common commands (run ON the iMac via SSH, from `~/gem-scraper`)

```bash
# one-off scrape
.venv/bin/python -m app.scraper.cli --keyword "Aluminium Plate"

# manage scheduled tasks (the only place tasks are created)
.venv/bin/python scripts/manage_task.py list
.venv/bin/python scripts/manage_task.py create <task_id> <HH:MM> [name] --keyword "..." [--keyword ...]
.venv/bin/python scripts/manage_task.py delete <task_id>

# (re)install the single launchd job (web + scheduler + tunnel) — idempotent
.venv/bin/python scripts/install_launchd.py

# restart everything (web server, scheduler, and tunnel come back together)
launchctl kickstart -k gui/$(id -u)/com.gemscraper.web
```

⚠ Never run the venv Python through the SMB mount — the venv belongs to the
iMac and its paths are iMac-native. Edit files via the mount, execute via SSH.

## Layout

```
app/          application code (scraper / scheduler / web)
scripts/      setup + task management CLIs
data/         app.db (tasks, runs, results) and scrape.lock
output/       daily CSVs (scheduled + CLI runs)
credentials/  Google service account JSON (not committed anywhere)
docs/         goals mapping, architecture, future tasks
config.json   scraper tunables (kept from the original single-file scraper)
seen_bids.sqlite   dedupe state ("is_new" tracking, shared by all runs)
```

See `docs/GOALS.md` for the goal → code mapping and `docs/ARCHITECTURE.md` for
the full component map.
