# app/web/

The web UI (Flask + waitress, port 8050, served by launchd).

- `serve.py` — launchd entry (`python -m app.web.serve`)
- `webapp.py` — all routes: scrape, run detail, history, scheduler, API
- `background.py` — daemon-thread scrape runs + live progress registry
- `sheets.py` — Save-to-Sheet: new tab in the fixed spreadsheet via service
  account (`credentials/gcp-service-account.json`)
- `llm.py` — optional DeepSeek summary; the API key comes from
  `credentials/deepseek.env` (chmod 600, gitignored)

Templates live in `app/templates/`, styles in `app/static/style.css`. The
scheduler page deliberately has no create option — only run-now, edit
keywords, and delete.
