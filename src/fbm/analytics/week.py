"""Turn rosters + the NBA schedule into simulated weeks (per-day optimal lineups -> started games)."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta

from fbm.analytics.lineup import LineupResult, optimize_day
from fbm.analytics.simulation import TeamWeek
from fbm.league.model import IL_SLOTS, FantasyTeam

AVG_GAMES_PER_WEEK = 3.5


def fantasy_week(day: date) -> tuple[date, date]:
    """Monday-Sunday week containing `day` (Yahoo NBA weeks run Mon-Sun ET)."""
    start = day - timedelta(days=day.weekday())
    return start, start + timedelta(days=6)


def date_range(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def teams_playing(conn: sqlite3.Connection, start: date, end: date) -> dict[date, set[str]]:
    out: dict[date, set[str]] = {d: set() for d in date_range(start, end)}
    for row in conn.execute(
        "SELECT et_date, home, away FROM games WHERE season_type = 2 AND et_date BETWEEN ? AND ?",
        (start.isoformat(), end.isoformat()),
    ):
        d = date.fromisoformat(row["et_date"])
        out.setdefault(d, set()).update((row["home"], row["away"]))
    return out


@dataclass(frozen=True)
class DayPlan:
    day: date
    lineup: LineupResult
    started: tuple[str, ...]  # player_ids starting with a game


def plan_team_week(
    team: FantasyTeam,
    days: list[date],
    playing_by_date: Mapping[date, set[str]],
    starting_slots: list[str],
    value: Mapping[str, float],
) -> tuple[TeamWeek, list[DayPlan]]:
    starting = set(starting_slots)
    games: list[str] = []
    plans: list[DayPlan] = []
    for d in days:
        nba_teams = playing_by_date.get(d, set())
        playing = {p.player_id for p in team.players if p.nba_team in nba_teams}
        res = optimize_day(team.players, starting_slots, playing, value)
        started = tuple(pid for pid, slot in res.assignments.items() if slot in starting and pid in playing)
        games.extend(started)
        plans.append(DayPlan(d, res, started))
    return TeamWeek(team.team_key, games), plans


def generic_week(team: FantasyTeam, n_starting: int, games_per_player: float = AVG_GAMES_PER_WEEK) -> TeamWeek:
    """Schedule-neutral week for season-level comparisons: every active player plays ~3.5 games, scaled by the
    share of roster spots that can start."""
    active = [p for p in team.players if p.selected not in IL_SLOTS]
    if not active:
        return TeamWeek(team.team_key, [])
    games = 4
    share = min(1.0, n_starting / len(active)) * games_per_player / games
    return TeamWeek(
        team.team_key,
        [p.player_id for p in active for _ in range(games)],
        play_scale={p.player_id: share for p in active},
    )
