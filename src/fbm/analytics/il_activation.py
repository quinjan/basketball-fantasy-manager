"""IL activation: move one of my IL players to the active roster (optionally dropping another player) from an
effective ET day, and model the rest of the week as dated roster phases.

Pure helpers: validation, roster phases and the games each phase starts. The matchup simulation itself lives in
`fbm.gm.il` (it needs the analytics context)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import date

from fbm.analytics.lineup import optimize_day
from fbm.league.model import IL_SLOTS, RosterPlayer

Phase = tuple[date, list[RosterPlayer]]  # (first ET day this roster applies, players)


def validate_activation(players: Sequence[RosterPlayer], activate: str, drop: str | None, capacity: int) -> list[str]:
    """Why "activate `activate` from IL (and drop `drop`)" is impossible on this roster; [] when it works.
    `capacity` = active (non-IL) roster spots."""
    by_id = {p.player_id: p for p in players}
    problems: list[str] = []
    mover = by_id.get(activate)
    if mover is None:
        problems.append(f"{activate} is not on your roster")
    elif mover.selected not in IL_SLOTS:
        problems.append(f"{mover.name} is not in an IL slot")
    dropped = by_id.get(drop) if drop else None
    if drop and dropped is None:
        problems.append(f"{drop} is not on your roster")
    if drop and drop == activate:
        problems.append("the player being activated cannot also be the drop")
    if mover is None or mover.selected not in IL_SLOTS:
        return problems
    active = sum(1 for p in players if p.selected not in IL_SLOTS)
    frees_spot = dropped is not None and dropped.selected not in IL_SLOTS
    if active + 1 - (1 if frees_spot else 0) > capacity:
        if dropped is not None:
            problems.append(f"dropping {dropped.name} (an IL player) does not open an active roster spot")
        else:
            problems.append(f"roster is full ({active}/{capacity} active): a drop is required to activate {mover.name}")
    return problems


def activation_phases(
    players: Sequence[RosterPlayer],
    activate: str,
    drop: str | None,
    effective: date,
    available_from: date | None,
) -> list[Phase]:
    """Roster by day. Before `effective` nothing changes. From `effective` the drop is gone and the activated player
    sits on the bench; he can only start from `available_from` (his return day: while still injured the lineup
    optimizer treats him as out). `available_from=None`: he stays injured for the whole window."""
    before = [replace(p) for p in players]
    after = [replace(p) for p in players if p.player_id != drop]
    for p in after:
        if p.player_id == activate:
            p.selected = "BN"
    phases: list[Phase] = [(date.min, before)]
    if available_from is not None and available_from <= effective:
        for p in after:
            if p.player_id == activate:
                p.status = ""
        phases.append((effective, after))
        return phases
    phases.append((effective, after))
    if available_from is not None:
        healthy = [replace(p, status="") if p.player_id == activate else replace(p) for p in after]
        phases.append((available_from, healthy))
    return phases


def phase_games(
    phases: Sequence[Phase],
    days: Sequence[date],
    playing: Mapping[date, set[str]],
    starting_slots: list[str],
    value: Mapping[str, float],
    skip: Mapping[date, set[str]] | None = None,
) -> list[tuple[str, date]]:
    """(player_id, day) for every game my daily-optimal lineup starts, using the roster phase in force each day.
    `skip`: NBA teams whose game that day already started (stats banked)."""
    skip = skip or {}
    starting = set(starting_slots)
    games: list[tuple[str, date]] = []
    for d in days:
        players = next(roster for start, roster in reversed(phases) if start <= d)
        nba = playing.get(d, set())
        playing_ids = {p.player_id for p in players if p.nba_team in nba}
        res = optimize_day(players, starting_slots, playing_ids, value)
        by_id = {p.player_id: p for p in players}
        for pid, slot in res.assignments.items():
            if (
                slot in starting
                and pid in playing_ids
                and not by_id[pid].is_out
                and by_id[pid].nba_team not in skip.get(d, set())
            ):
                games.append((pid, d))
    return games
