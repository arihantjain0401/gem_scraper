"""Cross-process scrape lock (fcntl.flock on data/scrape.lock).

The web server, the launchd scheduler task, and the CLI run in different
processes, so a simple in-memory flag is not enough. flock is released
automatically by the OS when the holding process dies — no stale locks.
"""

import fcntl
import os

from app.config import DATA_DIR, LOCK_PATH


class ScrapeLock:
    def __init__(self):
        self._fh = None

    def acquire(self):
        """Try to take the lock. Returns True on success, False if held elsewhere."""
        os.makedirs(DATA_DIR, exist_ok=True)
        self._fh = open(LOCK_PATH, "w")
        try:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._fh.write(str(os.getpid()))
            self._fh.flush()
            return True
        except (BlockingIOError, OSError):
            self._fh.close()
            self._fh = None
            return False

    def release(self):
        if self._fh is None:
            return
        try:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            self._fh.close()
        except OSError:
            pass
        self._fh = None

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *exc):
        self.release()
        return False
