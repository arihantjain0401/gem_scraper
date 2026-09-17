# app/scheduler/

Daily task scheduling — no per-task launchd jobs anymore.

- `inapp.py` — `InAppScheduler`: a thread inside the web process checks every
  30 s whether an enabled task's HH:MM has arrived (IST) and no scheduled run
  exists for it today, then fires the scrape with the cross-process lock. If
  the app was down or the iMac asleep at the scheduled time, the first check
  after recovery fires the missed run (catch-up), matching launchd's
  wake behaviour. Lock-busy fires are recorded as `skipped`.

Tasks live in `data/app.db` (keywords + schedule). Editing keywords never
affects anything else. The old `run_task.py` launchd entry was removed with
the single-job consolidation (2026-08-31).
