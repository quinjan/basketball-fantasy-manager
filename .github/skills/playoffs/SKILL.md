---
name: playoffs
description: Fantasy-playoff planning. Use for "Playoff schedule outlook", "Who should I target for playoff weeks?", "Which of my players have bad playoff schedules?", "Am I making the playoffs?".
---

# Playoffs

Run as the `gm` agent.

1. `context_pack`; in the Yahoo league `standings` for the playoff race.
2. `playoff_outlook(scope="my_roster")` — playoff weeks, games per player per playoff week, playoff value, and the
   current trade playoff weight. Targets: `playoff_outlook(scope="free_agents")` and `scope="league"`.
3. Cross-check targets with `free_agents` / `waiver_decision` (unowned) or `find_trades` / `evaluate_trade`
   (owned; the trade output's playoff weight grows toward the playoffs).
4. Sells: rostered players with light playoff schedules and high trade value (`rankings`).
5. Any move is a proposal (`create_proposal` → `reviewer` → owner → `execute_proposal`; Yahoo = manual steps).
