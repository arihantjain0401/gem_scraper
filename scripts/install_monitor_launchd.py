"""Install the four monitor launchd jobs (idempotent).

  com.gemscraper.monitor.api    RunAtLoad+KeepAlive — MCP server :8105
  com.gemscraper.monitor.delta  daily 05:15 IST (iMac wall clock = IST)
  com.gemscraper.monitor.full   weekly Sunday 03:00 IST
  com.gemscraper.monitor.pulse  08:00, 14:00, 20:00 IST

Does NOT touch com.gemscraper.web. Run on the iMac:
  .venv/bin/python scripts/install_monitor_launchd.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import MCP_PORT  # noqa: E402
from app.launchd_ctl import (  # noqa: E402
    MONITOR_API_LABEL,
    MONITOR_DELTA_LABEL,
    MONITOR_FULL_LABEL,
    MONITOR_PULSE_LABEL,
    is_loaded,
    is_port_free,
    load,
    write_monitor_plist,
)

JOBS = [
    (MONITOR_API_LABEL, ["serve"], None, True),
    (MONITOR_DELTA_LABEL, ["delta"], [{"Hour": 5, "Minute": 15}], False),
    (MONITOR_FULL_LABEL, ["full"], [{"Hour": 3, "Minute": 0, "Weekday": 0}], False),
    (MONITOR_PULSE_LABEL, ["pulse"], [
        {"Hour": 8, "Minute": 0},
        {"Hour": 14, "Minute": 0},
        {"Hour": 20, "Minute": 0},
    ], False),
]


def main():
    # Refuse only when something ELSE holds the port; our own running api job
    # (idempotent reinstall) is fine — bootout+bootstrap handles it.
    if not is_port_free(MCP_PORT) and not is_loaded(MONITOR_API_LABEL):
        print("Port %d is in use by something other than the monitor api job. "
              "Refusing to install." % MCP_PORT)
        sys.exit(1)
    for label, args, intervals, keepalive in JOBS:
        path = write_monitor_plist(label, args, calendar_intervals=intervals,
                                   keepalive=keepalive)
        load(label, path)
        print("installed %s" % label)
    print("Done. Check: launchctl list | grep gemscraper")


if __name__ == "__main__":
    main()
