"""Flask app factory and all routes."""

import json
import time
from datetime import datetime
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from flask import (Flask, abort, flash, jsonify, redirect, render_template,
                   request, session, url_for)

from app import db, launchd_ctl
from app.config import VIEW_HEADERS, get_secret_key, load_deepseek_key
from app.scraper.util import log, validate_keyword
from app.web import background, gsheets_oauth, llm, sheets, tunnel

IST = ZoneInfo("Asia/Kolkata")
RESULTS_PER_PAGE = 200

# Endpoints reachable without logging in.
PUBLIC_ENDPOINTS = {"login_page", "static", "api_health", "oauth2callback"}

# Admin-only endpoints. Limited users can extract and view results only.
ADMIN_ENDPOINTS = {
    "history", "scheduler", "access_log",
    "task_run_now", "task_edit", "task_delete",
    "run_summarize", "run_save_sheet",
    "connect_google",
}

# Sessions expire this many seconds after login (7 days).
LOGIN_TTL_SECONDS = 7 * 24 * 3600


def _ist(iso):
    """Jinja filter: ISO UTC -> 'YYYY-MM-DD HH:MM' IST."""
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone(IST)
        return dt.strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return iso


def _client_ip():
    """Real client IP. waitress strips the standard X-Forwarded-* headers, so
    the ngrok tunnel injects the client IP as X-Client-Ip (traffic policy).
    Falls back to X-Forwarded-For, then the socket address."""
    direct = request.headers.get("X-Client-Ip", "")
    if direct:
        return direct.split(",")[0].strip()
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.remote_addr or ""


def _oauth_https_url(url):
    """Rewrite http->https for non-localhost hosts.

    The tunnel front is https, but waitress sees plain http from the ngrok
    agent (and it strips X-Forwarded-Proto). Google's OAuth library requires
    https URLs — for both the redirect URI (redirect_uri_mismatch) and the
    callback response URL (InsecureTransportError). localhost stays http.
    """
    parts = urlsplit(url)
    if parts.scheme == "http" and parts.hostname not in ("localhost", "127.0.0.1"):
        parts = parts._replace(scheme="https")
        return urlunsplit(parts)
    return url


def _oauth_redirect_uri():
    """Redirect URI matching what the browser used (https unless localhost).
    Registered URIs: the public ngrok URL (https) and http://localhost:8050.
    """
    return _oauth_https_url("http://%s/oauth2callback" % request.host)


def _device_label(ua):
    """Small best-effort browser/OS label from a User-Agent string."""
    ua = ua or ""
    browser = "unknown browser"
    for name, marker in (("Edge", "Edg"), ("Chrome", "Chrome"),
                         ("Firefox", "Firefox"), ("Safari", "Safari")):
        if marker in ua:
            browser = name
            break
    os_name = "unknown OS"
    if "iPhone" in ua or "iPad" in ua:
        os_name = "iOS"
    elif "Android" in ua:
        os_name = "Android"
    elif "Macintosh" in ua:
        os_name = "macOS"
    elif "Windows" in ua:
        os_name = "Windows"
    elif "Linux" in ua:
        os_name = "Linux"
    return "%s on %s" % (browser, os_name)


