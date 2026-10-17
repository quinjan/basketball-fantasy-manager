---
name: lineup-guard
description: Frequent, cheap lineup check during NBA game windows (hourly ~11:00-24:00 ET). Refresh injuries, re-optimize today's ET lineup for late scratches, and send the owner the recommended steps. Silent unless something changed.
---

# Lineup guard

Run as the `gm` agent but keep it short and cheap. No proposals, no market scan.

1. `refresh_data`. It also detects early signals; note `early_signals.signals_new`.
2. `optimize_lineup` for today's ET game day (`nba_game_day_et` from `league_overview`). Players whose games have
   started are locked (`locked_game_started`); IL moves are included. Ties are broken by this week's
   swing categories.
3. If the optimal lineup or IL moves differ from the current roster, or a rostered player's injury status changed:
   `lineup_steps` for that day. It returns the recommended lineup and exact steps; FBM never changes the league, so
   send them to the owner with the tip-off time in local time (the next `snapshot` shows whether they were done).
4. If `il_moves_blocked` is non-empty, a healthy player is stuck in IL with a full roster (Yahoo blocks roster moves
   until fixed): alert the owner and draft an add/drop proposal on the next `daily-gm` run.
5. **Next man up.** If `signals_new` has an `opportunity` or `role_change` (up) signal of medium/high confidence
   for a player who is a free agent (`signals(scope="fa", new_only=true)`), it's time-sensitive: include it in the
   alert with its note (other managers react within hours). No proposals here — the next `daily-gm` run (or the
   owner) evaluates the pickup.
6. Only if something changed or needs the owner: `send_email` kind `alert` with the recommended steps
   and the reason (e.g. "X ruled OUT 40 min before tip; started Y instead").
7. If nothing changed, do not email. Journal only when a new recommendation was sent (`journal_append`).
