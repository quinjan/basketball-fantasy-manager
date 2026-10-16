"""Punt-scenario evaluator: which categories (if any) should the build give up?

A punt changes how we value players, so its payoff comes through roster moves. For each candidate punt set:

1. Re-value every player under that lens (G-scores summed over the categories still targeted).
2. Lens proposes, simulation confirms: walk the lens's swap candidates (best free agents in, weakest players out) in
   lens order and keep the first that raises the simulated score, up to `punt.fa_moves` swaps. This mirrors how the
   GM works (rankings under the strategy, then evaluate_add_drop) and stops a lens from piling on categories we
   already win. The lens's top pick, when simulation rejects it, is reported as a conflict.
3. Score the resulting roster in ALL categories against every other team (schedule-neutral typical week, common
   random numbers), so scenarios differ only by the roster each lens builds. "No punt" gets the same budget.

Trade-market edges are reported, not simulated: players the lens ranks far above their neutral (market) rank are buy
targets; my players it ranks far below are sell candidates.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import combinations

import numpy as np
import pandas as pd

from fbm.analytics.simulation import compare


@dataclass(frozen=True)
class Swap:
    add: str
    drop: str
    gain: float  # lens value gained (G points per game)
    sim_delta: float = 0.0  # change in the simulated score when the swap was tried


@dataclass(frozen=True)
class Edge:
    player_id: str
    team_key: str
    market_rank: int  # rank by neutral (all-category) value
    lens_rank: int  # rank under this punt lens


@dataclass
class Scenario:
    punts: tuple[str, ...]
    swaps: list[Swap]
    cat_rates: dict[str, float]  # avg P(win) + 0.5 P(tie) vs every other team, after the swaps
    expected_cats: float
    p_win_week: float
    reliable_cats: int  # categories won at least `reliable` of the time
    roster_value: float  # my active roster, summed under the lens (after swaps)
    value_rank: int  # my rank among league teams by lens value (after swaps)
    sell: list[Edge] = field(default_factory=list)
    buy: list[Edge] = field(default_factory=list)
    conflicts: list[Swap] = field(default_factory=list)  # lens's top swap that simulation rejected


def candidate_punts(win_rates: Mapping[str, float], max_size: int = 2, pair_pool: int = 4) -> list[tuple[str, ...]]:
    """No punt, every single category, and pairs among my `pair_pool` weakest categories."""
    weakest = sorted(win_rates, key=lambda k: win_rates[k])
    out: list[tuple[str, ...]] = [(), *[(k,) for k in weakest]]
    for size in range(2, max_size + 1):
        out += [tuple(sorted(c)) for c in combinations(weakest[:pair_pool], size)]
    return out


def lens_values(values: pd.DataFrame, categories: Sequence[str], punts: Sequence[str]) -> pd.Series:
    """Per-player value under a punt lens: G-scores summed over the categories not punted."""
    return values[[f"g_{k}" for k in categories if k not in punts]].sum(axis=1)


def plan_swaps(
    roster: Sequence[str],
    free_agents: Sequence[str],
    value: Mapping[str, float],
    k: int,
    min_gain: float,
    score: Callable[[Sequence[str]], float],
    min_improvement: float,
    fa_depth: int = 5,
    drop_depth: int = 3,
) -> tuple[list[Swap], list[Swap]]:
    """Up to `k` swaps: candidates in lens order, each kept only if it raises `score` by `min_improvement`.

    Returns (accepted swaps, conflicts) where a conflict is the lens's top candidate at a step that the simulation
    rejected.
    """
    ids = list(roster)
    current = score(ids)
    swaps: list[Swap] = []
    conflicts: list[Swap] = []
    for _ in range(max(0, k)):
        fas = sorted((f for f in free_agents if f not in ids), key=lambda f: -value.get(f, -99.0))[:fa_depth]
        drops = sorted(ids, key=lambda pid: value.get(pid, 0.0))[:drop_depth]
        pairs = sorted(
            (
                (add, drop, value.get(add, -99.0) - value.get(drop, 0.0))
                for add in fas
                for drop in drops
                if value.get(add, -99.0) - value.get(drop, 0.0) >= min_gain
            ),
            key=lambda x: -x[2],
        )
        accepted = None
        for i, (add, drop, gain) in enumerate(pairs):
            trial = [pid for pid in ids if pid != drop] + [add]
            new = score(trial)
            if new - current >= min_improvement:
                accepted = (Swap(add, drop, float(gain), float(new - current)), trial, new)
                break
            if i == 0:
                conflicts.append(Swap(add, drop, float(gain), float(new - current)))
        if accepted is None:
            break
        swap, ids, current = accepted
        swaps.append(swap)
    return swaps, conflicts


def apply_swaps(roster: Sequence[str], swaps: Sequence[Swap]) -> list[str]:
    ids = list(roster)
    for s in swaps:
        ids.remove(s.drop)
        ids.append(s.add)
    return ids


def odds_vs_league(mine: np.ndarray, others: Mapping[str, np.ndarray], categories) -> tuple[dict, float, float]:
    """Average category win rates, expected categories and P(win the week) against every other team."""
    acc = {c.key: 0.0 for c in categories}
    expected = p_win = 0.0
    for totals in others.values():
        odds = compare(mine, totals, categories)
        for o in odds.categories:
            acc[o.category.key] += o.p_win + 0.5 * o.p_tie
        expected += odds.expected_score
        p_win += odds.p_majority
    n = max(1, len(others))
    return {k: v / n for k, v in acc.items()}, expected / n, p_win / n


def market_edges(
    neutral: pd.Series,
    lens: pd.Series,
    owners: Mapping[str, str],
    my_team_key: str,
    top_pool: int = 150,
    limit: int = 5,
    min_rank_gap: int = 10,
) -> tuple[list[Edge], list[Edge]]:
    """Sell: my players the lens ranks well below the market. Buy: others' players it ranks well above."""
    market_rank = neutral.rank(ascending=False, method="first").astype(int)
    lens_rank = lens.rank(ascending=False, method="first").astype(int)
    relevant = [pid for pid in neutral.index if pid in owners and min(market_rank[pid], lens_rank[pid]) <= top_pool]
    sell = [
        Edge(pid, owners[pid], int(market_rank[pid]), int(lens_rank[pid]))
        for pid in relevant
        if owners[pid] == my_team_key and lens_rank[pid] - market_rank[pid] >= min_rank_gap
    ]
    buy = [
        Edge(pid, owners[pid], int(market_rank[pid]), int(lens_rank[pid]))
        for pid in relevant
        if owners[pid] != my_team_key and market_rank[pid] - lens_rank[pid] >= min_rank_gap
    ]
    sell.sort(key=lambda e: e.market_rank - e.lens_rank)
    buy.sort(key=lambda e: e.lens_rank - e.market_rank)
    return sell[:limit], buy[:limit]


