---
name: lineup
description: Recommend or explain a daily lineup (with exact steps the owner does in Yahoo). Use for "Set today's lineup", "Who sits tonight if X is ruled out?", "Why is X on my bench?", "Set my lineup for tomorrow".
---

# Lineup

Run as the `gm` agent. Lineups and IL moves are **recommendations**: FBM never changes the league — the owner sets them in Yahoo.

1. `context_pack` (once per session). Use the ET game day (`nba_game_day_et`); convert times to the owner's time zone.
2. If injury news may have moved, `refresh_data` (and `snapshot` in the Yahoo league so rosters are current).
3. `optimize_lineup(day)` and explain it: games started first, then value, ties broken by this week's swing
   categories; started games are locked (`locked_game_started`); IL moves respect roster capacity.
4. "Why is X benched?" → cite X's numbers vs the starter who beat him from the tool output (games, value, swing
   tie-break, lock). "Who sits if X is out?" → check `injuries` / `player_card(X)` and re-run `optimize_lineup`
   once his status is known; say which player would start, from the tool output.
5. `lineup_steps(day)` returns the recommended lineup with exact steps — give them to the owner verbatim (slot by
   slot, before tip-off in local time). When the owner says it's set, `lineup_steps(day, confirm=true)` (demo league:
   this updates the demo roster).
6. `il_moves_blocked` non-empty → a healthy player is stuck in IL with a full roster: draft an add/drop proposal
   (`approvals` flow). Journal the recommendation if it changed anything (`journal_append`).
