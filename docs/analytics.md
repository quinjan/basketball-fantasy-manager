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

- **Preseason baseline.** Every data sync snapshots ESPN's season projections (`espn_projections`, one snapshot per
  ET day). Each player's last snapshot before opening night is his preseason baseline (players ESPN first projects
  later use their first snapshot). Players ESPN doesn't project fall back to last season's per-game stats. This is
  the prior: rookies and players who missed last season get real values.
- **FBM engine.** Current-season per-game stats are shrunk toward that prior, weighted by games played
  (`gp / (gp + 15)`). Recent per-minute form is blended in. Counting stats are scaled by the recent-minutes trend
  (recent games vs season, damped and capped) so role changes show up early.
- **ESPN blend.** The final projection is `(1 − w) × engine + w × ESPN's latest projection`. `w` starts at
  `projection.espn_weight` (0.5) and is replaced by a measured weight once enough forecasts are graded (below);
  an override in `config/constants.toml` always wins.

## Projection calibration

Every sync logs both forecasts (`projection_log`). After `projection.calibration_horizon_days` (14) each forecast is
graded against what the player actually averaged (≥ 3 games). Per stat the best blend weight has a closed form
(least squares on `(1 − w)·engine + w·ESPN − actual`); the overall weight minimises the sum of per-stat errors, each
normalised by that stat's spread across players. `projection_calibration` reports RMSE per stat, engine-only vs
ESPN-only vs blend error, and stores the measured weight once `projection.calibration_min_dates` forecast dates are
graded.

## Early signals

Detected after every sync (scheduled `fbm data sync`, `refresh_data` in the daily run and the hourly lineup guard),
stored once per episode with the time FBM first caught it, and graded 14 days later. `direction` is where the
player's value should go relative to what the market (season stats, rankings) sees.

| Kind | Trigger | Why it's early |
|---|---|---|
| `buy_low` / `sell_high` | Season-to-date value vs FBM's rest-of-season value differs by ≥ 1.5 G/game | Reason tags separate luck (FG%/FT%/3P% vs expectation, a key teammate out) from role (minutes, usage). Confidence is high when the gap is luck-driven, low when the role changed or the sample is small. |
| `role_change` | A bench player starts (or a starter is benched) at ≥ 24 min, or last-3-game minutes move ≥ 6 | Starts and minutes stabilise in a few games; the box score shows them days before averages move. |
| `opportunity` | A key player (≥ 24 projected min) is ruled out | Teammates' minutes with vs without him (this season's games first; last season's split is at most medium confidence), floor of 20 min without him. Flagged the moment the injury is posted, before any game is played. |
| `espn_revision` | ESPN's projection value moved ≥ 0.5 G/game in a week | ESPN's (and then the market's) view changes before season stats do. |

Grading: buy/sell is a hit when production over the window was closer to our projection than to the season line;
other kinds when the value or minutes moved in the predicted direction. In the Yahoo league the change in % owned
after the signal shows whether the market followed (our lead). Backtest on 2025-26 (7 cutoffs): role changes at
medium/high confidence hit ~70%, high-confidence buy-low ~87% (small sample), sell-high ~55%, low-confidence
signals ~45–55% — so the feed shows medium and high confidence by default (`signals.feed_min_confidence`).

## Availability

ESPN injury status and expected return dates scale rest-of-season value (rankings, free agents, waivers, trades,
punt scenarios) and season-level simulations; in matchup simulations each game is weighted by a play probability.

## Matchup simulation

Monte Carlo over the rest of the fantasy week. It resamples players' real game lines (which keeps correlations
between stats), re-centred on the projection table's per-game means (`sim.projection_anchor`, so role/minutes changes
and the ESPN blend show up), and multiplies by play probability. Each player's games in a simulated week share one
role/form multiplier (`sim.form_sd`, measured from week-to-week variance), and players with a history of sitting a
leg of back-to-backs carry that rest risk on their team's back-to-back dates (`sim.b2b_rest_*`). In the Yahoo league
the stats already banked this week (live scoreboard) are added to the simulated remainder. Output includes
per-category win/tie/loss odds and an "if the opponent streams" upper bound (the streaming planner run from their
side).

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
rebalanced (forced drops, free-agent fills from true free agents only, never players on waivers). The finder screens
candidates by need-weighted G-scores, confirms them by simulation, and filters offers that cost the partner too much
value to be realistic. That "ranking" value is measured above replacement level (the last rostered player), so a
partner's forced drop is never counted as a gain for them. The finder also asks for the partner's expendable players
(lowest value to their own build), turning a 2-for-1 into a roster-neutral 2-for-2 that takes their likely cut.
Playoff-schedule value is added and weighted up as the playoffs approach.

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
apparent category build, plus the best trade partners by complementary needs. History syncs incrementally from Yahoo
on every snapshot (and `refresh_data`); a manager's own claim rate replaces the default only once there is enough of it.

## Learning loop

- **Decision grading:** completed (`done`) add/drops, waiver claims and trades are graded by realized production since
  completion (acquired minus given up), in team-week standard-deviation units.
- **Calibration:** finished matchup predictions are graded by Brier score against a coin-flip baseline, with
  reliability buckets.
- **Projections:** engine vs ESPN forecasts graded on the following 14 days; the measured blend weight feeds back
  into every projection (see Projection calibration).
- **Signals:** hit rate by kind and confidence, and the ownership move after each signal (see Early signals).

## Draft assistant

Punt-aware G-score board with tiers, compared against ADP (ESPN by default, Yahoo draft analysis when refreshed).
Live recommendations are build-aware and account for who is likely available at your next pick; picks can be
synced live from Yahoo. Mock drafts (others by noisy ADP) are scored by season-level simulation.

## Constants registry

Every tunable constant is registered in `src/fbm/constants.py` with value, default, unit, kind (measured, research,
rule, heuristic) and source. `uv run fbm constants` lists them; `config/constants.toml` overrides are validated
before use.
