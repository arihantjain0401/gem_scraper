# CLAUDE.md

## Execution environment (read this first)

Claude Code runs **on the iMac itself** (verified 2026-09-04: `scutil --get
ComputerName` → "AMIT's iMac"; files are on the local APFS disk — no SMB
mount). Use local paths and the native venv directly, no SSH hop:

```bash
cd ~/gem-scraper && .venv/bin/python …
```

Machine facts: iMac `amitkumarjain`, MagicDNS `amits-imac`, Tailscale
`100.115.236.19`. If a future Claude Code session runs on the MacBook (an SMB
mount under `/Volumes/amitkumarjain/...` would be visible in `mount`), the old
rules apply: edit via the mount, **never run the venv Python through the
mount** (large binary loads over SMB stall — see the dyld freeze incident of
2026-09-02), execute via `ssh amitkumarjain@amits-imac 'cd ~/gem-scraper && …'`.

Key facts:
- Venv on the iMac: `.venv` (iMac-native paths — never run it from the mount).
- Runtime: one launchd job `com.gemscraper.web` (web :8050 + in-app scheduler
  + ngrok tunnel); restart with `launchctl kickstart -k gui/$(id -u)/com.gemscraper.web`.
- CLIs (on the iMac): `.venv/bin/python -m app.scraper.cli --keyword …`,
  `scripts/manage_task.py`, `scripts/install_launchd.py` (idempotent).
- Monitor subsystem (parked): `app.monitor.cli` + `scripts/install_monitor_launchd.py`.
- Web UI: `http://100.115.236.19:8050` (Tailscale) + public ngrok URL.
- Docs: `docs/ARCHITECTURE.md` (component map), `docs/GOALS.md`,
  `docs/FUTURE_TASKS.md`.
