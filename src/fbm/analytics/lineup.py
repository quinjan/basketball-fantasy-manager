"""Daily lineup optimizer (max games started, then value), solved as a max-weight bipartite assignment."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
from scipy.optimize import linear_sum_assignment

from fbm.league.model import IL_SLOTS, RosterPlayer, can_fill

GAME_WEIGHT = 1000.0  # a started game always beats any value difference
STABILITY_BONUS = 0.01  # prefer leaving players where they are (fewer manual changes)
INELIGIBLE = -1e9


@dataclass(frozen=True)
class LineupResult:
    assignments: dict[str, str]  # player_id -> slot (starting slot, BN, or IL slot)
    starters_with_games: int
    benched_with_games: tuple[str, ...]
    changes: dict[str, tuple[str, str]] = field(default_factory=dict)  # player_id -> (old, new)

    def moves(self, players: Iterable[RosterPlayer]) -> list[tuple[str, str, str]]:
        names = {p.player_id: p.name for p in players}
        return [(names.get(pid, pid), old, new) for pid, (old, new) in self.changes.items()]


def optimize_day(
    players: list[RosterPlayer],
    starting_slots: list[str],
    playing: set[str],
    value: Mapping[str, float],
    locked: Iterable[str] = (),
) -> LineupResult:
    """Assign players to `starting_slots` for one game day.

    playing: player_ids whose NBA team has a game that day.
    locked: player_ids whose game has started (their slot cannot change).
    Players currently in IL/IL+ stay there (IL moves are handled separately).
    """
    locked_ids = set(locked)
    il_players = [p for p in players if p.selected in IL_SLOTS]
    locked_players = [p for p in players if p.player_id in locked_ids and p.selected not in IL_SLOTS]
    open_slots = list(starting_slots)
    for p in locked_players:
        if p.selected in open_slots:
            open_slots.remove(p.selected)
    candidates = [p for p in players if p.player_id not in locked_ids and p.selected not in IL_SLOTS]

    def has_game(p: RosterPlayer) -> bool:
        return p.player_id in playing and not p.is_out

    assignments: dict[str, str] = {p.player_id: p.selected for p in il_players + locked_players}
    if candidates and open_slots:
        weights = np.full((len(candidates), len(open_slots)), INELIGIBLE)
        for i, p in enumerate(candidates):
            base = (GAME_WEIGHT if has_game(p) else 0.0) + float(value.get(p.player_id, 0.0))
            for j, slot in enumerate(open_slots):
                if can_fill(p.eligible, slot):
                    weights[i, j] = base + (STABILITY_BONUS if p.selected == slot else 0.0)
        rows, cols = linear_sum_assignment(weights, maximize=True)
        for i, j in zip(rows, cols, strict=True):
            if weights[i, j] > INELIGIBLE / 2:
                assignments[candidates[i].player_id] = open_slots[j]
    for p in candidates:
        assignments.setdefault(p.player_id, "BN")

    starting = set(starting_slots)
    starters_with_games = sum(1 for p in players if assignments[p.player_id] in starting and has_game(p))
    benched = tuple(p.player_id for p in players if assignments[p.player_id] == "BN" and has_game(p))
    changes = {
        p.player_id: (p.selected, assignments[p.player_id]) for p in players if p.selected != assignments[p.player_id]
    }
    return LineupResult(assignments, starters_with_games, benched, changes)


@dataclass(frozen=True)
class IlMove:
    player_id: str
    name: str
    from_slot: str
    to_slot: str
    reason: str
    blocked: bool = False  # needs an approval-gated drop before it can happen


def il_moves(
    players: list[RosterPlayer],
    il_slot_counts: Mapping[str, int],
    capacity: int | None = None,
    locked: Iterable[str] = (),
) -> list[IlMove]:
    """IL compliance moves.

    1. Stash: injured active players that are IL/IL+-eligible go into open IL slots (frees active spots).
    2. Activate: IL players no longer IL-eligible move to the bench if an active spot is open; otherwise the move is
       `blocked` (Yahoo blocks all roster moves while an ineligible player sits in IL, so a drop must be proposed).
    Players whose games have started (`locked`) are left alone.
    """
    locked_ids = set(locked)
    moves: list[IlMove] = []
    used = {slot: sum(1 for p in players if p.selected == slot) for slot in il_slot_counts}
    active = sum(1 for p in players if p.selected not in IL_SLOTS)
    injured = [
        p
        for p in players
        if p.selected not in IL_SLOTS and p.player_id not in locked_ids and any(s in p.eligible for s in il_slot_counts)
    ]
    to_activate = [
        p for p in players if p.selected in IL_SLOTS and p.selected not in p.eligible and p.player_id not in locked_ids
    ]

    def stash(p: RosterPlayer, slot: str) -> None:
        nonlocal active
        moves.append(IlMove(p.player_id, p.name, p.selected, slot, f"{p.status or 'injured'}; frees a spot"))
        injured.remove(p)
        active -= 1

    # 1. Stash injured players into currently open IL slots.
    for p in list(injured):
        for slot in ("IL", "IL+"):
            if slot in p.eligible and used.get(slot, 0) < il_slot_counts.get(slot, 0):
                stash(p, slot)
                used[slot] = used.get(slot, 0) + 1
                break
    # 2. Activate healthy IL players: swap with an injured player for the same slot, else use an open active spot.
    for p in to_activate:
        swap = next((q for q in injured if p.selected in q.eligible), None)
        if swap is not None:
            stash(swap, p.selected)
            moves.append(IlMove(p.player_id, p.name, p.selected, "BN", "healthy: activate from IL"))
            active += 1
        elif capacity is None or active < capacity:
            moves.append(IlMove(p.player_id, p.name, p.selected, "BN", "healthy: activate from IL"))
            used[p.selected] -= 1
            active += 1
        else:
            moves.append(
                IlMove(
                    p.player_id,
                    p.name,
                    p.selected,
                    p.selected,
                    "healthy but the roster is full: drop someone to activate (proposal needed)",
                    True,
                )
            )
    # 3. Stash remaining injured players into slots freed by activations.
    for p in list(injured):
        for slot in ("IL", "IL+"):
            if slot in p.eligible and used.get(slot, 0) < il_slot_counts.get(slot, 0):
                stash(p, slot)
                used[slot] = used.get(slot, 0) + 1
                break
    return moves


def started_teams(games: Iterable[Mapping[str, object]], now: datetime) -> set[str]:
    """NBA teams whose game on this ET day has started (by state or scheduled tip time) — their players are locked."""
    out: set[str] = set()
    for g in games:
        start = datetime.fromisoformat(str(g["start_utc"]))
        if g["state"] in ("in", "post") or start <= now:
            out.update((str(g["home"]), str(g["away"])))
    return out
