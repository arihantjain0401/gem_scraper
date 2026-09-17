"""Central paths, URLs, and config.json loading.

Every module resolves project files through PROJECT_ROOT (absolute), because
the modules now live in subfolders. config.json stays at the project root,
unchanged from the original single-file scraper.
"""

import json
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CONFIG_PATH = os.path.join(PROJECT_ROOT, "config.json")
SEEN_DB_PATH = os.path.join(PROJECT_ROOT, "seen_bids.sqlite")
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
APP_DB_PATH = os.path.join(DATA_DIR, "app.db")
MONITOR_DB_PATH = os.path.join(DATA_DIR, "monitor.db")
LOCK_PATH = os.path.join(DATA_DIR, "scrape.lock")
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "output")
CREDENTIALS_DIR = os.path.join(PROJECT_ROOT, "credentials")
GCP_CREDENTIALS_PATH = os.path.join(CREDENTIALS_DIR, "gcp-service-account.json")
GCP_OAUTH_PATH = os.path.join(CREDENTIALS_DIR, "gcp-oauth-client.json")
GOOGLE_TOKEN_PATH = os.path.join(CREDENTIALS_DIR, "google_token.json")
DEEPSEEK_KEY_PATH = os.path.join(CREDENTIALS_DIR, "deepseek.env")
WEB_PASSWORD_PATH = os.path.join(CREDENTIALS_DIR, "web_password.env")
MCP_API_KEY_PATH = os.path.join(CREDENTIALS_DIR, "mcp_api_key.env")
SECRET_KEY_PATH = os.path.join(DATA_DIR, "secret_key")

# GeM BidPlus endpoints (preserved from the original scraper).
BASE = "https://bidplus.gem.gov.in"
LIST_URL = BASE + "/all-bids"
DATA_URL = BASE + "/all-bids-data"
BID_URL = BASE + "/showbidDocument/"
# The bid result view is a JS shell (verified 2026-09-01): static HTML holds
# no bid data, and its AJAX sub-endpoints (getConsignees, getCaDocView,
# showSpecs) need buyer-session params we don't have. showbidDocument/
# returns 200 with an empty body. See docs/FUTURE_TASKS.md — detail
# enrichment needs a headless browser or deeper AJAX reverse-engineering.
DETAIL_URL = BASE + "/bidding/bid/getBidResultView/"

# Target spreadsheet for the "save as new sheet" button.
# "Copy of BD 3.2" owned by defproglobal@gmail.com (the user's preferred
# copy). The original BD 3.2 (1I4aJ3ZrqMt...) has a broken get-by-ID
# reference in Google's backend — see docs/FUTURE_TASKS.md.
GOOGLE_SPREADSHEET_ID = "1K2DkM_uvH7KcRcdK5auKDS8xxS_sFVuM1D8KBw423rg"

# The exact 7 headers the user wants in the web UI and in Google Sheets.
VIEW_HEADERS = [
    "Bid Number", "End Date", "Time", "Organization", "Location", "Item", "Quantity",
]

WEB_HOST = "0.0.0.0"
WEB_PORT = 8050

# Standalone MCP server for the full-board monitor (Bento-style HTTP+JSON).
# Port 8105 follows Bento's 81xx capability numbering (8101-8104 taken).
MCP_HOST = "0.0.0.0"
MCP_PORT = 8105

DEFAULT_CONFIG = {
    "keywords": [],
    "output_dir": "output",
    "request_delay_seconds": 1.5,
    "timeout_seconds": 30,
    "max_retries": 3,
    "max_pages_per_keyword": 200,
    "user_agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    # --- full-board monitor tunables (all merged over config.json) ---
    "monitor_max_pages_delta": 400,
    "monitor_max_pages_full": 6000,
    "monitor_convergence_pages": 5,
    "monitor_min_delta_pages": 10,
    "monitor_resolve_disappeared": True,
    "monitor_resolve_disappeared_cap": 300,
    "monitor_detail_fresh_seconds": 86400,
    "monitor_detail_failed_retry_seconds": 21600,
    "monitor_detail_delay_seconds": 2.0,
    "monitor_pulse_drift_pct": 2.0,
    "monitor_lock_wait_minutes_delta": 10,
    "monitor_lock_wait_minutes_full": 60,
    "monitor_full_max_hours": 4,
    "monitor_closed_margin_hours": 6,
}


def load_config():
    """Load config.json merged over defaults (file must exist; defaults fill gaps)."""
    with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
        cfg = json.load(fh)
    merged = dict(DEFAULT_CONFIG)
    merged.update(cfg)
    return merged


def _read_env_value(path, key):
    """Read KEY=value from a small env file; returns None when missing."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith(key + "="):
                    value = line.split("=", 1)[1].strip().strip('"').strip("'")
                    return value or None
    except OSError:
        pass
    return None


def load_deepseek_key():
    """Read the DeepSeek API key from credentials/deepseek.env (DEEPSEEK_API_KEY=...).

    Returns None when the file is missing/unreadable. The file is chmod 600
    and gitignored; nothing else stores the key.
    """
    return _read_env_value(DEEPSEEK_KEY_PATH, "DEEPSEEK_API_KEY")


def load_web_password():
    """Read the site password from credentials/web_password.env (WEB_PASSWORD=...).

    Returns None when not set — in that case the app stays open (private
    network mode, as before). Set it to enable the login page.
    """
    return _read_env_value(WEB_PASSWORD_PATH, "WEB_PASSWORD")


def load_mcp_api_key():
    """Read the monitor MCP API key from credentials/mcp_api_key.env
    (MCP_API_KEY=...). Returns None when not set — the MCP server then stays
    open (same convention as Bento: key unset means no auth check).
    """
    return _read_env_value(MCP_API_KEY_PATH, "MCP_API_KEY")


def get_secret_key():
    """Persistent Flask session secret so logins survive server restarts."""
    import secrets as _secrets
    try:
        with open(SECRET_KEY_PATH, "r", encoding="utf-8") as fh:
            key = fh.read().strip()
            if key:
                return key
    except OSError:
        pass
    key = _secrets.token_hex(32)
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(SECRET_KEY_PATH, "w", encoding="utf-8") as fh:
        fh.write(key)
    os.chmod(SECRET_KEY_PATH, 0o600)
    return key
