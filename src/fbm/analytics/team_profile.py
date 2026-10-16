"""League-wide category profile: how often each team wins each category against every other team in a typical week.

This is the quantitative basis for build strategy (which categories to target, which are realistic punts).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np

from fbm.analytics.simulation import PlayerModel, compare, simulate_totals
from fbm.analytics.week import generic_week
from fbm.league.model import LeagueState


@dataclass(frozen=True)
class TeamProfile:
    team_key: str
    name: str
    cat_score: dict[str, float]  # avg (P(win) + 0.5 P(tie)) vs every other team
    cat_rank: dict[str, int]
    expected_score: float  # avg categories won (ties 0.5) vs a random opponent
    p_majority: float  # avg P(win more cats than lose) vs a random opponent


def league_profile(
    league: LeagueState, models: Mapping[str, PlayerModel], n_sims: int = 4000, seed: int = 11
) -> list[TeamProfile]:
    rng = np.random.default_rng(seed)
    n_start = len(league.starting_slots())
    totals = {t.team_key: simulate_totals(generic_week(t, n_start), models, n_sims, rng) for t in league.teams}
    keys = [c.key for c in league.categories]
    acc = {t.team_key: {k: [] for k in keys} for t in league.teams}
    exp: dict[str, list[float]] = {t.team_key: [] for t in league.teams}
    maj: dict[str, list[float]] = {t.team_key: [] for t in league.teams}
    teams = league.teams
    for i, a in enumerate(teams):
        for b in teams[i + 1 :]:
            odds = compare(totals[a.team_key], totals[b.team_key], league.categories)
            for o in odds.categories:
                acc[a.team_key][o.category.key].append(o.p_win + 0.5 * o.p_tie)
                acc[b.team_key][o.category.key].append(o.p_loss + 0.5 * o.p_tie)
            exp[a.team_key].append(odds.expected_score)
            exp[b.team_key].append(len(keys) - odds.expected_score)
            maj[a.team_key].append(odds.p_majority)
            maj[b.team_key].append(odds.p_majority_loss)
    scores = {t.team_key: {k: float(np.mean(v)) if v else 0.5 for k, v in acc[t.team_key].items()} for t in teams}
    ranks: dict[str, dict[str, int]] = {t.team_key: {} for t in teams}
    for k in keys:
        ordered = sorted(teams, key=lambda t, _k=k: scores[t.team_key][_k], reverse=True)
        for r, t in enumerate(ordered, 1):
            ranks[t.team_key][k] = r
    return [
        TeamProfile(
            t.team_key,
            t.name,
            scores[t.team_key],
            ranks[t.team_key],
            float(np.mean(exp[t.team_key])) if exp[t.team_key] else len(keys) / 2,
            float(np.mean(maj[t.team_key])) if maj[t.team_key] else 0.5,
        )
        for t in teams
    ]
