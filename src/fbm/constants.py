"""Constants registry: every tunable number in the engine, with its unit, meaning, and source.

Defaults live here; overrides live in config/constants.toml (owner/GM-tunable without code changes):

    "stream.min_gain" = 0.08

`kind` is one of: measured (computed from our data, with sample size), research (from a cited method),
rule (league/platform rule or convention), heuristic (judgment call; revisit with evidence).
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fbm.config import CONFIG_DIR

OVERRIDES_PATH = CONFIG_DIR / "constants.toml"


@dataclass(frozen=True)
class Constant:
    key: str
    default: Any
    unit: str
    description: str
    source: str
    kind: str


_ALL = [
    # projections
    Constant(
        "projection.shrink_games",
        15.0,
        "games",
        "Current-season weight = gp / (gp + this); the rest comes from last season.",
        "Shrinkage toward prior; half weight at 15 games.",
        "heuristic",
    ),
    Constant(
        "projection.recent_games",
        10,
        "games",
        "Window for the recent minutes trend.",
        "Common fantasy 'last 10' window.",
        "heuristic",
    ),
    Constant(
        "projection.trend_min_games",
        5,
        "games",
        "Minimum recent games before a minutes trend is applied.",
        "Avoid one-game noise.",
        "heuristic",
    ),
    Constant(
        "projection.trend_min_change",
        3.0,
        "minutes/game",
        "Recent minus season minutes needed to flag a role change.",
        "Judgment: ~10% of a starter's minutes.",
        "heuristic",
    ),
    Constant(
        "projection.trend_max_weight",
        0.6,
        "fraction",
        "Max weight on recent minutes when projecting minutes (reached at 2x recent_games).",
        "Recent role matters but regresses.",
        "heuristic",
    ),
    Constant(
        "projection.form_games",
        10,
        "games",
        "Window of last played games for the recent per-minute form blend.",
        "Two-plus weeks of games: recent enough to catch role/usage changes.",
        "heuristic",
    ),
    Constant(
        "projection.form_shrink_games",
        30.0,
        "games",
        "Recent-form weight = n / (n + this) before the cap; per-minute rates regress hard to the season blend.",
        "Per-minute rates stabilise slowly; 10 games -> 0.25 weight before the cap.",
        "heuristic",
    ),
    Constant(
        "projection.form_max_weight",
        0.25,
        "fraction",
        "Cap on the weight given to recent per-minute production.",
        "Conservative: recent hot/cold streaks mostly regress.",
        "heuristic",
    ),
    Constant(
        "projection.espn_weight",
        0.5,
        "fraction",
        "Weight on ESPN's current projection in the final blend (1 - this on FBM's engine). Used until "
        "projection_calibration has measured a weight; an override here always wins.",
        "Equal weight until graded forecasts say otherwise.",
        "heuristic",
    ),
    Constant(
        "projection.espn_snapshot_hours",
        6.0,
        "hours",
        "Minimum age of the cached ESPN projection download before it is fetched again (one snapshot per ET day).",
        "ESPN revises projections at most a few times a day; the payload is ~1 MB.",
        "heuristic",
    ),
    Constant(
        "projection.calibration_horizon_days",
        14,
        "days",
        "Forecasts logged on day D are graded against per-game production over the next this-many days.",
        "Two weeks: long enough for a stable average, short enough to reflect the forecast's information.",
        "heuristic",
    ),
    Constant(
        "projection.calibration_min_games",
        3,
        "games",
        "Minimum games a player must play in a grading window to count.",
        "Avoid grading on one or two games.",
        "heuristic",
    ),
    Constant(
        "projection.calibration_min_dates",
        10,
        "forecast dates",
        "Graded forecast dates needed before the measured ESPN weight replaces projection.espn_weight.",
        "About two weeks of daily logs before trusting the measurement.",
        "heuristic",
    ),
    Constant(
        "signals.min_games",
        5,
        "games",
        "Games played this season before a player can get a buy-low / sell-high signal.",
        "Below five games the season line is mostly noise.",
        "heuristic",
    ),
    Constant(
        "signals.buy_sell_min_gap",
        1.5,
        "G points / game",
        "Gap between season-to-date value and FBM's rest-of-season value that triggers buy-low / sell-high.",
        "Backtest on 2025-26 (7 cutoffs, 14-day grading): high-confidence buy-low hit 57% / 63% / 87% at gaps "
        "0.75 / 1.0 / 1.5; sell-high stayed near 50% except high confidence at 1.5 (63%, n=16).",
        "measured",
    ),
    Constant(
        "signals.relevance_rank_mult",
        1.25,
        "x league pool",
        "Only players ranked within pool size x this (season-to-date or rest of season) get buy/sell, ESPN revision "
        "and role-change-down signals.",
        "Ignore deep-bench noise; keep the fringe of rosterable players.",
        "heuristic",
    ),
    Constant(
        "signals.gem_rank_mult",
        2.0,
        "x league pool",
        "Role-change-up signals (new starter, minutes spike) only for players ranked within pool size x this by "
        "rest-of-season value: wider than relevance_rank_mult because gems start outside the rostered pool.",
        "Backtest: without a filter 60-100 role changes per day, mostly deep bench.",
        "heuristic",
    ),
    Constant(
        "signals.feed_min_confidence",
        "medium",
        "high | medium | low",
        "Lowest confidence shown in the signal feed and context_pack (all signals are still stored and graded).",
        "Backtest on 2025-26: low-confidence signals hit ~45-55% (no better than the naive view).",
        "measured",
    ),
    Constant(
        "signals.fg_pct_change",
        0.03,
        "FG% points",
        "FG% above/below expectation that counts as hot/cold shooting (with >= 3 FGA per game).",
        "FG% takes a long time to stabilise; 3 points is a large in-season swing.",
        "heuristic",
    ),
    Constant(
        "signals.ft_pct_change",
        0.06,
        "FT% points",
        "FT% above/below expectation that counts as hot/cold (with >= 1.5 FTA per game).",
        "FT% is noisy on small volume.",
        "heuristic",
    ),
    Constant(
        "signals.tp_pct_change",
        0.05,
        "3P% points",
        "3P% above/below expectation that counts as hot/cold (with >= 2 3PA per game).",
        "3P% is among the slowest stats to stabilise.",
        "heuristic",
    ),
    Constant(
        "signals.usage_change",
        0.15,
        "fraction",
        "Relative change in shot attempts per minute (FGA + 0.44 FTA) that counts as a usage change.",
        "Usage stabilises within a few games, so it is an early signal.",
        "heuristic",
    ),
    Constant(
        "signals.small_sample_games",
        10,
        "games",
        "Signals built on fewer games than this are tagged small_sample (lower confidence).",
        "Judgment: two to three weeks of games.",
        "heuristic",
    ),
    Constant(
        "signals.key_player_minutes",
        24.0,
        "minutes/game",
        "Projected minutes for an injured player to open a next-man-up opportunity (and a teammate_out reason).",
        "Starter-level minutes leave real playing time to redistribute.",
        "heuristic",
    ),
    Constant(
        "signals.opportunity_min_delta",
        3.0,
        "minutes/game",
        "Minutes a teammate historically gains when the injured player sits, to count as a beneficiary.",
        "Matches projection.trend_min_change.",
        "heuristic",
    ),
    Constant(
        "signals.opportunity_min_minutes",
        20.0,
        "minutes/game",
        "Minutes a beneficiary must average without the injured player to count (fantasy relevance).",
        "Below ~20 minutes few players return standard-league value.",
        "heuristic",
    ),
    Constant(
        "signals.opportunity_min_without",
        3,
        "games",
        "Games without the injured player needed to trust the with/without minutes split.",
        "Fewer games fall back to a positional estimate (lower confidence).",
        "heuristic",
    ),
    Constant(
        "signals.role_lookback_games",
        10,
        "games",
        "Games before the latest one used to judge a player's usual role (starter rate, minutes).",
        "Same window as the minutes trend.",
        "heuristic",
    ),
    Constant(
        "signals.role_starter_rate",
        0.3,
        "fraction",
        "A start counts as new when the player started at most this share of the lookback games (and the reverse "
        "for a lost starting job).",
        "A bench player who starts is the earliest role-change evidence in a box score.",
        "heuristic",
    ),
    Constant(
        "signals.role_min_minutes",
        24.0,
        "minutes",
        "Minutes the latest start (or minutes spike) must reach to be a role-change signal.",
        "Filters spot starts with normal bench minutes.",
        "heuristic",
    ),
    Constant(
        "signals.minutes_spike",
        6.0,
        "minutes/game",
        "Last-3-game minutes above the lookback average that count as a minutes spike (or drop).",
        "Twice projection.trend_min_change: fast but not noisy.",
        "heuristic",
    ),
    Constant(
        "signals.espn_revision_days",
        7,
        "days",
        "Compare ESPN's latest projection with its projection this many days earlier.",
        "Weekly revisions; daily ones are noise.",
        "heuristic",
    ),
    Constant(
        "signals.espn_revision_min",
        0.5,
        "G points / game",
        "Change in value of ESPN's projection that counts as a revision signal.",
        "Half the buy/sell gap: revisions are early, smaller moves.",
        "heuristic",
    ),
    Constant(
        "signals.dedup_days",
        7,
        "days",
        "A signal seen again within this many days updates the existing one instead of creating a new one.",
        "One signal per episode.",
        "heuristic",
    ),
    Constant(
        "signals.active_hours",
        48,
        "hours",
        "A signal is active while it was re-detected within this many hours.",
        "Spans a missed daily run.",
        "heuristic",
    ),
    Constant(
        "signals.new_hours",
        36,
        "hours",
        "Signals first seen within this many hours are reported as new.",
        "Covers the gap between daily runs.",
        "heuristic",
    ),
    Constant(
        "signals.grade_horizon_days",
        14,
        "days",
        "Signals are graded on production over this many days after they were first seen.",
        "Matches projection.calibration_horizon_days.",
        "heuristic",
    ),
    Constant(
        "signals.grade_min_games",
        3,
        "games",
        "Minimum games played in the grading window; otherwise the signal is graded no_games.",
        "Same as projection.calibration_min_games.",
        "heuristic",
    ),
    Constant(
        "signals.grade_min_minutes",
        2.0,
        "minutes/game",
        "A role/opportunity signal is a hit when minutes moved this much in the predicted direction.",
        "Smaller than the trigger: the signal claims a direction, not the full size.",
        "heuristic",
    ),
    Constant(
        "availability.out_default_games",
        8.0,
        "games",
        "Expected games missed for an OUT/INJ player without a return date.",
        "Typical multi-week injury absence.",
        "heuristic",
    ),
    Constant(
        "availability.susp_default_games",
        3.0,
        "games",
        "Expected games missed for a suspension without a return date.",
        "Most NBA suspensions are a few games.",
        "heuristic",
    ),
    Constant(
        "availability.doubtful_default_games",
        2.0,
        "games",
        "Expected games missed for a DOUBTFUL player without a future return date.",
        "Short-term absence.",
        "heuristic",
    ),
    Constant(
        "availability.dtd_default_games",
        1.0,
        "games",
        "Expected games missed for a DTD/GTD/questionable player without a future return date.",
        "Usually misses at most a game or two.",
        "heuristic",
    ),
    # schedule / simulation
    Constant(
        "week.avg_games_per_week",
        3.29,
        "games/team/week",
        "Average NBA games per team in a full fantasy week (schedule-neutral week).",
        "Measured: 2025-26 regular season, 690 full team-weeks.",
        "measured",
    ),
    Constant(
        "sim.play_prob.gtd",
        0.6,
        "probability",
        "Chance a game-time-decision player plays a given game.",
        "Judgment until injury history is collected.",
        "heuristic",
    ),
    Constant(
        "sim.play_prob.dtd",
        0.8,
        "probability",
        "Chance a day-to-day player plays a given game.",
        "Judgment until injury history is collected.",
        "heuristic",
    ),
    Constant(
        "sim.play_prob.questionable",
        0.5,
        "probability",
        "Chance a questionable player plays.",
        "NBA convention: questionable ~ 50%.",
        "heuristic",
    ),
    Constant(
        "sim.play_prob.doubtful",
        0.2,
        "probability",
        "Chance a doubtful player plays.",
        "NBA convention: doubtful ~ 25%.",
        "heuristic",
    ),
    Constant(
        "sim.projection_anchor",
        1.0,
        "weight",
        "Re-centre each player's resampled real game lines on the projection table's per-game means (0 = raw recent "
        "games, 1 = projection means). Keeps game-to-game shape and stat correlations; picks up role/minutes "
        "changes, ESPN blend and recent form that raw last-season lines miss.",
        "Measured week 1 2026-27 (230 players): raw last-40-game pool vs actual minutes bias -2.0/g; our team's "
        "planned starts ran 927 sim minutes vs 796 actual. Rankings already use the projection table.",
        "measured",
    ),
    Constant(
        "sim.anchor_scale_min",
        0.5,
        "ratio",
        "Lower bound on the per-stat rescale factor applied by sim.projection_anchor.",
        "Guard rail: a projection far below the player's real games usually means missing data, not a role change.",
        "heuristic",
    ),
    Constant(
        "sim.anchor_scale_max",
        2.0,
        "ratio",
        "Upper bound on the per-stat rescale factor applied by sim.projection_anchor.",
        "Guard rail against tiny-sample projections inflating a bench player's lines.",
        "heuristic",
    ),
    Constant(
        "sim.form_sd",
        0.09,
        "multiplier sd",
        "Weekly role/form shock: every simulated week each player's counting stats (and attempts, so percentages "
        "hold) are multiplied by one shared factor with this standard deviation. Resampling games independently "
        "otherwise makes weekly totals too certain.",
        "Measured 2025-26 box scores, 216 regulars (>=40 g, >=20 mpg): excess variance of weekly means over iid "
        "game resampling gives multiplier sd 0.064 MIN, 0.091 PTS, 0.087 REB, 0.092 STL, 0.084 FGA (median). "
        "FG% showed no excess (0.000), so no separate shooting shock.",
        "measured",
    ),
    Constant(
        "sim.b2b_rest_baseline",
        0.15,
        "share of back-to-backs",
        "Share of back-to-backs where a typical regular plays exactly one leg for non-rest reasons (injury "
        "boundaries etc.). Only a player's rate above this counts as rest.",
        "Measured 2025-26: median regular (>=28 mpg, >=8 back-to-backs, n=103) 0.154; mean 0.20.",
        "measured",
    ),
    Constant(
        "sim.b2b_rest_prior_pairs",
        8,
        "back-to-backs",
        "Shrinkage: a player's back-to-back one-leg rate is pulled toward the league rate with this many pseudo "
        "back-to-backs.",
        "Typical team plays ~14 back-to-backs a season; 8 pseudo-pairs = half-season of evidence to move off prior.",
        "heuristic",
    ),
    Constant(
        "sim.opponent_stream_adds",
        2,
        "adds/week",
        "Streaming adds assumed for an opponent when the league has no transaction history (prior anchor).",
        "Judgment: typical active 9-cat manager; with history the manager model's adds/week replaces it.",
        "heuristic",
    ),
    # streaming
    Constant(
        "stream.min_gain",
        0.05,
        "expected categories",
        "Minimum gain in expected categories won for a streaming move to be worth an add.",
        "Judgment; above Monte Carlo noise with common random numbers.",
        "heuristic",
    ),
    Constant(
        "stream.ros_penalty",
        0.5,
        "exp. cats per G point",
        "Penalty per point of rest-of-season G value given up by a streaming drop.",
        "Set after a demo plan dropped a core player for a 1-game stream.",
        "heuristic",
    ),
    Constant(
        "stream.drop_pool",
        4,
        "players",
        "How many of my lowest-value players are considered droppable.",
        "Protects the core roster.",
        "heuristic",
    ),
    Constant(
        "stream.candidates",
        25,
        "players",
        "Free agents considered by the streaming planner.",
        "Speed/coverage trade-off.",
        "heuristic",
    ),
    # trades
    Constant(
        "trade.max_their_value_loss",
        1.0,
        "G points above replacement",
        "Max overall ranking value (above replacement level) a partner may lose for a suggested trade to count as "
        "realistic.",
        "Managers judge offers largely by rankings; a player they would cut anyway is worth ~0 to them.",
        "heuristic",
    ),
    Constant(
        "trade.expendable_pool",
        3,
        "players",
        "Partner players considered as expendable add-ins (lowest value to their own build), so a 2-for-1 can be "
        "offered as a roster-neutral 2-for-2.",
        "The receiving side of a 2-for-1 must cut someone; asking for that player keeps their roster count even.",
        "heuristic",
    ),
    Constant(
        "trade.need_weight_floor",
        0.1,
        "weight",
        "Minimum category weight in need-weighted trade screening.",
        "Keep locked/lost categories slightly relevant.",
        "heuristic",
    ),
    Constant(
        "trade.need_weight_slope",
        1.6,
        "weight per win-rate",
        "Weight falls by this per unit |win rate - 0.5|.",
        "Swing categories (near 50%) matter most in H2H each-category.",
        "heuristic",
    ),
    Constant(
        "trade.playoff_weight_max",
        0.5,
        "fraction",
        "Max weight of fantasy-playoff schedule in trade value (reached at the trade deadline).",
        "Playoff weeks decide titles; regular season still matters.",
        "heuristic",
    ),
    # learning loop
    Constant(
        "outcomes.team_starts_per_week",
        35,
        "player-games",
        "Team-week scale: started player-games per week (10 slots x ~3.5 games).",
        "Roster math.",
        "rule",
    ),
    Constant(
        "outcomes.good_threshold",
        0.5,
        "team-week SD",
        "Decision grade threshold: >= +this is good, <= -this is bad.",
        "Judgment.",
        "heuristic",
    ),
    Constant(
        "outcomes.too_early_games",
        3,
        "games",
        "Fewer combined games than this (and < 4 days) = too early.",
        "Avoid grading on noise.",
        "heuristic",
    ),
    # draft
    Constant(
        "draft.build_weight",
        0.5,
        "weight",
        "Extra weight on categories where my picks trail the league.",
        "Judgment; keeps value first, build second.",
        "heuristic",
    ),
    Constant(
        "draft.tier_gap",
        0.35,
        "G points",
        "Value drop between consecutive players that starts a new tier.",
        "Judgment.",
        "heuristic",
    ),
    Constant(
        "draft.yahoo_adp_limit",
        300,
        "players",
        "How many players to fetch Yahoo draft analysis (ADP) for, in pages of 25.",
        "Covers every draftable player in a 12-15 team, 13-15 round league with margin.",
        "heuristic",
    ),
    Constant(
        "draft.yahoo_adp_min_players",
        50,
        "players",
        "Minimum mapped Yahoo ADP rows before the board uses Yahoo ADP instead of ESPN ADP.",
        "Guards against a partial/empty Yahoo fetch replacing a complete ESPN ADP list.",
        "heuristic",
    ),
    Constant(
        "draft.live_interval_sec",
        20,
        "seconds",
        "Default polling interval for `fbm draft live` (Yahoo draft results).",
        "Typical draft pick clocks are 60-90 s; 20 s keeps advice timely without hammering the API.",
        "heuristic",
    ),
    # waivers
    Constant(
        "waiver.threshold_top",
        1.0,
        "G points",
        "ROS value gain required to spend the #1 waiver priority on a claim.",
        "Top priority is worth a real upgrade (injury returns, breakouts).",
        "heuristic",
    ),
    Constant(
        "waiver.threshold_bottom",
        0.1,
        "G points",
        "ROS value gain required when holding the last waiver priority (little to lose).",
        "Judgment.",
        "heuristic",
    ),
    Constant(
        "waiver.manager_activity",
        0.5,
        "probability",
        "Chance a team ahead of us claims a player who would improve its roster (league has no claim history).",
        "Judgment; with history, per-manager empirical-Bayes claim participation replaces it (weak prior anchor).",
        "heuristic",
    ),
    Constant(
        "waiver.claim_gain_half",
        1.5,
        "G points",
        "Need-weighted gain at which a team is half as likely to claim a specific player as its overall claim rate.",
        "Judgment: interest = gain / (gain + this); marginal upgrades rarely get claimed.",
        "heuristic",
    ),
    Constant(
        "waiver.wait_max_p_claimed",
        0.3,
        "probability",
        "Below the claim bar, wait for free agency only if P(another team claims him) is under this.",
        "Judgment: accept a ~30% risk of losing a marginal upgrade to keep priority.",
        "heuristic",
    ),
    # opponent intel (manager profiles)
    Constant(
        "intel.sync_max_pages", 20, "pages", "Max transaction pages read per Yahoo sync.", "API budget.", "heuristic"
    ),
    Constant("intel.sync_page_size", 25, "transactions", "Transactions per Yahoo page.", "API default.", "rule"),
    Constant(
        "intel.recent_days",
        14,
        "days",
        "Window for a manager's recent adds/week.",
        "Judgment: two weeks tracks current habits.",
        "heuristic",
    ),
    Constant(
        "intel.stream_days",
        7,
        "days",
        "An add dropped again within this many days counts as a stream.",
        "Judgment: one fantasy week.",
        "heuristic",
    ),
    Constant(
        "intel.inactive_days",
        14,
        "days",
        "No move for this long -> inactive manager.",
        "Judgment: two missed weeks.",
        "heuristic",
    ),
    Constant("intel.tier_active", 1.5, "adds/week", "Recent adds/week for 'active'.", "Judgment.", "heuristic"),
    Constant(
        "intel.tier_very_active", 3.0, "adds/week", "Recent adds/week for 'very active'.", "Judgment.", "heuristic"
    ),
    Constant(
        "intel.min_moves",
        5,
        "moves",
        "Moves needed before a profile replaces engine defaults (sim streaming, waiver activity).",
        "Judgment: small samples are noise.",
        "heuristic",
    ),
    Constant(
        "intel.activity_floor",
        0.05,
        "probability",
        "Clamp for profile waiver-claim activity (floor; ceiling = 1 - floor).",
        "Judgment: nobody is certain to (not) claim.",
        "heuristic",
    ),
    Constant("intel.strong_cat", 0.6, "win rate", "Category win rate counted as a strength.", "Judgment.", "heuristic"),
    Constant("intel.weak_cat", 0.4, "win rate", "Category win rate counted as a weakness.", "Judgment.", "heuristic"),
    Constant("intel.punt_cat", 0.25, "win rate", "Category win rate read as a likely punt.", "Judgment.", "heuristic"),
    Constant(
        "intel.half_life_days",
        28,
        "days",
        "Recency half-life for manager behavior evidence (an observation this old counts half).",
        "Judgment: ~4 weeks tracks changing habits over a 22-week season without discarding history.",
        "heuristic",
    ),
    Constant(
        "intel.prior_weeks",
        2.0,
        "weeks",
        "Strength of the league prior for per-week rates (adds, claims, drops, trades), in weeks of evidence.",
        "Judgment: a manager's own two weeks outweigh the league average after ~2 weeks.",
        "heuristic",
    ),
    Constant(
        "intel.prior_trials",
        3.0,
        "trials",
        "Strength of the league prior for probabilities (claim participation, stream fraction, near-limit weeks).",
        "Judgment: shrinks small samples hard, lets a month of data speak.",
        "heuristic",
    ),
    Constant(
        "intel.league_prior_weight",
        7.0,
        "team-weeks",
        "Weight of the engine's default constants inside the league-wide prior (anchor against a tiny early sample).",
        "Judgment: half a week of a 14-team league's evidence.",
        "heuristic",
    ),
    Constant(
        "intel.preseason_weight",
        0.3,
        "weight",
        "Weight of activity (and days) before the league's start date: draft-day-after churn is not a streaming habit.",
        "Judgment: still evidence of engagement, but not week-to-week behavior.",
        "heuristic",
    ),
    Constant(
        "intel.near_limit_margin",
        1,
        "adds",
        "A week with adds within this many of the weekly add limit counts as a streaming week.",
        "Judgment: using all-but-one add is streaming behavior.",
        "heuristic",
    ),
    Constant(
        "intel.prior_stream_fraction",
        0.25,
        "share of adds",
        "Share of adds dropped again within a week, assumed when the league has no transaction history.",
        "Judgment.",
        "heuristic",
    ),
    Constant(
        "intel.prior_near_limit",
        0.1,
        "share of weeks",
        "Share of weeks near the weekly add limit, assumed when the league has no transaction history.",
        "Judgment.",
        "heuristic",
    ),
    # health heartbeat
    Constant(
        "health.data_max_age_h_gameday",
        12,
        "hours",
        "Max age of an ESPN sync job when NBA games were played in the last ET day (FAIL at 3x).",
        "Box scores/injuries change nightly; a twice-daily GM cadence keeps this under 12h.",
        "heuristic",
    ),
    Constant(
        "health.data_max_age_h",
        24,
        "hours",
        "Max age of an ESPN sync job during the regular season on days without games (FAIL at 3x).",
        "One daily GM run refreshes data.",
        "heuristic",
    ),
    Constant(
        "health.data_max_age_h_offseason",
        168,
        "hours",
        "Max age of an ESPN sync job in the preseason/offseason (FAIL at 3x).",
        "Little changes outside the season; weekly is enough.",
        "heuristic",
    ),
    Constant(
        "health.manual_stale_h",
        12,
        "hours",
        "Warn when a proposal has been awaiting manual execution longer than this.",
        "Matches the health skill's manual-review guidance.",
        "heuristic",
    ),
    Constant(
        "health.approved_stale_h",
        2,
        "hours",
        "Warn when an approved proposal has not been executed after this long.",
        "Execution normally follows approval within the same session.",
        "heuristic",
    ),
    Constant(
        "health.journal_lookback_days",
        3,
        "days",
        "Days (before today) checked for missing daily-GM and game-day lineup journal entries in the season.",
        "Catches a sleeping PC / closed app within a few days without stale noise.",
        "heuristic",
    ),
    Constant(
        "health.journal_fail_days",
        2,
        "days",
        "Missing daily-GM days in the lookback window that turn the scheduled-runs check from WARN into FAIL.",
        "One missed day can be a blip; two is a broken schedule.",
        "heuristic",
    ),
    Constant(
        "health.outbox_lookback_h",
        48,
        "hours",
        "Window for reporting failed outbox notifications.",
        "Covers the last two daily runs.",
        "heuristic",
    ),
    Constant(
        "health.db_max_mb",
        500,
        "MB",
        "Warn when data/fbm.db grows beyond this size.",
        "A full season of box scores is well under this; growth past it suggests a runaway table.",
        "heuristic",
    ),
    # playoffs
    Constant(
        "playoffs.weeks",
        3,
        "weeks",
        "Fantasy playoff length when Yahoo settings are unavailable.",
        "Yahoo default for 12-team leagues with 6 playoff teams.",
        "rule",
    ),
    # lineup / IL
    Constant(
        "lineup.il_statuses",
        "O,OUT,INJ,OFS",
        "statuses",
        "Injury statuses treated as IL/IL+ eligible in the demo league (Yahoo provides eligibility directly).",
        "Yahoo IL rules approximated; verify when connected.",
        "rule",
    ),
    Constant(
        "lineup.swing_sims",
        2000,
        "simulations",
        "Matchup simulations run to weight categories for the daily lineup tie-break.",
        "Only a tie-break among equal games-started lineups; 2k sims gives ~±1pp category odds, cheap enough per run.",
        "heuristic",
    ),
    Constant(
        "lineup.swing_band",
        0.25,
        "probability",
        "Distance of P(win category) from 50% at which a category's lineup weight falls to the floor.",
        "Matches MatchupOdds.swing default (25-75% = contested).",
        "heuristic",
    ),
    Constant(
        "lineup.swing_floor",
        0.2,
        "weight",
        "Lineup tie-break weight for categories that are effectively locked or lost this week (punts get 0).",
        "Keeps some ROS value for decided categories so ties among them still resolve sensibly.",
        "heuristic",
    ),
    # punt scenarios
    Constant(
        "yahoo.snapshot_max_age_min",
        60,
        "minutes",
        "How long a Yahoo league snapshot is served before it is re-read (rosters, scoreboard, waivers).",
        "Hourly matches the lineup-guard cadence; `fbm snapshot` forces a refresh.",
        "heuristic",
    ),
    Constant(
        "yahoo.ownership_limit",
        100,
        "players",
        "How many available players (by Yahoo actual rank) are read for percent-owned trends in each snapshot.",
        "Covers every realistic pickup in a 12-team league in 4 paged reads; more costs API calls for no signal.",
        "heuristic",
    ),
    Constant(
        "yahoo.session_min_interval_s",
        0.4,
        "seconds",
        "Minimum gap between Yahoo reads when FBM uses your Yahoo web session (`fbm yahoo login`).",
        "Keeps a full snapshot (~25 reads) at a person-like pace: about 10 seconds, far below any rate limit.",
        "heuristic",
    ),
    # ownership history & pickup radar
    Constant(
        "ownership.delta_days",
        1,
        "days",
        "Short window for the percent-owned change shown on player rows (pct_owned_delta), from FBM's own history.",
        "Yahoo's delta is weekly and often 0.0; a daily window shows what the league is grabbing right now.",
        "heuristic",
    ),
    Constant(
        "ownership.trend_days",
        3,
        "days",
        "Longer window for pct_owned_delta_trend on player rows and the ownership-surge test in the pickup radar.",
        "Three days smooths a one-day rush while still catching a role change early.",
        "heuristic",
    ),
    Constant(
        "ownership.min_span_hours",
        6.0,
        "hours",
        "History needed before a percent-owned change is reported (shorter spans fall back to Yahoo's weekly delta).",
        "Snapshots are hourly at best; under 6 hours is noise, not movement.",
        "heuristic",
    ),
    Constant(
        "ownership.record_gap_hours",
        6.0,
        "hours",
        "An unchanged percent owned is re-recorded at most this often (changes are always recorded).",
        "Keeps the history table small while the trend still has a recent anchor point.",
        "heuristic",
    ),
    Constant(
        "ownership.retention_days",
        30,
        "days",
        "Percent-owned history older than this is pruned.",
        "Longer than any trend window we use; bounds the table.",
        "heuristic",
    ),
    Constant(
        "ownership.min_move",
        1.0,
        "percent points",
        "Smallest percent-owned change listed as a riser/faller by ownership_trends.",
        "Below a point is rounding noise in Yahoo's integer-ish percentages.",
        "heuristic",
    ),
    Constant(
        "ownership.moves_days",
        3,
        "days",
        "Lookback for in-league adds and drops (from the stored transaction log) in ownership_trends.",
        "Matches the trend window: who in this league reacted to the same movement.",
        "heuristic",
    ),
    Constant(
        "radar.points_high",
        3.0,
        "points",
        "Radar points for the strongest up signal (opportunity, role_change, buy_low, espn_revision) at high "
        "confidence.",
        "Judgment: a high-confidence early signal is the radar's main trigger.",
        "heuristic",
    ),
    Constant(
        "radar.points_medium",
        2.0,
        "points",
        "Radar points for the strongest up signal at medium confidence.",
        "Judgment.",
        "heuristic",
    ),
    Constant(
        "radar.points_low",
        1.0,
        "points",
        "Radar points for the strongest up signal at low confidence (low-confidence signals backtested no better than "
        "the naive view).",
        "Judgment.",
        "heuristic",
    ),
    Constant(
        "radar.points_extra_signal",
        0.5,
        "points",
        "Radar points for each additional up signal of a different kind on the same player.",
        "Independent corroboration counts, but less than the first.",
        "heuristic",
    ),
    Constant(
        "radar.points_minutes",
        1.5,
        "points",
        "Radar points when recent minutes are well above his season minutes.",
        "Minutes are the fastest real evidence of a role change.",
        "heuristic",
    ),
    Constant(
        "radar.points_ownership",
        1.5,
        "points",
        "Radar points for an ownership surge (the league and Yahoo at large are grabbing him).",
        "The market acting is evidence, though it lags FBM's own signals.",
        "heuristic",
    ),
    Constant(
        "radar.points_value",
        2.0,
        "points",
        "Radar points when his rest-of-season value beats my worst rostered player by radar.min_ros_gain.",
        "A pickup only matters if it improves my roster.",
        "heuristic",
    ),
    Constant(
        "radar.min_minutes_trend",
        3.0,
        "minutes/game",
        "Recent minus season minutes needed for the radar's minutes reason.",
        "Same bar as projection.trend_min_change (a flagged role change).",
        "heuristic",
    ),
    Constant(
        "radar.min_recent_mpg",
        15.0,
        "minutes/game",
        "Recent minutes a player needs for the minutes reason to count (ignores end-of-bench noise).",
        "Below ~15 minutes a jump rarely produces fantasy-relevant stats.",
        "heuristic",
    ),
    Constant(
        "radar.min_owned_surge",
        3.0,
        "percent points",
        "Percent-owned gain over ownership.trend_days (or Yahoo's weekly delta when history is thin) that counts as a "
        "surge.",
        "A few points on a low-owned player is the first sign of a pickup rush.",
        "heuristic",
    ),
    Constant(
        "radar.min_ros_gain",
        0.5,
        "G points",
        "Rest-of-season G-score gain over my worst active rostered player that counts as a value reason.",
        "Filters near-ties where a swap costs an add for nothing.",
        "heuristic",
    ),
    Constant(
        "radar.high_score",
        5.0,
        "points",
        "Radar score for high confidence.",
        "Roughly a strong signal plus corroboration (minutes, ownership or value).",
        "heuristic",
    ),
    Constant(
        "radar.medium_score",
        3.0,
        "points",
        "Radar score for medium confidence.",
        "A medium signal plus one corroborating reason, or a high signal alone.",
        "heuristic",
    ),
    Constant(
        "punt.fa_moves",
        3,
        "moves",
        "Free-agent swaps each punt scenario may make before it is scored (same budget for no punt).",
        "About one week of adds: what a build changes quickly, without assuming trades.",
        "heuristic",
    ),
    Constant(
        "punt.min_swap_gain",
        0.25,
        "G points",
        "Lens-value gain a scenario's free-agent swap must clear to be considered.",
        "Filters noise swaps between near-equal players.",
        "heuristic",
    ),
    Constant(
        "punt.min_sim_gain",
        0.02,
        "categories/week",
        "Simulated improvement a proposed swap must deliver to be kept (divided by the number of categories when "
        "ranking by P(win the week)).",
        "Noise floor under common random numbers; the lens proposes, the simulation confirms.",
        "heuristic",
    ),
    Constant(
        "punt.pair_pool",
        4,
        "categories",
        "Two-category punts are tried among this many of my weakest categories.",
        "Keeps the search small; pairs of strong categories are not realistic punts.",
        "heuristic",
    ),
    Constant(
        "punt.reliable_win_rate",
        0.6,
        "probability",
        "A category won at least this often vs the league counts as reliable.",
        "Five reliable categories is the 9-cat H2H target.",
        "heuristic",
    ),
    Constant(
        "punt.switch_margin",
        0.15,
        "categories/week",
        "Expected-category edge a build needs over the approved strategy before recommending a switch "
        "(divided by the number of categories when ranking by P(win the week)).",
        "Avoids churning strategy over simulation noise; switching costs moves.",
        "heuristic",
    ),
]

REGISTRY: dict[str, Constant] = {c.key: c for c in _ALL}
_cache: dict[str, Any] = {"mtime": None, "values": {}}


def _flatten(d: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(_flatten(v, key + "."))
        else:
            out[key] = v
    return out


def overrides(path: Path | None = None) -> dict[str, Any]:
    path = path or OVERRIDES_PATH
    if not path.exists():
        return {}
    stamp = (str(path), path.stat().st_mtime)
    if _cache["mtime"] != stamp:
        _cache["values"] = _flatten(tomllib.loads(path.read_text(encoding="utf-8")))
        _cache["mtime"] = stamp
    return _cache["values"]


def C(key: str) -> Any:
    """Current value of a registered constant (override if set, else default)."""
    if key not in REGISTRY:
        raise KeyError(f"Unknown constant '{key}'")
    over = overrides()
    if key in over:
        default = REGISTRY[key].default
        return type(default)(over[key]) if isinstance(default, (int, float, str)) else over[key]
    return REGISTRY[key].default


def unknown_overrides() -> list[str]:
    return sorted(k for k in overrides() if k not in REGISTRY)


def table() -> list[dict[str, Any]]:
    over = overrides()
    return [
        {
            "key": c.key,
            "value": C(c.key),
            "default": c.default,
            "overridden": c.key in over,
            "unit": c.unit,
            "kind": c.kind,
            "description": c.description,
            "source": c.source,
        }
        for c in _ALL
    ]
