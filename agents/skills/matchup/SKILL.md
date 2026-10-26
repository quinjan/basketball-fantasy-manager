---
name: matchup
description: This week's head-to-head outlook. Use for "How's my matchup looking?", "Which categories can I still flip?", "What do I need to win STL this week?", "Am I safe in FG%?".
---

# Matchup

Run as the `gm` agent. Every number comes from tool output.

1. `context_pack`. In the Yahoo league, `scoreboard` for the live category totals already banked this week (and
   `snapshot` if `fetched_at` is stale); the demo league has no scoreboard. "Who carried / sank us?" → `week_box`
   (my team and the opponent, per player: totals, FG/FT makes/attempts, G-score sum).
2. `weekly_plan(trigger="owner-ask")` and/or `simulate_matchup` (current opponent; banked stats included in the
   Yahoo league). Report expected categories won, P(win week), per-category win odds, chase/hold/lock/concede status,
   swing categories, and `if_opponent_streams` (in Hashtag mode quote its `official_if_opponent_streams` — same
   basis as the official odds; the FBM-sim stand_pat/streaming numbers are not). **Official odds** (Hashtag mode) = HT's projected weekly totals
   (policy view `policy.view.weekly_odds`, default STREAM7) with injured players removed (HT's grid counts them;
   see `injured_excluded`, `*_ht_grid` vs `*_total_ht`) + FBM's simulator spread × `policy.odds_spread_scale`.
   HT's displayed "probability" is a share formula (mine/(mine+opp)), not a win probability: quote it only as a
   reference, labelled as such. `odds_reliability` says how well the official odds have held up vs Yahoo finals.
3. Category questions ("can I flip STL?"): read that category's odds; `streaming_plan` and
   `free_agents(sort="week")` show adds that move it — confirm a specific add with `evaluate_add_drop(add, drop,
   opponent)`. Protect FG%/FT% when ahead late in the week; chase counting stats when behind.
4. Context: `schedule` (games left), `injuries(scope="my_roster")`; opponent habits via `league_intel` /
   `manager_profile` if available, and `memory/managers/`.
5. Lead with the verdict, then a short category odds table. Any add/drop is a proposal (`create_proposal` →
   `reviewer` → owner approval → `proposal_steps` → owner does it in Yahoo → `confirm_done`).
