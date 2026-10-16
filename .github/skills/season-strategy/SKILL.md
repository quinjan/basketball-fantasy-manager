---
name: season-strategy
description: Build or revise the season-long category strategy (which categories to target or punt) from the league-wide category profile, then submit it as a reviewed strategy proposal for the owner's approval.
---

# Season strategy

Run as the `gm` agent.

1. `context_pack`, `league_profile` (n≥4000), `my_roster`.
2. Diagnose my build: categories where I'm top-4, middle, bottom-4 (win rate vs league). Note correlated pairs
   (big men: REB/BLK/FG% vs FT%; guards: AST/3PM/STL/FT% vs FG%/TO).
3. Run `punt_scenarios` (auto: no punt, every single category, pairs of my weakest; or pass specific builds, e.g.
   `scenarios="FT%;3PM,FT%"`). For each build it reports expected categories and P(win the week) after the
   free-agent swaps the build would make and the simulation confirms, reliable categories, `lens_conflicts`
   (moves the build's rankings would get wrong) and buy/sell market edges. Use `rankings(punts=...)` /
   `free_agents(punts=...)` only to illustrate a build.
4. Recommend one. Follow the tool's `recommendation`: switch only on `propose_switch` (edge ≥ the switch margin);
   otherwise keep the approved build. Explain the trade-off in plain language with the numbers. Punting is a
   commitment — mention how it affects waivers, trades (the buy targets), and late-week streaming (e.g. punting
   FT% frees you to stream bigs).
5. `create_proposal` kind `strategy`, payload `{"punts": [...], "targets": [...], "notes": "..."}` → `reviewer`
   agent → ask the owner (or email kind `approval`).
6. After execution, rewrite `memory/strategy.md` (narrative: build, targets, punts, how to play waivers/trades
   under it, conditions that would make us revisit), journal, and `uv run fbm sync-memory`.
