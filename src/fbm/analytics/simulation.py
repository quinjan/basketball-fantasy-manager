"""Monte Carlo category simulation for H2H weeks.

Each player-game is drawn by resampling the player's real recent box-score lines (keeps stat correlations such as
makes-with-attempts), re-centred on the projection table's per-game means, times a Bernoulli "plays" draw from injury
status (and back-to-back rest risk). An Out player with an ESPN return date misses every game before it and plays
from it at the game-time-decision probability. Each player's games in a simulated week share one role/form
multiplier. Team weekly totals are simulated once per team, so any pair of teams can be compared cheaply (matchups,
league-wide category profiles).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from fbm.analytics.categories import Category
from fbm.constants import C

STATS: tuple[str, ...] = ("pts", "reb", "ast", "stl", "blk", "tov", "tpm", "fgm", "fga", "ftm", "fta")
COL = {s: i for i, s in enumerate(STATS)}

PLAY_PROB_FIXED = {
    "": 1.0,
    "ACTIVE": 1.0,
    "O": 0.0,
    "OUT": 0.0,
    "OFS": 0.0,
    "INJ": 0.0,
    "SUSP": 0.0,
    "NA": 0.0,
}
PLAY_PROB_TUNABLE = {
    "DTD": "sim.play_prob.dtd",
    "DAY-TO-DAY": "sim.play_prob.dtd",
    "GTD": "sim.play_prob.gtd",
    "QUESTIONABLE": "sim.play_prob.questionable",
    "DOUBTFUL": "sim.play_prob.doubtful",
}


def play_probability(status: str | None) -> float:
    key = (status or "").strip().upper()
    if key in PLAY_PROB_TUNABLE:
        return float(C(PLAY_PROB_TUNABLE[key]))
    return PLAY_PROB_FIXED.get(key, 1.0)


@dataclass
class PlayerModel:
    player_id: str
    samples: np.ndarray  # (k, len(STATS)) per-game lines
    play_prob: float = 1.0
    synthetic: bool = False
    form_sd: float = 0.0  # sd of the weekly multiplier shared by all of this player's games in a simulated week
    rest_prob: float = 0.0  # extra chance of sitting a back-to-back leg (load management)
    b2b_dates: frozenset = frozenset()  # ET dates that are one leg of a back-to-back for the player's NBA team
    return_date: date | None = None  # Out player's ESPN return date: from this ET day he plays at return_play_prob
    return_play_prob: float = 0.0

    @property
    def mean(self) -> np.ndarray:
        return self.samples.mean(axis=0)

    def game_play_prob(self, day: object = None) -> float:
        """Play probability for one game: from the return date on, an Out player's return probability; back-to-back
        legs carry the player's rest risk."""
        p = self.play_prob
        if self.return_date is not None and isinstance(day, date) and day >= self.return_date:
            p = self.return_play_prob
        if self.rest_prob > 0 and day is not None and day in self.b2b_dates:
            return p * (1.0 - self.rest_prob)
        return p


def form_multiplier(rng: np.random.Generator, n: int, sd: float) -> np.ndarray:
    """Mean-one lognormal weekly multiplier with (approximately) standard deviation `sd`."""
    if sd <= 0:
        return np.ones(n)
    s = float(np.sqrt(np.log1p(sd**2)))
    return np.exp(rng.normal(-0.5 * s**2, s, n))


def anchor_samples(samples: np.ndarray, target: pd.Series, weight: float, lo: float, hi: float) -> np.ndarray:
    """Rescale each stat column so its mean moves toward the projection's per-game mean (`weight` 1 = all the way).

    Column-wise scaling keeps each game's shape (and makes/attempts pairing); makes and attempts are scaled
    separately so shooting percentages follow the projection too."""
    if weight <= 0 or len(samples) == 0:
        return samples
    out = samples.astype(float, copy=True)
    means = out.mean(axis=0)
    for s, i in COL.items():
        t = target.get(s)
        if t is None or not np.isfinite(t) or means[i] <= 0:
            continue
        goal = (1.0 - weight) * means[i] + weight * float(t)
        out[:, i] *= float(np.clip(goal / means[i], lo, hi))
    return out


