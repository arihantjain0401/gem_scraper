"""Google Sheets: write a run's results as a new worksheet tab.

Uses a dedicated service account (credentials/gcp-service-account.json on the
iMac). The AI summary is never included in sheets output.
"""

import json
import os
from datetime import datetime

import gspread

from app import db
from app.config import GCP_CREDENTIALS_PATH, GOOGLE_SPREADSHEET_ID, VIEW_HEADERS
from app.scraper.util import log
from app.web import gsheets_oauth


class SheetsError(Exception):
    """Friendly, user-facing sheets error."""


def _client():
    """Prefer the connected Google account; fall back to the service account."""
    oauth_creds = gsheets_oauth.credentials()
    if oauth_creds:
        return gspread.authorize(oauth_creds)
    if os.path.exists(GCP_CREDENTIALS_PATH):
        return gspread.service_account(filename=GCP_CREDENTIALS_PATH)
    raise SheetsError(
        "No Google connection. Click 'Connect Google' in the nav first — the "
        "app will then save sheets under your own Google account."
    )


def _client_email():
    if gsheets_oauth.has_token():
        return gsheets_oauth.token_email() or "your Google account"
    if not os.path.exists(GCP_CREDENTIALS_PATH):
        return "the service account email"
    try:
        with open(GCP_CREDENTIALS_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh).get("client_email", "the service account email")
    except (OSError, ValueError):
        return "the service account email"


def _unique_title(sh, title):
    existing = {w.title for w in sh.worksheets()}
    if title not in existing:
        return title
    n = 2
    while "%s-%d" % (title, n) in existing:
        n += 1
    return "%s-%d" % (title, n)


def auto_save_run(run_id, mode):
    """Auto-save a finished run to sheets — scheduled runs only.

    Ad-hoc runs stay manual (the Save button). Failures are logged, never
    raised: the run remains completed and the user can still save manually.
    """
    if mode != "scheduled":
        return
    try:
        tab_name = save_run_as_sheet(run_id)
        log("auto-saved run %d as sheet tab '%s'" % (run_id, tab_name))
    except SheetsError as err:
        log("auto-save skipped for run %d: %s" % (run_id, err))
    except Exception as err:  # last line of defense — log, don't break the run
        log("auto-save failed for run %d: %s" % (run_id, err))


def save_run_as_sheet(run_id):
    """Write one run's results as a new tab; returns the tab name."""
    rows = db.get_results(run_id, limit=100000)
    if not rows:
        raise SheetsError("This run has no results to save.")

    data = [[
        r["bid_number"] or "",
        r["end_date_ist"] or "",
        r["end_time_ist"] or "",
        r["organization"] or "",
        r["location"] or "",
        r["item"] or "",
        r["quantity"] or "",
    ] for r in rows]

    try:
        sh = _client().open_by_key(GOOGLE_SPREADSHEET_ID)
    except SheetsError:
        raise
    except gspread.exceptions.SpreadsheetNotFound:
        raise SheetsError("Spreadsheet not found for the configured ID.")
    except gspread.exceptions.APIError as err:
        raise SheetsError(_map_api_error(err, _client_email()))
    except Exception as err:  # network trouble etc. — keep it friendly
        raise SheetsError("Could not reach Google Sheets (%s)." % type(err).__name__)

    title = _unique_title(sh, "bids-" + datetime.now().strftime("%Y%m%d-%H%M"))
    try:
        ws = sh.add_worksheet(title=title, rows=max(len(data) + 1, 100), cols=7)
        ws.update([VIEW_HEADERS] + data, value_input_option="RAW")
    except gspread.exceptions.APIError as err:
        raise SheetsError(_map_api_error(err, _client_email()))
    return title


def _map_api_error(err, email):
    status = getattr(getattr(err, "response", None), "status_code", None)
    if status == 403:
        return ("Google returned 403: share the spreadsheet (Editor) with %s "
                "and try again." % email)
    if status == 429:
        return "Google API quota exceeded — wait a minute and retry."
    return "Google Sheets error (status %s)." % status
