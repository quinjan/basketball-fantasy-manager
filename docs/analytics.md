# Analytics methods

All numbers come from deterministic, tested Python in `src/fbm/analytics/`. The AI layer only reads these outputs.
Every tunable value lives in the constants registry (see the end of this page).

## Hashtag Basketball data layer

With `[hashtag] enabled = true`, Hashtag Basketball Premium (HT) owns player projections and their accuracy; FBM no
longer re-projects or calibrates projections. The daily import (`refresh_data` / `fbm hashtag import`) stores HT's
projection views — ROS, BLEND7/14/30, STREAM7/14/30 — plus the H2H Planner weekly totals (banked + projected rest of
week), the waiver wire and (on demand) Trade Finder ideas, both run with FBM's category multipliers, team ranks,
player notes and Next Man Up. Each decision uses the HT view its policy setting names (baseline: ROS for drops,
trades, IL, season weights and playoffs; BLEND14 for claims kept beyond the week; STREAM7 for weekly odds, lineups
and in-week streams). HT data is personal-use only and HT notes are untrusted data.

**Health gate (no fallback).** HT is healthy only when, within `hashtag.max_age_hours`, every projection view and the
H2H Planner imported OK (no later session-expired / layout-changed attempt), the league sync matches Yahoo,
unmatched projected players ≤ `hashtag.max_unmatched`, and a sign-in is stored. Otherwise every suggestion tool
returns `HT_UNHEALTHY` with the fix and no move proposals can be created; insights keep working.

**Category multipliers sent to HT** (`category_weights`): season multipliers = `policy.season_weights` when the owner
approved explicit ones, else derived from the approved strategy (targets ×1.25, others ×1, punts excluded). Week
multipliers map each category's chase/hold/lock/concede status (below) to HT's steps (concede ×0.25, lock/hold ×0.5,
chase ×1–1.5 by closeness); the two are multiplied and snapped to HT's steps (0.25, 0.5, 0.75, 1, 1.25, 1.5, 2, 2.5, 3).

With HT disabled (demo league, other installs) the ESPN-based engine below supplies projections.

## G-scores (player value)

FBM ranks players with **G-scores** (Rosenof, *"Improving Fantasy Basketball Rankings with G-scores"*,
[arXiv:2307.02188](https://arxiv.org/abs/2307.02188)) — a Z-score variant that accounts for week-to-week variance in
head-to-head play:

`G = (μ_player − μ_pool) / √(σ² + κτ²)`, where σ is the spread of player means in the pool, τ is the root-mean-square
week-to-week standard deviation, and κ = 2N/(2N−1).

- FG% and FT% use volume-weighted impact (a high percentage on low volume matters less).
- TO counts negatively. Z-scores are shown alongside for comparison.
- Rankings are punt-aware: punted categories are excluded from value.

## Projections (ESPN engine: demo league / Hashtag disabled)

- **Preseason baseline.** Every data sync snapshots ESPN's season projections (`espn_projections`, one snapshot per
  ET day). Each player's last snapshot before opening night is his preseason baseline (players ESPN first projects
  later use their first snapshot). Players ESPN doesn't project fall back to last season's per-game stats. This is
  the prior: rookies and players who missed last season get real values.
- **FBM engine.** Current-season per-game stats are shrunk toward that prior, weighted by games played
  (`gp / (gp + 15)`). Recent per-minute form is blended in. Counting stats are scaled by the recent-minutes trend
  (recent games vs season, damped and capped) so role changes show up early.
- **ESPN blend.** The final projection is `(1 − w) × engine + w × ESPN's latest projection`. `w` starts at
  `projection.espn_weight` (0.5) and is replaced by a measured weight once enough forecasts are graded (below);
  an override in `config/constants.toml` always wins. (Projection calibration of this blend is retired.)

## Early signals

Detected after every sync (scheduled `fbm data sync`, `refresh_data` in the daily run and the hourly lineup guard),
stored once per episode with the time FBM first caught it, and graded 14 days later. `direction` is where the
player's value should go relative to what the market (season stats, rankings) sees.

| Kind | Trigger | Why it's early |
|---|---|---|
| `buy_low` / `sell_high` | Season-to-date value vs FBM's rest-of-season value differs by ≥ 1.5 G/game (Hashtag mode: HT ROS value vs the market's view — HT season stats or box scores plus Yahoo % owned; feeds trade-scan) | Reason tags separate luck (FG%/FT%/3P% vs expectation, a key teammate out) from role (minutes, usage). Confidence is high when the gap is luck-driven, low when the role changed or the sample is small. |
| `role_change` | A bench player starts (or a starter is benched) at ≥ 24 min, or last-3-game minutes move ≥ 6 | Starts and minutes stabilise in a few games; the box score shows them days before averages move. |
| `opportunity` | A key player (≥ 24 projected min) is ruled out | Teammates' minutes with vs without him (this season's games first; last season's split is at most medium confidence), floor of 20 min without him. Hashtag mode: the numbers come from HT Next Man Up (imported for key players ruled out). Flagged the moment the injury is posted, before any game is played. |
| `ht_revision` (Hashtag mode; `espn_revision` otherwise) | HT's ROS/STREAM7 projection moved day over day (ESPN mode: ESPN's projection value moved ≥ 0.5 G/game in a week) | The projection changes before season stats and the market do; the HT note is quoted as untrusted data. |

Grading: buy/sell is a hit when production over the window was closer to our projection than to the season line;
other kinds when the value or minutes moved in the predicted direction. In the Yahoo league the change in % owned
after the signal shows whether the market followed (our lead). Backtest on 2025-26 (7 cutoffs): role changes at
medium/high confidence hit ~70%, high-confidence buy-low ~87% (small sample), sell-high ~55%, low-confidence
signals ~45–55% — so the feed shows medium and high confidence by default (`signals.feed_min_confidence`).
Hashtag mode grades signals by lead time (over HT's projection and over % owned), whether FBM acted (a decision or
proposal within `signals.acted_days`) and the replay grade of the resulting decisions; there is no projection grading.

