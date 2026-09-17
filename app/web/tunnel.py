"""ngrok tunnel supervision — the public tunnel runs as a child of the web app.

Single-launchd-job architecture: the web process spawns `ngrok start gemsite`
on startup and respawns it if it dies. A pid file (data/ngrok.pid) lets a
restart kill any leftover tunnel before spawning a new one (ngrok's free plan
allows only one agent session, so two ngrok processes would fight).

Tunnel logs append to ~/Library/Logs/gemscraper-ngrok.out.log.
"""

import atexit
import os
import subprocess
import threading
import time

from app import launchd_ctl
from app.config import DATA_DIR

PID_FILE = os.path.join(DATA_DIR, "ngrok.pid")
RESPAWN_DELAY_SECONDS = 10

_current_url = None
_url_checked_at = 0.0
_url_lock = threading.Lock()


def public_url():
    """Current public HTTPS URL, cached for 5 minutes. None when no tunnel."""
    global _current_url, _url_checked_at
    with _url_lock:
        if _current_url is None or time.time() - _url_checked_at > 300:
            _current_url = launchd_ctl.public_url()
            _url_checked_at = time.time()
        return _current_url


def _kill_stale():
    """Kill a previous tunnel instance (pid file) and any leftover gemsite tunnel."""
    try:
        with open(PID_FILE, "r", encoding="utf-8") as fh:
            old_pid = int(fh.read().strip())
        os.kill(old_pid, 15)
    except (OSError, ValueError):
        pass
    subprocess.run(["pkill", "-f", "ngrok start gemsite"], capture_output=True)
    try:
        os.remove(PID_FILE)
    except OSError:
        pass


def start_tunnel():
    """Spawn the tunnel and supervise it (respawn on exit). Returns immediately."""
    if not os.path.exists(launchd_ctl.NGROK_BIN):
        print("ngrok not installed — no public tunnel", flush=True)
        return
    _kill_stale()
    threading.Thread(target=_supervise, daemon=True, name="ngrok-supervisor").start()


def _supervise():
    log_path = os.path.join(launchd_ctl.LOG_DIR, "gemscraper-ngrok.out.log")
    while True:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as log_fh:
            proc = subprocess.Popen(
                [launchd_ctl.NGROK_BIN, "start", "gemsite", "--log", "stdout"],
                stdout=log_fh, stderr=subprocess.STDOUT,
            )
        with open(PID_FILE, "w", encoding="utf-8") as fh:
            fh.write(str(proc.pid))
        print("ngrok tunnel started (pid %d)" % proc.pid, flush=True)
        code = proc.wait()
        print("ngrok exited (code %s) — restarting in %ds"
              % (code, RESPAWN_DELAY_SECONDS), flush=True)
        time.sleep(RESPAWN_DELAY_SECONDS)


def _shutdown():
    try:
        with open(PID_FILE, "r", encoding="utf-8") as fh:
            pid = int(fh.read().strip())
        os.kill(pid, 15)
    except (OSError, ValueError):
        pass


atexit.register(_shutdown)
