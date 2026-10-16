---
name: health
description: System health check — data freshness, Yahoo access status, email configuration, missed scheduled runs, open/stale proposals. Use when something seems off or weekly.
---

# Health check

1. Call the `health_check` tool first (shell: `uv run fbm health`; exit code 1 on any FAIL). It returns every check
   below as OK / WARN / FAIL with a detail and a fix; thresholds are `health.*` constants (`fbm constants`).
   Use `fbm status`, `fbm data status`, `fbm notify outbox` and `context_pack` only to dig into a WARN/FAIL.
2. What it covers (report as a short table):
   - ESPN data: `sync_log` times for schedule, boxscores, injuries, news (WARN if >24h during the season).
   - Yahoo: credentials, league configured, `[league] source` (demo vs yahoo).
   - Email: Gmail configured (`fbm status`); recent failures in the outbox (`uv run fbm notify outbox`).
   - Proposals: anything `awaiting_owner` > 12h, or approved but `proposal_steps` not yet run.
   - Scheduled runs: today's/yesterday's journal has the expected daily-gm entry; flag gaps (PC asleep, app closed).
3. Fix what you safely can (e.g. `refresh_data`); list what needs the owner.
4. Email only on WARN/FAIL (kind `alert`).
