---
name: matchup
description: This week's head-to-head outlook. Use for "How's my matchup looking?", "Which categories can I still flip?", "What do I need to win STL this week?", "Am I safe in FG%?".
---

# Matchup

Run as the `gm` agent. Every number comes from tool output.

1. `context_pack`. In the Yahoo league, `scoreboard` for the live category totals already banked this week (and
   `snapshot` if `fetched_at` is stale); the demo league has no scoreboard.
2. `simulate_matchup` (current opponent; it includes banked stats in the Yahoo league). Report expected categories
   won, P(win week), per-category win odds, swing categories, and `if_opponent_streams`.
3. Category questions ("can I flip STL?"): read that category's odds; `streaming_plan` and
   `free_agents(sort="week")` show adds that move it — confirm a specific add with `evaluate_add_drop(add, drop,
   opponent)`. Protect FG%/FT% when ahead late in the week; chase counting stats when behind.
4. Context: `schedule` (games left), `injuries(scope="my_roster")`; opponent habits via `league_intel` /
   `manager_profile` if available, and `memory/managers/`.
5. Lead with the verdict, then a short category odds table. Any add/drop is a proposal (`create_proposal` →
   `reviewer` → owner approval → `execute_proposal`; Yahoo league = manual steps).
