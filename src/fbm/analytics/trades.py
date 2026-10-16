"""Trade evaluation and search.

Season-level lens: each team's schedule-neutral typical week is simulated (common random numbers via DrawBank) and
compared against every other team. A trade's value to a team = change in its average expected categories won per
week vs the league. Rosters are kept legal: the side receiving more players drops its least valuable players; the
side receiving fewer fills open spots with the best free agents.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from itertools import combinations

import numpy as np
import pandas as pd

from fbm.analytics.bank import DrawBank
from fbm.analytics.simulation import compare
from fbm.league.model import IL_SLOTS, LeagueState

AVG_GAMES_PER_WEEK = 3.5
GENERIC_GAMES = 4


def need_weights(win_rates: Mapping[str, float], punts: Sequence[str] = ()) -> dict[str, float]:
    """Marginal importance of each category: highest near 50% win rate, low when locked/lost, 0 when punted."""
    return {k: 0.0 if k in punts else max(0.1, 1.0 - abs(p - 0.5) * 1.6) for k, p in win_rates.items()}


class LeagueSim:
    def __init__(self, league: LeagueState, bank: DrawBank, values: pd.DataFrame):
        self.league = league
        self.bank = bank
        self.values = values
        self.categories = league.categories
        self.n_start = len(league.starting_slots())
        self.capacity = league.roster_size()
        self.active = {t.team_key: [p.player_id for p in t.players if p.selected not in IL_SLOTS] for t in league.teams}
        self.totals = {k: self.team_totals(ids) for k, ids in self.active.items()}
        self._base_profiles: dict[str, tuple[dict[str, float], float]] = {}

    def team_totals(self, ids: Sequence[str]) -> np.ndarray:
        if not ids:
            return np.zeros((self.bank.n, 11))
        share = min(1.0, self.n_start / len(ids)) * AVG_GAMES_PER_WEEK / GENERIC_GAMES
        total = np.zeros((self.bank.n, 11))
        for pid in ids:
            total += self.bank.generic_week(pid, GENERIC_GAMES)
        return total * share

    def profile(
        self, team_key: str, overrides: Mapping[str, np.ndarray] | None = None
    ) -> tuple[dict[str, float], float]:
        if not overrides and team_key in self._base_profiles:
            return self._base_profiles[team_key]
        totals = {**self.totals, **(overrides or {})}
        mine = totals[team_key]
        acc = {c.key: 0.0 for c in self.categories}
        expected = 0.0
        others = [k for k in totals if k != team_key]
        for other in others:
            odds = compare(mine, totals[other], self.categories)
            for o in odds.categories:
                acc[o.category.key] += o.p_win + 0.5 * o.p_tie
            expected += odds.expected_score
        n = max(1, len(others))
        result = ({k: v / n for k, v in acc.items()}, expected / n)
        if not overrides:
            self._base_profiles[team_key] = result
        return result


@dataclass
class TradeSide:
    team_key: str
    name: str
    gives: list[str]
    gets: list[str]
    drops: list[str] = field(default_factory=list)
    fa_adds: list[str] = field(default_factory=list)
    cats_before: dict[str, float] = field(default_factory=dict)
    cats_after: dict[str, float] = field(default_factory=dict)
    expected_before: float = 0.0
    expected_after: float = 0.0
    value_delta: float = 0.0  # neutral G-score total (per game) in minus out, incl. drops/fills
    g_delta_by_cat: dict[str, float] = field(default_factory=dict)

    @property
    def expected_delta(self) -> float:
        return self.expected_after - self.expected_before


@dataclass
class TradeEval:
    me: TradeSide
    them: TradeSide
    verdict: str
    notes: list[str] = field(default_factory=list)


def _g(values: pd.DataFrame, pid: str, col: str) -> float:
    return float(values.at[pid, col]) if pid in values.index else 0.0


def _rank_value(values: pd.DataFrame, pid: str, punts: Sequence[str], categories) -> float:
    return sum(_g(values, pid, f"g_{c.key}") for c in categories if c.key not in punts)


def _rebalance(
    ids: list[str],
    keep: set[str],
    capacity: int,
    values: pd.DataFrame,
    punts: Sequence[str],
    categories,
    free_agents: list[str],
    taken: set[str],
) -> tuple[list[str], list[str], list[str]]:
    drops: list[str] = []
    adds: list[str] = []
    while len(ids) > capacity:
        candidates = [pid for pid in ids if pid not in keep] or ids
        worst = min(candidates, key=lambda pid: _rank_value(values, pid, punts, categories))
        ids.remove(worst)
        drops.append(worst)
    for fa in free_agents:
        if len(ids) >= capacity:
            break
        if fa not in taken and fa not in ids:
            ids.append(fa)
            adds.append(fa)
            taken.add(fa)
    return ids, drops, adds


def verdict_for(me_delta: float, them_delta: float) -> str:
    if me_delta <= 0.02:
        return "not worth it for us"
    if them_delta >= 0.02:
        return "win-win"
    if them_delta >= -0.05:
        return "favors us (defensible)"
    return "lopsided (unlikely to be accepted)"


def evaluate_trade(
    sim: LeagueSim,
    my_key: str,
    partner_key: str,
    give: Sequence[str],
    get: Sequence[str],
    free_agents: Sequence[str],
    my_punts: Sequence[str] = (),
) -> TradeEval:
    league = sim.league
    mine_ids, their_ids = list(sim.active[my_key]), list(sim.active[partner_key])
    mine_all = {p.player_id for p in league.team(my_key).players}
    their_all = {p.player_id for p in league.team(partner_key).players}
    missing = [pid for pid in give if pid not in mine_all] + [pid for pid in get if pid not in their_all]
    if missing:
        raise ValueError(f"players not owned by the expected team: {missing}")
    if not give or not get:
        raise ValueError("a trade needs at least one player on each side")

    new_mine = [pid for pid in mine_ids if pid not in give] + list(get)
    new_theirs = [pid for pid in their_ids if pid not in get] + list(give)
    taken = {p.player_id for t in league.teams for p in t.players}
    fas = list(free_agents)
    new_mine, my_drops, my_adds = _rebalance(
        new_mine, set(get), sim.capacity, sim.values, my_punts, sim.categories, fas, taken
    )
    new_theirs, their_drops, their_adds = _rebalance(
        new_theirs, set(give), sim.capacity, sim.values, (), sim.categories, fas, taken
    )
    overrides = {my_key: sim.team_totals(new_mine), partner_key: sim.team_totals(new_theirs)}

    sides = []
    for key, gives, gets, drops, adds in (
        (my_key, give, get, my_drops, my_adds),
        (partner_key, get, give, their_drops, their_adds),
    ):
        before_cats, before_exp = sim.profile(key)
        after_cats, after_exp = sim.profile(key, overrides)
        incoming, outgoing = [*gets, *adds], [*gives, *drops]
        g_delta = {
            c.label: round(
                sum(_g(sim.values, p, f"g_{c.key}") for p in incoming)
                - sum(_g(sim.values, p, f"g_{c.key}") for p in outgoing),
                2,
            )
            for c in sim.categories
        }
        sides.append(
            TradeSide(
                key,
                league.team(key).name,
                list(gives),
                list(gets),
                drops,
                adds,
                before_cats,
                after_cats,
                before_exp,
                after_exp,
                round(
                    sum(_g(sim.values, p, "g_total") for p in incoming)
                    - sum(_g(sim.values, p, "g_total") for p in outgoing),
                    2,
                ),
                g_delta,
            )
        )
    me, them = sides
    notes = []
    if them.value_delta >= 0:
        notes.append("Looks fair or better to them by overall rankings (helps acceptance).")
    elif them.value_delta < -1.0:
        notes.append("They lose overall ranking value; expect pushback unless it fixes a clear need.")
    if my_drops:
        notes.append(f"We must drop {len(my_drops)} player(s) to make room.")
    if my_adds:
        notes.append(f"Opens {len(my_adds)} roster spot(s) for us; filled with the best free agent(s).")
    return TradeEval(me, them, verdict_for(me.expected_delta, them.expected_delta), notes)


@dataclass(frozen=True)
class TradeCandidate:
    partner_key: str
    give: tuple[str, ...]
    get: tuple[str, ...]
    screen_me: float
    screen_them: float


def screen_trades(
    sim: LeagueSim,
    my_key: str,
    my_punts: Sequence[str],
    free_agents: Sequence[str] = (),
    partners: Sequence[str] | None = None,
    max_give: int = 2,
    max_get: int = 2,
    top_k: int = 10,
) -> list[TradeCandidate]:
    """Cheap need-weighted G-score screen of 1-1, 2-1, 1-2 (and 2-2) packages."""
    league, values = sim.league, sim.values
    keys = [c.key for c in sim.categories]

    def vec(pid: str) -> np.ndarray:
        return np.array([_g(values, pid, f"g_{k}") for k in keys])

    gmat = {pid: vec(pid) for ids in sim.active.values() for pid in ids}
    my_w = np.array([need_weights(sim.profile(my_key)[0], my_punts)[k] for k in keys])

    def top(team_key: str, punts: Sequence[str]) -> list[str]:
        return sorted(sim.active[team_key], key=lambda p: -_rank_value(values, p, punts, sim.categories))[:top_k]

    def worst_vec(team_key: str, punts: Sequence[str]) -> np.ndarray:
        worst = min(sim.active[team_key], key=lambda p: _rank_value(values, p, punts, sim.categories))
        return gmat[worst]

    out: list[TradeCandidate] = []
    partners = partners or [t.team_key for t in league.teams if t.team_key != my_key]
    mine_top = top(my_key, my_punts)
    my_worst = worst_vec(my_key, my_punts)
    best_fa = max(free_agents, key=lambda p: _g(values, p, "g_total"), default=None)
    replacement = vec(best_fa) if best_fa else np.zeros(len(keys))
    for partner in partners:
        their_w = np.array([need_weights(sim.profile(partner)[0])[k] for k in keys])
        their_top = top(partner, ())
        their_worst = worst_vec(partner, ())
        for ng in range(1, max_give + 1):
            for nr in range(1, max_get + 1):
                for give in combinations(mine_top, ng):
                    give_vec = sum(gmat[p] for p in give)
                    for get in combinations(their_top, nr):
                        get_vec = sum(gmat[p] for p in get)
                        mine_delta = get_vec - give_vec
                        their_delta = give_vec - get_vec
                        if nr > ng:  # we receive more: we drop our worst; they fill with replacement
                            mine_delta = mine_delta - (nr - ng) * my_worst
                            their_delta = their_delta + (nr - ng) * replacement
                        elif ng > nr:
                            mine_delta = mine_delta + (ng - nr) * replacement
                            their_delta = their_delta - (ng - nr) * their_worst
                        s_me = float(my_w @ mine_delta)
                        s_them = float(their_w @ their_delta)
                        if s_me > 0 and s_them > -1.0:
                            out.append(TradeCandidate(partner, give, get, s_me, s_them))
    out.sort(key=lambda c: -(c.screen_me + 0.5 * min(c.screen_them, 0.0)))
    return out


def find_trades(
    sim: LeagueSim,
    my_key: str,
    free_agents: Sequence[str],
    my_punts: Sequence[str] = (),
    partners: Sequence[str] | None = None,
    max_give: int = 2,
    max_get: int = 2,
    confirm: int = 25,
    limit: int = 8,
) -> list[TradeEval]:
    candidates = screen_trades(sim, my_key, my_punts, free_agents, partners, max_give, max_get)[:confirm]
    results = [evaluate_trade(sim, my_key, c.partner_key, c.give, c.get, free_agents, my_punts) for c in candidates]
    good = [r for r in results if r.me.expected_delta > 0.02 and r.them.expected_delta >= -0.05]
    good.sort(key=lambda r: -(r.me.expected_delta + 0.25 * min(r.them.expected_delta, 0.0)))
    return good[:limit]
