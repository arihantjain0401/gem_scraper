# Goals → where implemented

Every user-facing goal and the code that makes it happen.

| Goal | Where | Notes |
|---|---|---|
| Give keywords, extract bids | `app/scraper/` + `app/web/webapp.py` `POST /api/scrape` | textarea on `/`; one keyword per line; validated with the original regex (`app/scraper/util.py`) |
| Live progress while extracting | `app/web/background.py` (PROGRESS) + `app/web/webapp.py` `GET /api/run/<id>/status` | polled every 2 s by `app/templates/scrape.html` |
| Results table with 7 headers | `app/scraper/mapping.py` `build_view_row` + `app/templates/run_detail.html` | Bid Number, End Date, Time, Organization, Location, Item, Quantity; dates in IST |
| Save results as a new sheet in Google Sheets | `app/web/sheets.py` `save_run_as_sheet` + `POST /runs/<id>/save-sheet` | new tab `bids-YYYYMMDD-HHMM` in the fixed spreadsheet; OAuth-connected Google account |
| Scheduled runs populate the sheet automatically | `app/web/sheets.py` `auto_save_run` called from `app/scheduler/inapp.py` and `app/web/background.py` | only `mode='scheduled'` runs (daily fire + Run now); ad-hoc stays manual; failures logged, never break the run |
| AI summary after extraction, web UI only | `app/web/llm.py` + `POST /runs/<id>/summarize` | one-click; DeepSeek key lives in `credentials/deepseek.env` (chmod 600, gitignored); summary stored in `runs.summary`, never sent to sheets |
| Scheduled tasks for keywords | `app/scheduler/inapp.py` (in-app scheduler) + `tasks` table in `app/db.py` | fired inside the web process every 30 s; catch-up on recovery |
| Review scheduled tasks | `GET /scheduler` + `app/templates/scheduler.html` | shows schedule, keywords, last run status |
| Add/remove keywords on a task | `POST /tasks/<id>/edit` + `app/db.py` `update_task_keywords` | the ONLY editable field; plist untouched |
| Run a scheduled task now | `POST /tasks/<id>/run-now` | background run, `mode=scheduled` |
| Delete a scheduled task | `POST /tasks/<id>/delete` | row disabled (`enabled=0`), history kept |
| Create scheduled tasks (not in the UI) | `scripts/manage_task.py create` | CLI only, on purpose |
| Website + scheduler + tunnel run on the iMac | ONE launchd job `com.gemscraper.web` | installed by `scripts/install_launchd.py`; logs in `~/Library/Logs/gemscraper-*` |
| LLM key handling | `app/config.py` `load_deepseek_key()` | key read from `credentials/deepseek.env` only; never embedded in code or logs |
| Username + password login | `app/web/webapp.py` `login_page` + `_require_login` hook | `users` table (werkzeug password hashes); admin `arihant` seeded from the old password file; no users = open mode |
| Role-based access | `ADMIN_ENDPOINTS` in `app/web/webapp.py` + role-aware templates | admin: everything; limited: extract + view only (no AI/scheduler/history/access-log/save-sheet) |
| Manage users | `scripts/manage_users.py` | add / list / password / delete (CLI only; admin `arihant` is protected from deletion) |
| Identify who logged in | `logins` table + `GET /access-log` | username, real client IP via ngrok `X-Client-Ip`, device label, user agent; failed attempts flagged |
| Public internet access | `com.gemscraper.ngrok` (ngrok v3 endpoint `gemsite`) | public ngrok-free URL → localhost:8050; URL changes on tunnel restart (free tier) |
| Scheduler UI limited to edit/delete (no create) | `app/web/webapp.py` — no create route exists | create lives in `scripts/manage_task.py` |
| Run history | `runs` table + `GET /history` + `app/templates/history.html` | ad-hoc and scheduled together, filterable |
| Original CLI + CSV behavior preserved | `app/scraper/cli.py`, `app/scraper/pipeline.py`, `output/` | 17-column CSVs, dedupe, politeness settings unchanged |
