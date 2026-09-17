"""Google OAuth login flow — the app writes to Sheets as the user's account.

Alternative to the service-account path: the user clicks "Connect Google"
once, approves, and a refresh token is stored in credentials/google_token.json
(chmod 600). The app then writes to the spreadsheet under the user's own
identity, so no sheet sharing is required at all.

Requires an OAuth client config at credentials/gcp-oauth-client.json
(Web application client ID from the GCP console; see docs/ARCHITECTURE.md).
"""

import os

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

from app.config import GCP_OAUTH_PATH, GOOGLE_TOKEN_PATH

# openid is included explicitly: Google appends it to the granted scope set
# anyway, and google-auth-oauthlib rejects a token whose scope set differs
# from the requested one. drive.metadata.readonly lets us inspect file state
# (trash flag, owners, permissions) for diagnostics — read-only, no write
# power beyond what spreadsheets already grants.
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.metadata.readonly",
    "https://www.googleapis.com/auth/userinfo.email",
    "openid",
]


def has_client_config():
    return os.path.exists(GCP_OAUTH_PATH)


def has_token():
    return os.path.exists(GOOGLE_TOKEN_PATH)


def build_flow(redirect_uri):
    flow = Flow.from_client_secrets_file(
        GCP_OAUTH_PATH, scopes=SCOPES, redirect_uri=redirect_uri
    )
    return flow


def save_token(creds):
    os.makedirs(os.path.dirname(GOOGLE_TOKEN_PATH), exist_ok=True)
    with open(GOOGLE_TOKEN_PATH, "w", encoding="utf-8") as fh:
        fh.write(creds.to_json())
    os.chmod(GOOGLE_TOKEN_PATH, 0o600)


def credentials():
    """Valid OAuth credentials, or None when not connected. Auto-refreshes."""
    if not has_token():
        return None
    creds = Credentials.from_authorized_user_file(GOOGLE_TOKEN_PATH, SCOPES)
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            save_token(creds)
        except Exception:
            return None
    return creds


def fetch_account_email():
    """Ask Google's userinfo endpoint for the connected account's email."""
    creds = credentials()
    if creds is None:
        return None
    try:
        import json
        import urllib.request
        req = urllib.request.Request(
            "https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization": "Bearer " + creds.token},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.load(resp).get("email")
    except Exception:
        return None


def token_email():
    """Email of the connected account, or None.

    Tries the 'account' key in the token file first, then asks Google's
    userinfo endpoint once and caches the result back into the token file.
    """
    if not has_token():
        return None
    try:
        import json
        with open(GOOGLE_TOKEN_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if data.get("account"):
            return data["account"]
        email = fetch_account_email()
        if email:
            data["account"] = email
            with open(GOOGLE_TOKEN_PATH, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            os.chmod(GOOGLE_TOKEN_PATH, 0o600)
        return email
    except Exception:
        return None
