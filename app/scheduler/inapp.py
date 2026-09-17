"""In-app daily scheduler — one thread inside the web process fires due tasks.

Replaces per-task launchd jobs (single launchd job architecture). The thread
checks every 30 s whether any enabled task's HH:MM has arrived today (IST) and
no scheduled run for that task exists yet today. If the web app was down or
the iMac asleep when the schedule time passed, the first check after it comes
back fires the missed run — same behaviour launchd would give on wake.

The cross-process scrape lock still applies: if an ad-hoc scrape holds the
lock, the task run is recorded as 'skipped' for that day.
"""

import json
import threading
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

from app import db
from app.scraper.locking import ScrapeLock
from app.scraper.pipeline import run_scrape
from app.scraper.util import log
from app.web import sheets

IST = ZoneInfo("Asia/Kolkata")
CHECK_INTERVAL_SECONDS = 30


class InAppScheduler:
    def __init__(self):
        self._stop = threading.Event()

    def start(self):
        threading.Thread(target=self._loop, daemon=True,
                         name="inapp-scheduler").start()
        log("in-app scheduler started (checks every %ds)" % CHECK_INTERVAL_SECONDS)

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.is_set():
            try:
                self._check()
            except Exception as err:  # never let the loop die
                log("scheduler check error: %s" % err)
            self._stop.wait(CHECK_INTERVAL_SECONDS)

    def _check(self):
        now = datetime.now(IST)
        for task in db.get_tasks(include_disabled=False):
            schedule = dtime(hour=task["schedule_hour"],
                             minute=task["schedule_minute"])
            if now.time() < schedule:
                continue  # not due yet today
            if db.task_ran_today(task["id"]):
                continue  # already fired (or skipped) today
            log("scheduler: task '%s' is due (%02d:%02d)"
                % (task["id"], task["schedule_hour"], task["schedule_minute"]))
            self._fire(task)

    def _fire(self, task):
        keywords = json.loads(task["keywords"])
        lock = ScrapeLock()
        if not lock.acquire():
            db.create_finished_run(
                task_id=task["id"], mode="scheduled", keywords=keywords,
                status="skipped", error="scrape lock busy",
            )
            log("scheduler: task '%s' skipped (scrape lock busy)" % task["id"])
            return
        run_id = db.create_run(task_id=task["id"], mode="scheduled",
                               keywords=keywords)
        log("scheduler: firing task '%s' as run %d" % (task["id"], run_id))
        try:
            result = run_scrape(keywords, write_csv=True)
            db.insert_results(run_id, result["rows"])
            db.finish_run(run_id, status="completed",
                          total_count=len(result["rows"]),
                          new_count=result["new_count"])
            log("scheduler: run %d completed (%d bids, %d new)"
                % (run_id, len(result["rows"]), result["new_count"]))
            sheets.auto_save_run(run_id, "scheduled")
        except Exception as err:  # last line of defense — log and record
            db.finish_run(run_id, status="failed", error=str(err))
            log("scheduler: run %d FAILED: %s" % (run_id, err))
        finally:
            lock.release()
