"""CLI for managing scheduled tasks — the ONLY place new tasks are created.

Tasks are fired by the in-app scheduler (app/scheduler/inapp.py) inside the
web process — there are no per-task launchd jobs anymore. The website
deliberately has no create option (edit-keywords / delete only).

Usage (on the iMac, from the project root):
    .venv/bin/python scripts/manage_task.py list
    .venv/bin/python scripts/manage_task.py create <task_id> <HH:MM> [name] --keyword "..." [--keyword ...]
    .venv/bin/python scripts/manage_task.py delete <task_id>
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db
from app.scraper.util import validate_keyword


def cmd_list(_args):
    for t in db.get_tasks(include_disabled=True):
        state = "enabled" if t["enabled"] else "disabled"
        print("%-20s %-8s %02d:%02d daily | %s" % (
            t["id"], state, t["schedule_hour"], t["schedule_minute"],
            ", ".join(json.loads(t["keywords"]))))


def cmd_create(args):
    hour, minute = (int(x) for x in args.time.split(":"))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        print("bad time '%s' — use HH:MM (24h)" % args.time)
        sys.exit(1)
    keywords = [k for k in (validate_keyword(k) for k in args.keyword) if k]
    if not keywords:
        print("no valid keywords supplied")
        sys.exit(1)

    if db.get_task(args.task_id) is None:
        db.create_task(args.task_id, args.name or args.task_id, keywords, hour, minute)
        print("created task '%s' (fires daily at %02d:%02d via the in-app scheduler)"
              % (args.task_id, hour, minute))
    else:
        db.update_task_keywords(args.task_id, keywords)
        db.enable_task(args.task_id, hour, minute)
        print("task '%s' already existed — updated keywords/schedule, re-enabled"
              % args.task_id)


def cmd_delete(args):
    if db.get_task(args.task_id) is None:
        print("no such task: %s" % args.task_id)
        sys.exit(1)
    db.disable_task(args.task_id)
    print("deleted task '%s' (row kept, disabled — the scheduler skips it)"
          % args.task_id)


def main():
    parser = argparse.ArgumentParser(description="Manage gem-scraper scheduled tasks.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list").set_defaults(func=cmd_list)

    p_create = sub.add_parser("create")
    p_create.add_argument("task_id")
    p_create.add_argument("time", help="HH:MM (24h, local IST)")
    p_create.add_argument("name", nargs="?", default=None)
    p_create.add_argument("--keyword", action="append", required=True)
    p_create.set_defaults(func=cmd_create)

    p_delete = sub.add_parser("delete")
    p_delete.add_argument("task_id")
    p_delete.set_defaults(func=cmd_delete)

    args = parser.parse_args()
    db.init()
    args.func(args)


if __name__ == "__main__":
    main()