def create_app():
    app = Flask(__name__, template_folder="../templates", static_folder="../static")
    app.secret_key = get_secret_key()
    app.jinja_env.filters["ist"] = _ist
    app.jinja_env.filters["fromjson"] = json.loads
    db.init()

    @app.context_processor
    def _inject_globals():
        return {
            "public_url": tunnel.public_url(),
            "google_email": gsheets_oauth.token_email(),
            "google_connected": gsheets_oauth.has_token(),
            "google_client_ready": gsheets_oauth.has_client_config(),
            "user_name": session.get("display_name") or session.get("username"),
            "user_role": session.get("role"),
        }

    @app.get("/connect-google")
    def connect_google():
        if not gsheets_oauth.has_client_config():
            flash("OAuth client config is missing on the iMac "
                  "(credentials/gcp-oauth-client.json).", "error")
            return redirect(url_for("index"))
        flow = gsheets_oauth.build_flow(_oauth_redirect_uri())
        auth_url, state = flow.authorization_url(
            access_type="offline", prompt="consent",
            include_granted_scopes="true",
        )
        session["oauth_state"] = state
        # PKCE: the verifier is generated inside the flow instance and must
        # survive to the callback, which rebuilds the flow from scratch.
        session["oauth_verifier"] = flow.code_verifier
        return redirect(auth_url)

    @app.get("/oauth2callback")
    def oauth2callback():
        if not gsheets_oauth.has_client_config():
            flash("OAuth client config is missing on the iMac.", "error")
            return redirect(url_for("login_page"))
        flow = gsheets_oauth.build_flow(_oauth_redirect_uri())
        flow.code_verifier = session.pop("oauth_verifier", None)
        try:
            flow.fetch_token(
                authorization_response=_oauth_https_url(request.url),
                state=session.pop("oauth_state", None),
            )
        except Exception as err:
            log("oauth2callback FAILED: %s: %s" % (type(err).__name__, err))
            flash("Google login failed — try Connect Google again.", "error")
            return redirect(url_for("login_page"))
        gsheets_oauth.save_token(flow.credentials)
        log("google connected as %s" % gsheets_oauth.token_email())
        flash("Google connected as %s." % gsheets_oauth.token_email(),
              "success")
        return redirect(url_for("index"))

    @app.before_request
    def _require_login():
        if not db.has_users():
            return  # open mode (no users configured yet)
        if session.get("user_id"):
            if time.time() - session.get("login_at", 0) > LOGIN_TTL_SECONDS:
                session.clear()
                return redirect(url_for("login_page"))
            if request.endpoint in ADMIN_ENDPOINTS and session.get("role") != "admin":
                flash("That feature is for admins only.", "error")
                return redirect(url_for("index"))
            return
        if request.endpoint in PUBLIC_ENDPOINTS or request.endpoint is None:
            return
        if request.path.startswith("/api/"):
            return jsonify({"error": "login required"}), 401
        return redirect(url_for("login_page", next=request.path))

    @app.route("/login", methods=["GET", "POST"])
    def login_page():
        if not db.has_users():
            return redirect(url_for("index"))
        if request.method == "POST":
            username = (request.form.get("username") or "").strip()
            supplied = request.form.get("password") or ""
            ip = _client_ip()
            ua = request.headers.get("User-Agent", "")
            failures = db.count_recent_failures(ip)
            if failures >= 3:
                # Escalating delay to slow down brute-force guessing.
                time.sleep(min(30.0, 2.0 * failures))
            user = db.verify_user(username, supplied) if username else None
            db.record_login(user is not None, ip, ua, _device_label(ua),
                            username or None)
            if user:
                session["user_id"] = user["id"]
                session["username"] = user["username"]
                session["display_name"] = user["display_name"]
                session["role"] = user["role"]
                session["login_at"] = time.time()
                nxt = request.args.get("next") or url_for("index")
                if not nxt.startswith("/"):  # open-redirect guard
                    nxt = url_for("index")
                return redirect(nxt)
            flash("Wrong username or password.", "error")
        return render_template("login.html")

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login_page"))

    @app.get("/access-log")
    def access_log():
        return render_template("access_log.html", logins=db.list_logins(limit=50))

    @app.get("/")
    def index():
        return render_template("scrape.html")

    @app.get("/api/health")
    def api_health():
        return jsonify({"ok": True})

    @app.post("/api/scrape")
    def api_scrape():
        data = request.get_json(silent=True) or {}
        raw = data.get("keywords") or []
        keywords = [k for k in (validate_keyword(k) for k in raw) if k]
        if not keywords:
            return jsonify({"error": "No valid keywords "
                            "(min 3 chars each, no special characters)."}), 400
        run_id = background.start(keywords, task_id=None, mode="adhoc")
        if run_id is None:
            return jsonify({"error": "Another scrape is already running — "
                            "wait for it to finish."}), 409
        return jsonify({"run_id": run_id})

    @app.get("/api/run/<int:run_id>/status")
    def api_run_status(run_id):
        run = db.get_run(run_id)
        if run is None:
            abort(404)
        return jsonify({
            "status": run["status"],
            "progress": background.get_progress(run_id),
            "total_count": run["total_count"],
            "new_count": run["new_count"],
            "error": run["error"],
        })

    @app.get("/runs/<int:run_id>")
    def run_detail(run_id):
        run = db.get_run(run_id)
        if run is None:
            abort(404)
        page = max(request.args.get("page", 1, type=int), 1)
        rows = db.get_results(run_id, limit=RESULTS_PER_PAGE,
                              offset=(page - 1) * RESULTS_PER_PAGE)
        total = db.count_results(run_id)
        pages = max(1, -(-total // RESULTS_PER_PAGE))
        return render_template(
            "run_detail.html", run=run, rows=rows, total=total,
            page=page, pages=pages, headers=VIEW_HEADERS,
            keywords=json.loads(run["keywords"]),
        )

    @app.post("/runs/<int:run_id>/summarize")
    def run_summarize(run_id):
        run = db.get_run(run_id)
        if run is None:
            abort(404)
        api_key = load_deepseek_key()
        if not api_key:
            flash("DeepSeek API key file is missing on the iMac "
                  "(credentials/deepseek.env).", "error")
            return redirect(url_for("run_detail", run_id=run_id))
        rows = db.get_results(run_id, limit=llm.MAX_ROWS)
        if not rows:
            flash("This run has no results to summarize.", "error")
            return redirect(url_for("run_detail", run_id=run_id))
        try:
            summary = llm.summarize(rows, api_key)
        except llm.LLMError as err:
            flash(str(err), "error")
            return redirect(url_for("run_detail", run_id=run_id))
        db.set_summary(run_id, summary)
        flash("Summary generated.", "success")
        return redirect(url_for("run_detail", run_id=run_id))

    @app.post("/runs/<int:run_id>/save-sheet")
    def run_save_sheet(run_id):
        run = db.get_run(run_id)
        if run is None:
            abort(404)
        try:
            tab_name = sheets.save_run_as_sheet(run_id)
        except sheets.SheetsError as err:
            flash(str(err), "error")
            return redirect(url_for("run_detail", run_id=run_id))
        flash("Saved as new sheet tab: %s" % tab_name, "success")
        return redirect(url_for("run_detail", run_id=run_id))

    @app.get("/history")
    def history():
        status = request.args.get("status") or None
        mode = request.args.get("mode") or None
        page = max(request.args.get("page", 1, type=int), 1)
        limit = 50
        runs, total = db.list_runs(status=status, mode=mode, limit=limit,
                                   offset=(page - 1) * limit)
        pages = max(1, -(-total // limit))
        return render_template("history.html", runs=runs, total=total,
                               page=page, pages=pages, status=status, mode=mode)

    @app.get("/scheduler")
    def scheduler():
        return render_template("scheduler.html",
                               tasks=db.get_tasks(include_disabled=True))

    @app.post("/tasks/<task_id>/run-now")
    def task_run_now(task_id):
        task = db.get_task(task_id)
        if task is None:
            abort(404)
        keywords = json.loads(task["keywords"])
        run_id = background.start(keywords, task_id=task_id, mode="scheduled")
        if run_id is None:
            flash("Another scrape is already running — try again when it finishes.",
                  "error")
        else:
            flash("Started run #%d for task '%s'." % (run_id, task_id), "success")
        return redirect(url_for("scheduler"))

    @app.post("/tasks/<task_id>/edit")
    def task_edit(task_id):
        task = db.get_task(task_id)
        if task is None:
            abort(404)
        # The ONLY editable field is keywords — nothing else.
        raw_lines = (request.form.get("keywords") or "").splitlines()
        keywords = [k for k in (validate_keyword(k) for k in raw_lines) if k]
        if not keywords:
            flash("No valid keywords (min 3 chars each, one per line).", "error")
            return redirect(url_for("scheduler"))
        db.update_task_keywords(task_id, keywords)
        flash("Keywords updated for task '%s'." % task_id, "success")
        return redirect(url_for("scheduler"))

    @app.post("/tasks/<task_id>/delete")
    def task_delete(task_id):
        task = db.get_task(task_id)
        if task is None:
            abort(404)
        launchd_ctl.unload(launchd_ctl.task_label(task_id))
        db.disable_task(task_id)
        flash("Task '%s' deleted (scheduling stopped; history kept)." % task_id,
              "success")
        return redirect(url_for("scheduler"))

    return app
