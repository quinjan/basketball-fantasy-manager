"""Weekly streaming planner.

Greedy search over add/drop moves (with effective ET days) that maximize the punt-weighted expected number of
categories won against this week's opponent, within the remaining weekly adds. Every candidate is scored by
re-planning the daily lineups for the rest of the week and re-simulating with common random numbers (DrawBank).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, timedelta

import numpy as np

from fbm.analytics.bank import DrawBank
from fbm.analytics.categories import Category
from fbm.analytics.lineup import optimize_day
from fbm.analytics.simulation import MatchupOdds, compare
from fbm.constants import C
from fbm.league.model import IL_SLOTS, RosterPlayer


class InvalidPlan(ValueError):
    pass


@dataclass(frozen=True)
class Move:
    day: date  # first ET day the added player is on the roster (the dropped player is gone from this day)
    add: str
    drop: str | None


@dataclass(frozen=True)
class PlannedMove:
    move: Move
    gain: float  # increase in weighted expected categories from this move (given earlier moves)
    ros_cost: float  # rest-of-season value given up (>= 0)
    add_games: int  # add's games on/after move.day within the window
    drop_games_lost: int  # drop's games on/after move.day within the window


@dataclass
class StreamPlan:
    days: list[date]
    baseline: MatchupOdds
    final: MatchupOdds
    baseline_score: float
    final_score: float
    starts_before: int
    starts_after: int
    moves: list[PlannedMove] = field(default_factory=list)
    adds_available: int = 0
    candidates_considered: int = 0


@dataclass
class StreamContext:
    """Everything the planner needs; built by services from the league + analytics context."""

    base: list[RosterPlayer]  # my current roster (including IL players)
    pool: Mapping[str, RosterPlayer]  # RosterPlayer for every base player and free-agent candidate
    days: list[date]
    playing: Mapping[date, set[str]]  # NBA team abbreviations playing on each ET day
    starting_slots: list[str]
    capacity: int  # active (non-IL) roster spots
    value: Mapping[str, float]  # rest-of-season value (punt-aware) used for lineup tie-breaks and ROS cost
    bank: DrawBank
    opp_totals: np.ndarray
    categories: Sequence[Category]
    weights: Mapping[str, float]  # category key -> objective weight (0 for punts)
    banked: Mapping[str, float] = field(default_factory=dict)
    _lineups: dict = field(default_factory=dict)

    def game_days(self, pid: str, start: date | None = None) -> list[date]:
        team = self.pool[pid].nba_team
        return [d for d in self.days if team in self.playing.get(d, set()) and (start is None or d >= start)]


def score(odds: MatchupOdds, weights: Mapping[str, float]) -> float:
    return float(sum(weights.get(o.category.key, 1.0) * (o.p_win + 0.5 * o.p_tie) for o in odds.categories))


def roster_by_day(ctx: StreamContext, moves: Sequence[Move]) -> dict[date, tuple[str, ...]]:
    """Player ids on my roster each day after applying moves in day order. Raises InvalidPlan."""
    ids = [p.player_id for p in ctx.base]
    il = {p.player_id for p in ctx.base if p.selected in IL_SLOTS}
    ordered = sorted(moves, key=lambda m: m.day)
    out: dict[date, tuple[str, ...]] = {}
    i = 0
    for d in ctx.days:
        while i < len(ordered) and ordered[i].day <= d:
            m = ordered[i]
            if m.add in ids:
                raise InvalidPlan(f"{m.add} already rostered on {m.day}")
            if m.drop is not None:
                if m.drop not in ids:
                    raise InvalidPlan(f"{m.drop} not rostered on {m.day}")
                if m.drop in il:
                    raise InvalidPlan("cannot drop an IL player to open an active spot")
                ids.remove(m.drop)
            if len([x for x in ids if x not in il]) >= ctx.capacity:
                raise InvalidPlan(f"roster full on {m.day}: a drop is required")
            ids.append(m.add)
            i += 1
        out[d] = tuple(ids)
    if i < len(ordered):
        raise InvalidPlan("move outside the planning window")
    return out


def _started(ctx: StreamContext, d: date, ids: tuple[str, ...]) -> tuple[str, ...]:
    key = (d, frozenset(ids))
    cached = ctx._lineups.get(key)
    if cached is None:
        players = [ctx.pool[pid] for pid in ids]
        teams = ctx.playing.get(d, set())
        playing = {p.player_id for p in players if p.nba_team in teams}
        res = optimize_day(players, ctx.starting_slots, playing, ctx.value)
        starting = set(ctx.starting_slots)
        cached = tuple(pid for pid, slot in res.assignments.items() if slot in starting and pid in playing)
        ctx._lineups[key] = cached
    return cached


def evaluate(ctx: StreamContext, moves: Sequence[Move]) -> tuple[MatchupOdds, int]:
    rosters = roster_by_day(ctx, moves)
    games = [(pid, d) for d in ctx.days for pid in _started(ctx, d, rosters[d])]
    totals = ctx.bank.total(games, ctx.banked)
    return compare(totals, ctx.opp_totals, ctx.categories), len(games)


def _effective_days(ctx: StreamContext, add: str, drop: str | None, earliest: date) -> list[date]:
    add_days = ctx.game_days(add, earliest)
    if not add_days:
        return []
    candidates = {earliest}
    if drop is not None:
        for g in ctx.game_days(drop, earliest):
            nxt = g + timedelta(days=1)
            if nxt <= add_days[-1] and nxt in ctx.days:
                candidates.add(nxt)
    return sorted(d for d in candidates if d <= add_days[-1])


def plan_streams(
    ctx: StreamContext,
    free_agents: Sequence[str],
    droppable: Sequence[str],
    adds_available: int,
    min_gain: float | None = None,
    ros_penalty: float | None = None,
) -> StreamPlan:
    min_gain = float(C("stream.min_gain")) if min_gain is None else min_gain
    ros_penalty = float(C("stream.ros_penalty")) if ros_penalty is None else ros_penalty
    base_odds, base_starts = evaluate(ctx, [])
    current_score = score(base_odds, ctx.weights)
    plan = StreamPlan(
        ctx.days,
        base_odds,
        base_odds,
        current_score,
        current_score,
        base_starts,
        base_starts,
        adds_available=adds_available,
        candidates_considered=len(free_agents),
    )
    moves: list[Move] = []
    added: dict[str, date] = {}
    drop_pool = list(droppable)
    for _ in range(max(0, adds_available)):
        best: tuple[float, PlannedMove, MatchupOdds, int] | None = None
        rosters = roster_by_day(ctx, moves)
        for add in free_agents:
            if add in added:
                continue
            for drop in [*drop_pool, None]:
                if drop is not None and drop in added:
                    earliest = added[drop] + timedelta(days=1)
                else:
                    earliest = ctx.days[0]
                if earliest > ctx.days[-1]:
                    continue
                for d in _effective_days(ctx, add, drop, earliest):
                    if drop is not None and drop not in rosters[d]:
                        continue
                    candidate = Move(d, add, drop)
                    try:
                        odds, starts = evaluate(ctx, [*moves, candidate])
                    except InvalidPlan:
                        continue
                    gain = score(odds, ctx.weights) - current_score
                    cost = max(0.0, ctx.value.get(drop, 0.0) - ctx.value.get(add, 0.0)) if drop else 0.0
                    objective = gain - ros_penalty * cost
                    if best is None or objective > best[0]:
                        planned = PlannedMove(
                            candidate,
                            gain,
                            cost,
                            len(ctx.game_days(add, d)),
                            len(ctx.game_days(drop, d)) if drop else 0,
                        )
                        best = (objective, planned, odds, starts)
        if best is None or best[1].gain < min_gain:
            break
        _, planned, odds, starts = best
        moves.append(planned.move)
        added[planned.move.add] = planned.move.day
        drop_pool.append(planned.move.add)
        if planned.move.drop in drop_pool and planned.move.drop not in added:
            drop_pool.remove(planned.move.drop)
        current_score += planned.gain
        plan.moves.append(planned)
        plan.final, plan.final_score, plan.starts_after = odds, current_score, starts
    return plan


def with_selected_bn(player: RosterPlayer) -> RosterPlayer:
    return replace(player, selected="BN")
