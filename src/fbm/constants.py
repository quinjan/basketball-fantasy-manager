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
        "sim.opponent_stream_adds",
        2,
        "adds/week",
        "Streaming adds assumed for an opponent when no manager activity profile exists.",
        "Judgment: typical active 9-cat manager; replace with Yahoo transaction history.",
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
        "G points",
        "Max overall ranking value a partner may lose for a suggested trade to count as realistic.",
        "Managers judge offers largely by rankings.",
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
        "Chance a team ahead of us claims a player who would improve its roster (no profile yet).",
        "Judgment; replace with Yahoo claim history.",
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
