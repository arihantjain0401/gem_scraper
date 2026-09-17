"""Background scrape runs inside the web process + live progress registry.

A scrape takes minutes (pages x 1.5 s politeness delay), so the HTTP request
returns a run_id immediately and a daemon thread does the work. Progress is
served from the in-memory PROGRESS dict; the DB is the source of truth once
the run finishes. The cross-process flock in app.scraper.locking also guards
against a launchd/CLI run colliding with a web run.
"""

import threading

from app import db
from app.scraper.locking import ScrapeLock
from app.scraper.pipeline import run_scrape
from app.scraper.util import log
from app.web import sheets

PROGRESS = {}
_BUSY = False
_LOCK = threading.Lock()


def start(keywords, task_id=None, mode="adhoc"):
    """Start a background scrape. Returns run_id, or None if one is already running."""
    global _BUSY
    with _LOCK:
        if _BUSY:
            return None
        _BUSY = True
        run_id = db.create_run(task_id=task_id, mode=mode, keywords=keywords)
        threading.Thread(target=_worker, args=(run_id, keywords, mode),
                         daemon=True).start()
        return run_id


def get_progress(run_id):
    return PROGRESS.get(run_id)


def _worker(run_id, keywords, mode="adhoc"):
    global _BUSY
    lock = ScrapeLock()
    if not lock.acquire():
        # A launchd or CLI run in another process holds the lock.
        db.finish_run(run_id, status="skipped", error="another scrape is running")
        with _LOCK:
            _BUSY = False
        return
    try:
        def progress_cb(keyword, page, pages, docs, num_found):
            PROGRESS[run_id] = {
                "keyword": keyword,
                "page": page,
                "pages": pages,
                "docs": docs,
                "num_found": num_found,
            }

        result = run_scrape(keywords, progress_cb=progress_cb, write_csv=False)
        rows = result["rows"]
        db.insert_results(run_id, rows)
        db.finish_run(
            run_id, status="completed",
            total_count=len(rows), new_count=result["new_count"],
        )
        log("web run %d completed: %d bids, %d new"
            % (run_id, len(rows), result["new_count"]))
        sheets.auto_save_run(run_id, mode)
    except Exception as err:  # last line of defense — log and record
        db.finish_run(run_id, status="failed", error=str(err))
        log("web run %d FAILED: %s" % (run_id, err))
    finally:
        PROGRESS.pop(run_id, None)
        lock.release()
        with _LOCK:
            _BUSY = False
