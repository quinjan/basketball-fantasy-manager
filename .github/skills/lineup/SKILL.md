---
name: lineup
description: Set or explain a daily lineup on demand. Use for "Set today's lineup", "Who sits tonight if X is ruled out?", "Why is X on my bench?", "Set my lineup for tomorrow".
---

# Lineup

Run as the `gm` agent. Lineups and IL compliance moves are auto-allowed (no proposal).

1. `context_pack` (once per session). Use the ET game day (`nba_game_day_et`); convert times for the owner (UTC+8).
2. If injury news may have moved, `refresh_data` (and `snapshot` in the Yahoo league so rosters are current).
3. `optimize_lineup(day)` and explain it: games started first, then value, ties broken by this week's swing
   categories; started games are locked (`locked_game_started`); IL moves respect roster capacity.
4. "Why is X benched?" → cite X's numbers vs the starter who beat him from the tool output (games, value, swing
   tie-break, lock). "Who sits if X is out?" → check `injuries` / `player_card(X)` and re-run `optimize_lineup`
   once his status is known; say which player would start, from the tool output.
5. Apply with `apply_lineup(day)`. In the Yahoo league writes are out of scope: it returns exact manual steps —
   give them to the owner verbatim (slot by slot, before tip-off in local time). The demo league applies directly.
6. `il_moves_blocked` non-empty → a healthy player is stuck in IL with a full roster: draft an add/drop proposal
   (`approvals` flow). Journal any applied change (`journal_append` kind `execution`).
