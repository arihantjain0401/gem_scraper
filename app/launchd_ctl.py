"""launchd plist writing and load/unload helpers (no Flask dependency).

Single-job architecture for the website: exactly one launchd job exists for
the web app — com.gemscraper.web — which runs the website, the in-app
scheduler, and the ngrok tunnel supervisor. The monitor adds its own four
jobs (com.gemscraper.monitor.*) via write_monitor_plist(); the web job is
untouched by them. The task/ngrok labels below exist only for cleanup of
legacy jobs from the previous multi-job setup.
"""

import os
import plistlib
import socket
import subprocess

from app.config import PROJECT_ROOT

AGENTS_DIR = os.path.expanduser("~/Library/LaunchAgents")
LOG_DIR = os.path.expanduser("~/Library/Logs")
WEB_LABEL = "com.gemscraper.web"
NGROK_LABEL = "com.gemscraper.ngrok"
MONITOR_API_LABEL = "com.gemscraper.monitor.api"
MONITOR_DELTA_LABEL = "com.gemscraper.monitor.delta"
MONITOR_FULL_LABEL = "com.gemscraper.monitor.full"
MONITOR_PULSE_LABEL = "com.gemscraper.monitor.pulse"
ROUTER_LABEL = "com.gemscraper.router"

VENV_PYTHON = os.path.join(PROJECT_ROOT, ".venv", "bin", "python3")
NGROK_BIN = "/opt/homebrew/bin/ngrok"


def _agent_path(label):
    return os.path.join(AGENTS_DIR, label + ".plist")


def _write_plist(path, body):
    with open(path, "wb") as fh:
        plistlib.dump(body, fh)


def write_web_plist():
    path = _agent_path(WEB_LABEL)
    _write_plist(path, {
        "Label": WEB_LABEL,
        "ProgramArguments": [VENV_PYTHON, "-m", "app.web.serve"],
        "WorkingDirectory": PROJECT_ROOT,
        "RunAtLoad": True,
        "KeepAlive": True,
        "ProcessType": "Background",
        "StandardOutPath": os.path.join(LOG_DIR, "gemscraper-web.out.log"),
        "StandardErrorPath": os.path.join(LOG_DIR, "gemscraper-web.err.log"),
    })
    return path


def write_router_plist():
    """The path router job: one public URL -> gem site + /bento (KeepAlive)."""
    path = _agent_path(ROUTER_LABEL)
    _write_plist(path, {
        "Label": ROUTER_LABEL,
        "ProgramArguments": [VENV_PYTHON, "-m", "app.web.router"],
        "WorkingDirectory": PROJECT_ROOT,
        "RunAtLoad": True,
        "KeepAlive": True,
        "ProcessType": "Background",
        "StandardOutPath": os.path.join(LOG_DIR, "gemscraper-router.out.log"),
        "StandardErrorPath": os.path.join(LOG_DIR, "gemscraper-router.err.log"),
    })
    return path


def write_monitor_plist(label, args, calendar_intervals=None, keepalive=False):
    """Write a monitor launchd job: ProgramArguments + optional calendar schedule.

    calendar_intervals: list of {"Hour": h, "Minute": m} StartCalendarInterval
    dicts (iMac wall clock is IST). No KeepAlive on crawl jobs — a crash mid-
    crawl must not restart it; resume/catch-up lives in monitor.db instead.
    """
    log_name = label.replace("com.gemscraper.monitor.", "gemscraper-monitor-")
    body = {
        "Label": label,
        "ProgramArguments": [VENV_PYTHON, "-m", "app.monitor.cli"] + args,
        "WorkingDirectory": PROJECT_ROOT,
        "RunAtLoad": False,
        "ProcessType": "Background",
        "StandardOutPath": os.path.join(LOG_DIR, log_name + ".out.log"),
        "StandardErrorPath": os.path.join(LOG_DIR, log_name + ".err.log"),
    }
    if calendar_intervals:
        body["StartCalendarInterval"] = calendar_intervals
    if keepalive:
        body["RunAtLoad"] = True
        body["KeepAlive"] = True
    path = _agent_path(label)
    _write_plist(path, body)
    return path


def public_url():
    """Ask the local ngrok API for the current public HTTPS URL (None if down)."""
    import json as _json
    import urllib.request as _ur
    try:
        with _ur.urlopen("http://127.0.0.1:4040/api/tunnels", timeout=5) as resp:
            tunnels = _json.load(resp).get("tunnels", [])
        for tunnel in tunnels:
            if tunnel.get("proto") == "https":
                return tunnel.get("public_url")
    except Exception:
        pass
    return None


def remove_plist(label):
    path = _agent_path(label)
    if os.path.exists(path):
        os.remove(path)
    return path


def _run(args):
    return subprocess.run(args, capture_output=True, text=True)


def load(label, plist_path):
    """Idempotent load: bootout (errors ignored), then bootstrap.

    Falls back to `launchctl load -w` when the gui domain is unreachable
    (some SSH contexts), which still registers a per-user LaunchAgent.
    """
    uid = os.getuid()
    _run(["launchctl", "bootout", "gui/%d/%s" % (uid, label)])
    result = _run(["launchctl", "bootstrap", "gui/%d" % uid, plist_path])
    if result.returncode != 0:
        result = _run(["launchctl", "load", "-w", plist_path])
    if result.returncode != 0:
        raise RuntimeError(
            "launchctl failed to load %s: %s" % (label, result.stderr.strip())
        )


def unload(label):
    """Bootout (errors ignored) and delete the plist file."""
    uid = os.getuid()
    _run(["launchctl", "bootout", "gui/%d/%s" % (uid, label)])
    remove_plist(label)


def is_loaded(label):
    result = _run(["launchctl", "list"])
    return label in result.stdout


def is_port_free(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("0.0.0.0", port))
            return True
        except OSError:
            return False
