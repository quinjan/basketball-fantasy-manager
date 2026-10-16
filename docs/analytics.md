# Analytics methods

All numbers come from deterministic, tested Python in `src/fbm/analytics/`. The AI layer only reads these outputs.
Every tunable value lives in the constants registry (see the end of this page).

## G-scores (player value)

FBM ranks players with **G-scores** (Rosenof, *"Improving Fantasy Basketball Rankings with G-scores"*,
[arXiv:2307.02188](https://arxiv.org/abs/2307.02188)) — a Z-score variant that accounts for week-to-week variance in
head-to-head play:

`G = (μ_player − μ_pool) / √(σ² + κτ²)`, where σ is the spread of player means in the pool, τ is the root-mean-square
week-to-week standard deviation, and κ = 2N/(2N−1).

- FG% and FT% use volume-weighted impact (a high percentage on low volume matters less).
- TO counts negatively. Z-scores are shown alongside for comparison.
- Rankings are punt-aware: punted categories are excluded from value.

## Projections

- Current-season per-game stats are shrunk toward last season's, weighted by games played.
- Recent per-minute form is blended in.
- Counting stats are scaled by the recent-minutes trend (recent games vs season, damped and capped) so role changes
  show up early.

## Availability

ESPN injury status and expected return dates scale rest-of-season value (rankings, free agents, waivers, trades,
punt scenarios) and season-level simulations; in matchup simulations each game is weighted by a play probability.

## Matchup simulation

Monte Carlo over the rest of the fantasy week. It resamples players' real game lines (which keeps correlations
between stats) and multiplies by play probability. In the Yahoo league the stats already banked this week (live
scoreboard) are added to the simulated remainder. Output includes per-category win/tie/loss odds and an "if the
opponent streams" upper bound (the streaming planner run from their side).

## Daily lineup

Maximizes games started, then value; ties are broken by this week's swing categories. It prefers leaving players in
place (fewer manual changes). Players whose game has tipped off are locked. IL moves respect roster capacity;
activations that can't fit become proposals.

## Streaming planner

Greedy search over add/drop sequences on the remaining game days within the weekly add limit. Each candidate
re-plans daily lineups and re-simulates the matchup with common random numbers; the rest-of-season cost of the drop
is penalized.

## Rolling waivers

Claim if the rest-of-season gain clears a bar that rises with the waiver priority you'd spend (priority rotates to
last after a claim). Below the bar, wait for free agency only if no other team is likely to claim the player;
otherwise pass.

## Trades

Season-level evaluation: expected categories won in a typical week against every team, for both sides, with rosters
rebalanced (forced drops, free-agent fills). The finder screens candidates by need-weighted G-scores, confirms them
by simulation, and filters offers that cost the partner too much value to be realistic. Playoff-schedule value is
added and weighted up as the playoffs approach.

## Punt scenarios

Each build (no punt, every single category, and pairs of your weakest) re-values players, proposes free-agent swaps
in that order, and keeps only swaps the simulation confirms. The resulting roster is scored in all categories against
every team. A switch is recommended only past a margin. Reports lens conflicts (moves a build's rankings would get
wrong) and buy/sell edges (build rank vs market rank).

## Fantasy playoffs

Fantasy weeks run Monday–Sunday (ET). Playoff weeks come from the Yahoo league settings when available, otherwise
the last `playoffs.weeks` weeks of the NBA schedule (a registered constant). FBM reports NBA team games per playoff week (best and
worst schedules) and each player's playoff games and value above replacement.

## Opponent intel

Built from league transaction history: each manager's adds per week, waiver claims, streaming habits, trades, and
apparent category build, plus the best trade partners by complementary needs.

## Learning loop

- **Decision grading:** completed (`done`) add/drops, waiver claims and trades are graded by realized production since
  completion (acquired minus given up), in team-week standard-deviation units.
- **Calibration:** finished matchup predictions are graded by Brier score against a coin-flip baseline, with
  reliability buckets.

## Draft assistant

Punt-aware G-score board with tiers, compared against ADP (ESPN by default, Yahoo draft analysis when refreshed).
Live recommendations are build-aware and account for who is likely available at your next pick; picks can be
synced live from Yahoo. Mock drafts (others by noisy ADP) are scored by season-level simulation.

## Constants registry

Every tunable constant is registered in `src/fbm/constants.py` with value, default, unit, kind (measured, research,
rule, heuristic) and source. `uv run fbm constants` lists them; `config/constants.toml` overrides are validated
before use.
