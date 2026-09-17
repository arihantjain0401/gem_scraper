# app/

Application code. Three sub-packages:

- `scraper/` — the GeM BidPlus scraping engine (stdlib only, runnable as CLI)
- `scheduler/` — launchd task entry point (`python -m app.scheduler.run_task <id>`)
- `web/` — Flask UI, background runs, Google Sheets, DeepSeek summary

Shared: `config.py` (paths/URLs/config loading), `db.py` (SQLite for tasks/runs/
results), `launchd_ctl.py` (plist + launchctl helpers). Goal mapping in
`../docs/GOALS.md`.