def _synthetic_games(row: pd.Series, n: int, rng: np.random.Generator) -> np.ndarray:
    """Draw plausible per-game lines from per-game means (Poisson counts, binomial makes)."""
    out = np.zeros((n, len(STATS)))
    for s in ("pts", "reb", "ast", "stl", "blk", "tov"):
        out[:, COL[s]] = rng.poisson(max(float(row.get(s, 0.0)), 0.0), n)
    fga = rng.poisson(max(float(row.get("fga", 0.0)), 0.0), n)
    fg_pct = float(row["fgm"]) / float(row["fga"]) if row.get("fga", 0) else 0.45
    fgm = rng.binomial(fga, min(max(fg_pct, 0.0), 1.0))
    fta = rng.poisson(max(float(row.get("fta", 0.0)), 0.0), n)
    ft_pct = float(row["ftm"]) / float(row["fta"]) if row.get("fta", 0) else 0.75
    ftm = rng.binomial(fta, min(max(ft_pct, 0.0), 1.0))
    tpm = np.minimum(rng.poisson(max(float(row.get("tpm", 0.0)), 0.0), n), fgm)
    out[:, COL["fga"]], out[:, COL["fgm"]] = fga, fgm
    out[:, COL["fta"]], out[:, COL["ftm"]] = fta, ftm
    out[:, COL["tpm"]] = tpm
    return out


def build_player_models(
    conn: sqlite3.Connection,
    player_ids: Iterable[str],
    seasons: Sequence[int],
    projections: pd.DataFrame | None = None,
    statuses: Mapping[str, str] | None = None,
    max_games: int = 40,
    min_games: int = 8,
    seed: int | None = 7,
    play_probs: Mapping[str, float] | None = None,
    rest_probs: Mapping[str, float] | None = None,
    b2b_dates: Mapping[str, frozenset] | None = None,
    return_dates: Mapping[str, date] | None = None,
) -> dict[str, PlayerModel]:
    """Recent real games (most recent seasons first), re-centred on the projection's per-game means
    (`sim.projection_anchor`); synthetic from projections when history is thin.

    `play_probs` overrides the status-derived play probability per player (e.g. rest-of-season availability).
    `rest_probs` / `b2b_dates` add back-to-back rest risk on those dates (this-week sims only).
    `return_dates` (ESPN): a player whose status rules him out plays from that ET date at the GTD probability."""
    ids = list(dict.fromkeys(player_ids))
    rng = np.random.default_rng(seed)
    statuses = statuses or {}
    rest_probs = rest_probs or {}
    b2b_dates = b2b_dates or {}
    return_dates = return_dates or {}
    anchor = float(C("sim.projection_anchor"))
    lo, hi = float(C("sim.anchor_scale_min")), float(C("sim.anchor_scale_max"))
    form_sd = float(C("sim.form_sd"))
    models: dict[str, PlayerModel] = {}
    if not ids:
        return models
    placeholders = ",".join("?" * len(ids))
    season_list = ",".join(str(int(s)) for s in seasons)
    lines = pd.read_sql_query(
        f"SELECT espn_id, et_date, {', '.join(STATS)} FROM box_lines "
        f"WHERE played = 1 AND espn_id IN ({placeholders}) AND season IN ({season_list}) ORDER BY et_date DESC",
        conn,
        params=ids,
    )
    grouped = {pid: g for pid, g in lines.groupby("espn_id")}
    for pid in ids:
        recent = grouped.get(pid)
        samples = recent[list(STATS)].head(max_games).to_numpy(dtype=float) if recent is not None else np.empty((0, 11))
        synthetic = False
        if len(samples) < min_games and projections is not None and pid in projections.index:
            samples = _synthetic_games(projections.loc[pid], max_games, rng)
            synthetic = True
        elif len(samples) and projections is not None and pid in projections.index:
            samples = anchor_samples(samples, projections.loc[pid], anchor, lo, hi)
        if len(samples) == 0:
            samples = np.zeros((1, len(STATS)))
            synthetic = True
        overridden = play_probs is not None and pid in play_probs
        prob = play_probs[pid] if overridden else play_probability(statuses.get(pid, ""))
        returns = return_dates.get(pid) if not overridden and prob <= 0 else None
        models[pid] = PlayerModel(
            pid,
            samples,
            float(prob),
            synthetic,
            form_sd=form_sd,
            rest_prob=float(rest_probs.get(pid, 0.0)),
            b2b_dates=frozenset(b2b_dates.get(pid, frozenset())),
            return_date=returns,
            return_play_prob=play_probability("GTD") if returns is not None else 0.0,
        )
    return models


