---
name: daily-gm
description: The GM's main daily run (after all NBA games are final, ~10:00 ET). Refresh data, recommend lineups, assess the matchup, scan free agents/streams, draft reviewed proposals, email the daily report.
---

# Daily GM run

Run as the `gm` agent. Follow AGENTS.md. Be efficient: this runs every day.

1. **Load & refresh.** `context_pack` → `refresh_data` (ESPN sync and, with Hashtag enabled, the daily HT import:
   `hashtag_import` in its output) → `snapshot` (Yahoo league; fresh rosters, scoreboard, waivers) →
   `health_check` (mention any WARN/FAIL with its fix) → `hashtag_status` when Hashtag is enabled. Note any
   `rostered_injury_changes` and `early_signals.signals_new` (signals first caught by this refresh).
   **Suggestions paused?** If HT is enabled but unhealthy (`context_pack.suggestions_paused`, `hashtag_status`,
   or any tool returning `HT_UNHEALTHY`): no fallback. Skip steps 2–3's recommendations and steps 5–7 (no lineup
   or move suggestions, no proposals), still report injuries, scoreboard, standings, transactions, news, and put the
   fix (e.g. "run `fbm hashtag login`" / "`fbm hashtag import`") at the top of the report and email it (kind
   `alert`).
2. **Lineup recommendation.** `lineup_steps(day, trigger="daily-gm")` for the next ET game day
   (`nba_game_day_et`, or the next day with games). It returns the recommended lineup and exact steps for the owner
   (and logs the lineup decision) — put them at the top of the report.
3. **Matchup.** In the Yahoo league, `scoreboard` for stats already banked this week. Once per day call
   `weekly_plan(trigger="daily-gm")` (official odds: HT weekly totals + FBM spread; chase/hold/lock/concede status
   and category weights; logs the chase_concede / category_weights decisions) **or**
   `simulate_matchup(record=true)` (current opponent; includes banked stats; records the forecast that
   `odds_reliability` grades). Identify swing categories, expected categories won, P(win week), games-started gap,
   and remaining adds. HT's own "probability" column is a share formula: reference only.
4. **Roster health.** `my_roster` + `injuries(scope="my_roster")`: flag Out/GTD players, IL moves, dead spots.
5. **Early signals first.** `signals(new_only=true)` (medium/high confidence by default). Free agents with an
   up signal (`opportunity`, `role_change` up, `buy_low`, `ht_revision` up): check the cause (`news`, `player_card`)
   and test them now with `evaluate_add_drop` / `waiver_decision` — the edge is acting before other managers. My
   players with down signals (`sell_high`, lost job, minutes drop): note them for `trade-scan` or a replacement.
   Other teams' players with up signals: note as trade targets. Then `fa_radar` (every free agent / waiver player,
   not just flagged ones: signals + minutes jump + ownership surge + value vs my worst player) and
   `ownership_trends` (what the league is adding/dropping); test the top radar hits with the tool it names.
6. **Market scan.** `streaming_plan` for this week's best add/drop sequence (it respects the add limit, strategy
   punts, and rest-of-season cost; HT mode: candidates come from HT's waiver wire run with FBM's category
   multipliers, FBM picks the plan and logs it), plus `free_agents(sort="value")` for long-term upgrades. Confirm the 1–3 best
   moves with `evaluate_add_drop`. For anyone on waivers, `waiver_decision` decides claim / wait / pass under rolling
   priority. Check `simulate_matchup`'s `if_opponent_streams` before deciding how aggressive to be.
7. **Proposals.** Only if a move is clearly positive (meaningful gain in expected cats this week and/or
   rest-of-season value without a bigger loss): `create_proposal` → invoke the `reviewer` agent → handle
   DISAGREE honestly. Set expiry before the relevant tip-off. Max 3 per run. Doing nothing is a valid outcome.
8. **Offers & approvals.** In the Yahoo league, `pending_offers`: summarize each incoming offer's evaluation and
   recommend accept/counter/reject (accepting is a manual step for the owner). If the owner is present, follow the `approvals` skill. If not, include pending proposals in the
   email and end.
9. **Report.** Write `reports/daily/<local-date>.md` and `send_email` (kind `report`; kind `approval` if anything
   needs a decision) using the report format in the GM manual (watchlist from the signals). Keep it short.
10. **Journal & sync.** `journal_append` a 3–6 line summary (decisions + key numbers)
   (memory is saved locally).
