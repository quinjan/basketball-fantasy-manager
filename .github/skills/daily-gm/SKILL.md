---
name: daily-gm
description: The GM's main daily run (after all NBA games are final, ~15:00 UTC+8). Refresh data, set lineups, assess the matchup, scan free agents/streams, draft reviewed proposals, email the daily report.
---

# Daily GM run

Run as the `gm` agent. Follow AGENTS.md. Be efficient: this runs every day.

1. **Load & refresh.** `context_pack` → `refresh_data`. Note any `rostered_injury_changes`.
2. **Lineups (auto).** `apply_lineup` for the next ET game day (`nba_game_day_et`, or the next day with games).
   Include manual steps in the report if returned.
3. **Matchup.** `simulate_matchup` (current opponent). Identify swing categories, expected categories won,
   P(win week), games-started gap, and remaining adds this week.
4. **Roster health.** `my_roster` + `injuries(scope="my_roster")`: flag Out/GTD players, IL moves, dead spots.
5. **Market scan.** `free_agents(sort="week")` and `free_agents(sort="value")` (strategy punts apply). For the best
   1–3 candidates, run `evaluate_add_drop` against your weakest/droppable players. Consider the weekly add
   limit, waiver priority cost, and strategy.
6. **Proposals.** Only if a move is clearly positive (meaningful gain in expected cats this week and/or
   rest-of-season value without a bigger loss): `create_proposal` → invoke the `reviewer` agent → handle
   DISAGREE honestly. Set expiry before the relevant tip-off. Max 3 per run. Doing nothing is a valid outcome.
7. **Approvals.** If the owner is present, follow the `approvals` skill. If not, include pending proposals in the
   email and end.
8. **Report.** Write `reports/daily/<local-date>.md` and `send_email` (kind `report`; kind `approval` if anything
   needs a decision) using the report format in the GM manual. Keep it short.
9. **Journal & sync.** `journal_append` a 3–6 line summary (decisions + key numbers), then
   `uv run fbm sync-memory -m "daily-gm <date>"`.
