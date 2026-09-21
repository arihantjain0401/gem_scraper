#!/bin/zsh
set -eu

REPO_DIR="${0:A:h:h}"
LOG_DIR="$HOME/Library/Logs/claude-session-sync"
mkdir -p "$LOG_DIR"

cd "$REPO_DIR"
if [[ "$(/usr/bin/git remote get-url origin)" != "https://github.com/arihantjain0401/gem_scraper.git" ]]; then
  print -u2 "Refusing to sync: origin is not arihantjain0401/gem_scraper."
  exit 1
fi
/usr/bin/git pull --rebase origin main
/usr/bin/python3 scripts/export_claude_sessions.py --output session-summaries

/usr/bin/git add -- session-summaries
if /usr/bin/git diff --cached --quiet; then
  print "No new Claude session summaries."
  exit 0
fi

/usr/bin/git commit -m "chore: sync Claude session summaries $(/bin/date +%F)"
/usr/bin/git push origin main
