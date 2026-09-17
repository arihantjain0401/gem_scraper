# scripts/

Operational scripts — run them **on the iMac via SSH**, not through the SMB mount.

- `setup_venv.sh` — create `.venv` + install `requirements.txt` (one-time)
- `install_launchd.py` — idempotent: loads the single launchd job
  (`com.gemscraper.web` = website + in-app scheduler + ngrok tunnel), seeds
  task `daily` (06:30, the 6 initial keywords), removes legacy job plists
- `manage_task.py` — the ONLY place scheduled tasks are created (DB only —
  the in-app scheduler picks them up):
  `list` / `create <id> <HH:MM> [name] --keyword ...` / `delete <id>`
- `manage_users.py` — site users: `list` / `add <username> <display-name>
  <admin|limited> [--password ...]` / `password <username>` / `delete
  <username>` (admin `arihant` is protected from deletion)
