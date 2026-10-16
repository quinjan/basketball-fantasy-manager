---
name: daily-gm
description: The GM's main daily run (after all NBA games are final, ~15:00 UTC+8). Refresh data, set lineups, assess the matchup, scan free agents/streams, draft reviewed proposals, email the daily report.
---

# Daily GM run

Run as the `gm` agent. Follow AGENTS.md. Be efficient: this runs every day.

1. **Load & refresh.** `context_pack` → `refresh_data`. Note any `rostered_injury_changes`.
2. **Lineups (auto).** `apply_lineup` for the next ET game day (`nba_game_day_et`, or the next day with games).
   Include manual steps in the report if returned.
3. **Matchup.** `simulate_matchup(record=true)` (current opponent; recording feeds the calibration loop).
   Identify swing categories, expected categories won, P(win week), games-started gap, and remaining adds.
4. **Roster health.** `my_roster` + `injuries(scope="my_roster")`: flag Out/GTD players, IL moves, dead spots.
5. **Market scan.** `streaming_plan` for this week's best add/drop sequence (it respects the add limit, strategy
   punts, and rest-of-season cost), plus `free_agents(sort="value")` for long-term upgrades. Confirm the 1–3 best
   moves with `evaluate_add_drop`. For anyone on waivers, `waiver_decision` decides claim / wait / pass under rolling
   priority. Check `simulate_matchup`'s `if_opponent_streams` before deciding how aggressive to be.
6. **Proposals.** Only if a move is clearly positive (meaningful gain in expected cats this week and/or
   rest-of-season value without a bigger loss): `create_proposal` → invoke the `reviewer` agent → handle
   DISAGREE honestly. Set expiry before the relevant tip-off. Max 3 per run. Doing nothing is a valid outcome.
7. **Approvals.** If the owner is present, follow the `approvals` skill. If not, include pending proposals in the
   email and end.
8. **Report.** Write `reports/daily/<local-date>.md` and `send_email` (kind `report`; kind `approval` if anything
   needs a decision) using the report format in the GM manual. Keep it short.
9. **Journal & sync.** `journal_append` a 3–6 line summary (decisions + key numbers), then
   `uv run fbm sync-memory -m "daily-gm <date>"`.