def evaluate_scenarios(
    scenarios: Sequence[tuple[str, ...]],
    *,
    values: pd.DataFrame,
    category_keys: Sequence[str],
    categories,
    my_team_key: str,
    active: Mapping[str, Sequence[str]],
    free_agents: Sequence[str],
    team_totals: Callable[[Sequence[str]], np.ndarray],
    others_totals: Mapping[str, np.ndarray],
    fa_moves: int,
    min_gain: float,
    reliable: float,
    metric: str = "expected_cats",
    min_improvement: float = 0.02,
) -> list[Scenario]:
    neutral = lens_values(values, category_keys, ())
    owners = {pid: key for key, ids in active.items() for pid in ids}
    cache: dict[frozenset[str], tuple[dict, float, float]] = {}

    def odds(roster: Sequence[str]) -> tuple[dict, float, float]:
        key = frozenset(roster)
        if key not in cache:
            cache[key] = odds_vs_league(team_totals(list(roster)), others_totals, categories)
        return cache[key]

    def score(roster: Sequence[str]) -> float:
        _, expected, p_win = odds(roster)
        return p_win if metric == "p_win_week" else expected

    out = []
    for punts in scenarios:
        lens = lens_values(values, category_keys, punts)
        value = lens.to_dict()
        swaps, conflicts = plan_swaps(
            active[my_team_key], free_agents, value, fa_moves, min_gain, score, min_improvement
        )
        roster = apply_swaps(active[my_team_key], swaps)
        rates, expected, p_win = odds(roster)
        team_values = {key: sum(value.get(pid, 0.0) for pid in ids) for key, ids in active.items()}
        team_values[my_team_key] = sum(value.get(pid, 0.0) for pid in roster)
        rank = 1 + sum(1 for key, v in team_values.items() if key != my_team_key and v > team_values[my_team_key])
        sell, buy = market_edges(neutral, lens, owners, my_team_key)
        out.append(
            Scenario(
                tuple(punts),
                swaps,
                rates,
                expected,
                p_win,
                sum(1 for r in rates.values() if r >= reliable),
                team_values[my_team_key],
                rank,
                sell if punts else [],
                buy if punts else [],
                conflicts,
            )
        )
    return out
