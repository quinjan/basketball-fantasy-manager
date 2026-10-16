---
name: season-strategy
description: Build or revise the season-long category strategy (which categories to target or punt) from the league-wide category profile, then submit it as a reviewed strategy proposal for the owner's approval.
---

# Season strategy

Run as the `gm` agent.

1. `context_pack`, `league_profile` (n≥4000), `my_roster`.
2. Diagnose my build: categories where I'm top-4, middle, bottom-4 (win rate vs league). Note correlated pairs
   (big men: REB/BLK/FG% vs FT%; guards: AST/3PM/STL/FT% vs FG%/TO).
3. Evaluate 2–4 scenarios: no punt, punt one category, punt two correlated categories. For each, use
   `rankings(punts=...)` and `free_agents(punts=...)` to see how my players' value and the available upgrades
   change, and estimate the target: win 5+ of the remaining categories most weeks.
4. Recommend one. Explain the trade-off in plain language with the numbers. Punting is a commitment — mention
   how it affects waivers, trades, and late-week streaming (e.g. punting FT% frees you to stream bigs).
5. `create_proposal` kind `strategy`, payload `{"punts": [...], "targets": [...], "notes": "..."}` → `reviewer`
   agent → ask the owner (or email kind `approval`).
6. After execution, rewrite `memory/strategy.md` (narrative: build, targets, punts, how to play waivers/trades
   under it, conditions that would make us revisit), journal, and `uv run fbm sync-memory`.
