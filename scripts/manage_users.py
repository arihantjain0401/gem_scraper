"""CLI for managing site users (admins and limited users).

Limited users can extract and view results only — no AI summary, no scheduler,
no history, no access log, no save-to-sheet. Admins get everything.

Usage (on the iMac, from the project root):
    .venv/bin/python scripts/manage_users.py list
    .venv/bin/python scripts/manage_users.py add <username> <display-name> <role> --password "..."
    .venv/bin/python scripts/manage_users.py password <username> --password "..."
    .venv/bin/python scripts/manage_users.py delete <username>
"""

import argparse
import getpass
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db


def _password_or_prompt(args):
    if args.password:
        return args.password
    return getpass.getpass("Password: ")


def cmd_list(_args):
    for u in db.list_users():
        print("%-20s %-10s %-20s" % (u["username"], u["role"], u["display_name"]))


def cmd_add(args):
    if args.role not in ("admin", "limited"):
        print("role must be 'admin' or 'limited'")
        sys.exit(1)
    if db.get_user(args.username):
        print("user '%s' already exists — use 'password' to change the password"
              % args.username)
        sys.exit(1)
    password = _password_or_prompt(args)
    if not password:
        print("empty password not allowed")
        sys.exit(1)
    db.create_user(args.username, args.display_name, password, args.role)
    print("created user '%s' (%s) as %s" % (args.username, args.display_name, args.role))


def cmd_password(args):
    if db.get_user(args.username) is None:
        print("no such user: %s" % args.username)
        sys.exit(1)
    password = _password_or_prompt(args)
    if not password:
        print("empty password not allowed")
        sys.exit(1)
    db.set_user_password(args.username, password)
    print("password updated for '%s'" % args.username)


def cmd_delete(args):
    if args.username == "arihant":
        print("refusing to delete the admin 'arihant'")
        sys.exit(1)
    n = db.delete_user(args.username)
    print("deleted user '%s'" % args.username if n else "no such user: %s" % args.username)


def main():
    parser = argparse.ArgumentParser(description="Manage gem-scraper site users.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list").set_defaults(func=cmd_list)

    p_add = sub.add_parser("add")
    p_add.add_argument("username")
    p_add.add_argument("display_name")
    p_add.add_argument("role", choices=["admin", "limited"])
    p_add.add_argument("--password", default=None)
    p_add.set_defaults(func=cmd_add)

    p_pw = sub.add_parser("password")
    p_pw.add_argument("username")
    p_pw.add_argument("--password", default=None)
    p_pw.set_defaults(func=cmd_password)

    p_del = sub.add_parser("delete")
    p_del.add_argument("username")
    p_del.set_defaults(func=cmd_delete)

    args = parser.parse_args()
    db.init()
    args.func(args)


if __name__ == "__main__":
    main()
