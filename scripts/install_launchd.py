"""One-time, idempotent installer for the single launchd job.

Architecture: ONE launchd job — com.gemscraper.web — runs the website, the
in-app daily scheduler (app/scheduler/inapp.py), and the ngrok tunnel
supervisor (app/web/tunnel.py).

- writes + loads com.gemscraper.web
- seeds task 'daily' (06:30, the 6 initial keywords) in the DB if missing
- removes legacy jobs from the previous multi-job architecture
  (com.gemscraper.task.*, com.gemscraper.ngrok)

Run ON the iMac:  .venv/bin/python scripts/install_launchd.py
"""

import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db, launchd_ctl
from app.config import WEB_PORT

DAILY_KEYWORDS = [
    "Aluminium Plate",
    "Aluminium Rods",
    "Cathodes",
    "Anodes",
    "Silica Gel",
    "Platform Truck",
]


def _unload_legacy():
    """Remove jobs from the previous multi-job architecture (idempotent)."""
    launchd_ctl.unload(launchd_ctl.NGROK_LABEL)
    for path in glob.glob(
        os.path.join(launchd_ctl.AGENTS_DIR, "com.gemscraper.task.*.plist")
    ):
        launchd_ctl.unload(os.path.basename(path)[:-6])  # strip .plist


def main():
    db.init()

    _unload_legacy()

    if not launchd_ctl.is_loaded(launchd_ctl.WEB_LABEL) \
            and not launchd_ctl.is_port_free(WEB_PORT):
        print("port %d is busy — refusing to install the web job "
              "(KeepAlive would crash-loop)" % WEB_PORT)
        sys.exit(1)

    web_path = launchd_ctl.write_web_plist()
    launchd_ctl.load(launchd_ctl.WEB_LABEL, web_path)
    print("loaded %s (website + in-app scheduler + ngrok tunnel)"
          % launchd_ctl.WEB_LABEL)

    if db.get_task("daily") is None:
        db.create_task("daily", "gemscraper-daily", DAILY_KEYWORDS, 6, 30)
        print("created task 'daily' (06:30, %d keywords) — fired by the in-app "
              "scheduler" % len(DAILY_KEYWORDS))
    else:
        print("task 'daily' already exists — left as-is")


if __name__ == "__main__":
    main()