## Availability

ESPN injury status and expected return dates scale rest-of-season value (rankings, free agents, waivers, trades,
punt scenarios) and season-level simulations; in matchup simulations each game is weighted by a play probability.
An Out player with an ESPN return date misses every game before that date; from it the daily lineup can start him
and each game counts at the game-time-decision probability (`sim.play_prob.gtd`), the same rule IL-activation
evaluations use. Out players without a return date miss the whole week.

## Official weekly odds (Hashtag mode)

HT's displayed "probability" is a share formula (counting stats: mine / (mine + opp); percentages: 0.5 + point
difference / 40, clipped 0.05–0.95), not a win probability: FBM shows it for reference only. The official per-category
odds use HT's projected weekly totals (view `policy.view.weekly_odds`) as the means and FBM's simulator for the spread:

`P(win cat) = Φ(sign × (me − opp) / (policy.odds_spread_scale × sd))`, sign = −1 for TO,

where sd is the simulator's per-category standard deviation of (me − opp) over the rest of the week (HT per-game means,
real game-to-game variation; banked stats add no spread). P(tie) ≈ 0; matchup-level numbers treat categories as
independent. Each daily forecast is stored (`source = ht_spread`, with means and sd) so `odds_reliability` can grade
it against Yahoo's FINAL result and recompute it for other spread scales and views.

## Matchup simulation

Monte Carlo over the rest of the fantasy week. It resamples players' real game lines (which keeps correlations
between stats), re-centred on the projection table's per-game means (`sim.projection_anchor`, so role/minutes changes
and the ESPN blend show up), and multiplies by play probability. Each player's games in a simulated week share one
role/form multiplier (`sim.form_sd`, measured from week-to-week variance), and players with a history of sitting a
leg of back-to-backs carry that rest risk on their team's back-to-back dates (`sim.b2b_rest_*`). In the Yahoo league
the stats already banked this week (live scoreboard) are added to the simulated remainder. Output includes
per-category win/tie/loss odds and an "if the opponent streams" upper bound (the streaming planner run from their
side).

## Decision rule (choosers) and decision log

Every suggestion (lineup, stream plan, waiver claim, drop, IL, trade, chase/concede, category weights) is a logged
decision: the situation (odds, punts, weekday, adds left, days and games left, banked stats, opponent, HT view and
as-of time, expected days out for IL), the options (one per HT view where cheap, plus stand pat and the top
alternatives, each with a replayable `spec`), and the recommendation picked by the shared rule
`fbm.gm.choosers.choose` under the current policy. The rule weights categories by this week's status: a category is
**concede** below `policy.concede_below`, **lock** above `policy.lock_above`, **chase** (full weight) within
`policy.contested_band` of 50%, otherwise **hold** at `policy.weight_floor`; punts weigh 0. Streams must clear
`policy.stream_min_gain` after `policy.ros_penalty` × ROS value given up, and keep `policy.add_reserve` adds until
Thursday; IL players expected back within `policy.il_hold_days` are held; `policy.pct_risk.*` optionally sits
high-volume poor shooters when FG%/FT% is contested. `optimize_lineup` / `lineup_steps` / `weekly_plan` carry a
`trigger` (daily-gm, lineup-guard, owner-ask).

## Replay grading

`replay_grade` starts from my real week totals (Yahoo final, incl. made/attempted), undoes what actually happened
(Yahoo daily lineups and moves), applies each option's player-days from real box scores (an added player counts only
on days he would have started), recomputes every category and compares with the opponent's real totals (the
opponent is held at what they did). Drops, adds, IL and trades also get a `ros_value` (real production gained minus
given up over `replay.ros_horizon_days`). Each decision's grade: every option's categories won, the recommended,
chosen and best option, and `regret_cats` = best − recommended. A grade is complete only once Yahoo marks the week
final and every game's box score is synced.

## Policy tuning

`policy_suggestions` re-runs `choosers.choose` over every graded decision with candidate values of one setting (all
others as now) and sums the replayed outcome of the option each would have picked; chase/concede thresholds are also
checked against the categories' final results and `policy.odds_spread_scale` by the odds' Brier score. A change is
suggested only with ≥ `policy.min_weeks` graded weeks and within the setting's max step. Nothing self-tunes: a
suggestion becomes a `policy` proposal → reviewer → owner approval → `proposal_steps` applies it (policy version +1,
history kept). The weekly retro's `per_view` table shows, per decision kind, what each HT view would have chosen.

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

- **Decisions:** every logged decision is replay-graded (see Replay grading); policy changes are proposed from the
  results (see Policy tuning).
- **Executed moves:** completed (`done`) add/drops, waiver claims and trades are also graded by realized production
  since completion (acquired minus given up), in team-week standard-deviation units (`grade_decisions`).
- **Odds:** finished weekly forecasts are graded against Yahoo FINAL results by Brier score against a coin-flip
  baseline, with reliability buckets (`odds_reliability`; `prediction_calibration` for legacy simulator forecasts).
- **Signals:** see Early signals.
- **Projections:** not calibrated by FBM (Hashtag owns projection accuracy).

## Constants registry

Every tunable constant is registered in `src/fbm/constants.py` with value, default, unit, kind (measured, research,
rule, heuristic) and source. `uv run fbm constants` lists them; `config/constants.toml` overrides are validated
before use. Decision rules are not constants: they are policy settings (`policy_settings`), changed only by an
owner-approved `policy` proposal.
