---
name: health
description: System health check — data freshness, Yahoo access status, email configuration, missed scheduled runs, open/stale proposals. Use when something seems off or weekly.
---

# Health check

1. Run `uv run fbm status` and `uv run fbm data status` (shell), and `context_pack` (tool).
2. Check and report as a short table (OK / WARN / FAIL):
   - ESPN data: `sync_log` times for schedule, boxscores, injuries, news (WARN if >24h during the season).
   - Yahoo: credentials, league configured, write access, `[league] source` (demo vs yahoo).
   - Email: Gmail configured (`fbm status`); recent failures in the outbox (`uv run fbm notify outbox`).
   - Proposals: anything `awaiting_manual` > 12h, or approved-but-not-executed.
   - Scheduled runs: today's/yesterday's journal has the expected daily-gm entry; flag gaps (PC asleep, app closed).
3. Fix what you safely can (e.g. `refresh_data`); list what needs the owner.
4. Email only on WARN/FAIL (kind `alert`).
