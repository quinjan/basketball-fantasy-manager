---
name: lineup-guard
description: Frequent, cheap lineup check during NBA game windows (hourly ~23:00-12:00 UTC+8). Refresh injuries, re-optimize today's ET lineup for late scratches, auto-apply. Silent unless something changed.
---

# Lineup guard

Run as the `gm` agent but keep it short and cheap. No proposals, no market scan.

1. `refresh_data`.
2. `optimize_lineup` for today's ET game day (`nba_game_day_et` from `league_overview`). If any started game is
   already in progress, players in those games are locked — only change players whose games haven't started.
3. If the optimal lineup differs from the current one, or a rostered player's injury status changed:
   `apply_lineup` for that day.
4. Only if something changed or needs the owner: `send_email` kind `alert` with the change list (or manual steps)
   and the reason (e.g. "X ruled OUT 40 min before tip; started Y instead").
5. If nothing changed, do not email. Journal only when an action was taken (`journal_append` kind `execution`).