@dataclass
class TeamWeek:
    team_key: str
    player_games: list[str]  # one entry per remaining started game
    banked: dict[str, float] = field(default_factory=dict)  # stats already accumulated this week
    play_scale: dict[str, float] = field(default_factory=dict)  # extra per-player multipliers (e.g. start share)
    game_dates: list | None = None  # ET date of each entry in player_games (enables back-to-back rest)


def simulate_totals(
    week: TeamWeek, models: Mapping[str, PlayerModel], n_sims: int, rng: np.random.Generator
) -> np.ndarray:
    totals = np.zeros((n_sims, len(STATS)))
    for s, v in week.banked.items():
        if s in COL:
            totals[:, COL[s]] += v
    dates = week.game_dates if week.game_dates is not None else [None] * len(week.player_games)
    per_player: dict[str, np.ndarray] = {}
    for pid, day in zip(week.player_games, dates, strict=True):
        model = models.get(pid)
        if model is None:
            continue
        p = model.game_play_prob(day) * week.play_scale.get(pid, 1.0)
        if p <= 0:
            continue
        draws = model.samples[rng.integers(0, len(model.samples), n_sims)]
        if p < 1:
            draws = draws * (rng.random(n_sims) < p)[:, None]
        per_player[pid] = draws if pid not in per_player else per_player[pid] + draws
    for pid, week_lines in per_player.items():
        sd = models[pid].form_sd
        totals += week_lines * form_multiplier(rng, n_sims, sd)[:, None] if sd > 0 else week_lines
    return totals


def category_values(totals: np.ndarray, cat: Category) -> np.ndarray:
    if cat.kind == "pct":
        made, att = totals[:, COL[cat.made]], totals[:, COL[cat.att]]
        return np.divide(made, att, out=np.zeros_like(made), where=att > 0)
    return totals[:, COL[cat.stat]]


@dataclass(frozen=True)
class CategoryOdds:
    category: Category
    p_win: float
    p_tie: float
    p_loss: float
    mean_a: float
    mean_b: float


@dataclass(frozen=True)
class MatchupOdds:
    categories: tuple[CategoryOdds, ...]
    expected_score: float  # expected category wins, ties count 0.5
    p_majority: float  # P(win more categories than lose)
    cats_won_hist: tuple[float, ...]  # P(exactly k categories won), k = 0..n
    p_majority_loss: float = 0.0  # P(lose more categories than win)

    def swing(self, low: float = 0.25, high: float = 0.75) -> list[CategoryOdds]:
        return [c for c in self.categories if low <= c.p_win + 0.5 * c.p_tie <= high]


def compare(totals_a: np.ndarray, totals_b: np.ndarray, categories: Sequence[Category]) -> MatchupOdds:
    n = len(totals_a)
    wins = np.zeros(n)
    losses = np.zeros(n)
    odds = []
    for cat in categories:
        a, b = category_values(totals_a, cat), category_values(totals_b, cat)
        diff = a - b if cat.higher_is_better else b - a
        tie = np.isclose(a, b, rtol=0, atol=1e-9)
        win = (diff > 0) & ~tie
        loss = (diff < 0) & ~tie
        wins += win
        losses += loss
        odds.append(
            CategoryOdds(
                cat, float(win.mean()), float(tie.mean()), float(loss.mean()), float(a.mean()), float(b.mean())
            )
        )
    hist = np.bincount(wins.astype(int), minlength=len(categories) + 1) / n
    expected = float(sum(o.p_win + 0.5 * o.p_tie for o in odds))
    return MatchupOdds(
        tuple(odds),
        expected,
        float((wins > losses).mean()),
        tuple(float(x) for x in hist),
        float((losses > wins).mean()),
    )


def simulate_matchup(
    me: TeamWeek,
    opp: TeamWeek,
    models: Mapping[str, PlayerModel],
    categories: Sequence[Category],
    n_sims: int = 10000,
    seed: int | None = None,
) -> MatchupOdds:
    rng = np.random.default_rng(seed)
    return compare(simulate_totals(me, models, n_sims, rng), simulate_totals(opp, models, n_sims, rng), categories)
