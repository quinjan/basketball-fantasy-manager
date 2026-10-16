"""Daily lineup optimizer (max games started, then value), solved as a max-weight bipartite assignment."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

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


def il_moves(players: list[RosterPlayer], il_slot_counts: Mapping[str, int]) -> list[IlMove]:
    """Suggest IL compliance moves: activate healthy IL players; stash IL-eligible injured players in open IL slots."""
    moves: list[IlMove] = []
    used = {slot: sum(1 for p in players if p.selected == slot) for slot in il_slot_counts}
    for p in players:
        if p.selected in IL_SLOTS and p.selected not in p.eligible:
            moves.append(IlMove(p.player_id, p.name, p.selected, "BN", "no longer IL-eligible (must activate)"))
            used[p.selected] -= 1
    for p in players:
        if p.selected in IL_SLOTS:
            continue
        for slot in ("IL", "IL+"):
            if slot in p.eligible and used.get(slot, 0) < il_slot_counts.get(slot, 0):
                moves.append(IlMove(p.player_id, p.name, p.selected, slot, f"{p.status or 'injured'}; frees a spot"))
                used[slot] = used.get(slot, 0) + 1
                break
    return moves
