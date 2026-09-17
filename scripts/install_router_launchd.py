"""Install the path router launchd job (idempotent).

  com.gemscraper.router  RunAtLoad+KeepAlive — :8060, root -> gem site,
  /bento -> Bento Response server. Run on the iMac:
    .venv/bin/python scripts/install_router_launchd.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.launchd_ctl import ROUTER_LABEL, load, write_router_plist  # noqa: E402


def main():
    path = write_router_plist()
    load(ROUTER_LABEL, path)
    print("installed %s" % ROUTER_LABEL)


if __name__ == "__main__":
    main()
