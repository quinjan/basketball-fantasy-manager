---
name: lineup-guard
description: Frequent, cheap lineup check during NBA game windows (hourly ~23:00-12:00 UTC+8). Refresh injuries, re-optimize today's ET lineup for late scratches, auto-apply. Silent unless something changed.
---

# Lineup guard

Run as the `gm` agent but keep it short and cheap. No proposals, no market scan.

1. `refresh_data`.
2. `optimize_lineup` for today's ET game day (`nba_game_day_et` from `league_overview`). Players whose games have
   started are locked automatically (`locked_game_started`); IL moves are included.
3. If the optimal lineup or IL moves differ from the current roster, or a rostered player's injury status changed:
   `apply_lineup` for that day.
4. If `il_moves_blocked` is non-empty, a healthy player is stuck in IL with a full roster (Yahoo blocks roster moves
   until fixed): alert the owner and draft an add/drop proposal on the next `daily-gm` run.
5. Only if something changed or needs the owner: `send_email` kind `alert` with the change list (or manual steps)
   and the reason (e.g. "X ruled OUT 40 min before tip; started Y instead").
6. If nothing changed, do not email. Journal only when an action was taken (`journal_append` kind `execution`).
