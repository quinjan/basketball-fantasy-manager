"""Monte Carlo category simulation for H2H weeks.

Each player-game is drawn by resampling the player's real recent box-score lines (keeps stat correlations such as
makes-with-attempts), times a Bernoulli "plays" draw from injury status. Team weekly totals are simulated once per
team, so any pair of teams can be compared cheaply (matchups, league-wide category profiles).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from fbm.analytics.categories import Category

STATS: tuple[str, ...] = ("pts", "reb", "ast", "stl", "blk", "tov", "tpm", "fgm", "fga", "ftm", "fta")
COL = {s: i for i, s in enumerate(STATS)}

PLAY_PROB = {
    "": 1.0,
    "ACTIVE": 1.0,
    "DTD": 0.8,
    "DAY-TO-DAY": 0.8,
    "GTD": 0.6,
    "QUESTIONABLE": 0.5,
    "DOUBTFUL": 0.2,
    "O": 0.0,
    "OUT": 0.0,
    "INJ": 0.0,
    "SUSP": 0.0,
    "NA": 0.0,
}


def play_probability(status: str | None) -> float:
    return PLAY_PROB.get((status or "").strip().upper(), 1.0)


@dataclass
class PlayerModel:
    player_id: str
    samples: np.ndarray  # (k, len(STATS)) per-game lines
    play_prob: float = 1.0
    synthetic: bool = False

    @property
    def mean(self) -> np.ndarray:
        return self.samples.mean(axis=0)


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
) -> dict[str, PlayerModel]:
    """Recent real games (most recent seasons first); synthetic from projections when history is thin."""
    ids = list(dict.fromkeys(player_ids))
    rng = np.random.default_rng(seed)
    statuses = statuses or {}
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
        if len(samples) == 0:
            samples = np.zeros((1, len(STATS)))
            synthetic = True
        models[pid] = PlayerModel(pid, samples, play_probability(statuses.get(pid, "")), synthetic)
    return models


@dataclass
class TeamWeek:
    team_key: str
    player_games: list[str]  # one entry per remaining started game
    banked: dict[str, float] = field(default_factory=dict)  # stats already accumulated this week
    play_scale: dict[str, float] = field(default_factory=dict)  # extra per-player multipliers (e.g. start share)


def simulate_totals(
    week: TeamWeek, models: Mapping[str, PlayerModel], n_sims: int, rng: np.random.Generator
) -> np.ndarray:
    totals = np.zeros((n_sims, len(STATS)))
    for s, v in week.banked.items():
        if s in COL:
            totals[:, COL[s]] += v
    for pid in week.player_games:
        model = models.get(pid)
        if model is None:
            continue
        p = model.play_prob * week.play_scale.get(pid, 1.0)
        if p <= 0:
            continue
        draws = model.samples[rng.integers(0, len(model.samples), n_sims)]
        if p < 1:
            draws = draws * (rng.random(n_sims) < p)[:, None]
        totals += draws
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
