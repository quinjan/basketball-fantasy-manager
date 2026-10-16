"""Fantasy playoff schedule: which NBA teams play the most games in the fantasy playoff weeks.

Fantasy weeks run Monday-Sunday (ET). Week 1 is the week of the first regular-season game; the playoffs are the
league's playoff weeks from Yahoo settings when available, otherwise the last `playoffs.weeks` weeks.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from fbm.constants import C


@dataclass(frozen=True)
class FantasyWeek:
    number: int
    start: date
    end: date


def fantasy_weeks(conn: sqlite3.Connection, season: int) -> list[FantasyWeek]:
    row = conn.execute(
        "SELECT MIN(et_date), MAX(et_date) FROM games WHERE season = ? AND season_type = 2", (season,)
    ).fetchone()
    if not row or not row[0]:
        return []
    first, last = date.fromisoformat(row[0]), date.fromisoformat(row[1])
    start = first - timedelta(days=first.weekday())
    weeks, n = [], 1
    while start <= last:
        weeks.append(FantasyWeek(n, start, start + timedelta(days=6)))
        start += timedelta(days=7)
        n += 1
    return weeks


def playoff_weeks(weeks: list[FantasyWeek], settings: Mapping[str, Any] | None = None) -> list[FantasyWeek]:
    s = (settings or {}).get("settings", {}) if settings else {}
    start_week = s.get("playoff_start_week")
    end_week = (settings or {}).get("end_week")
    if start_week and str(s.get("uses_playoff", "1")) == "1":
        lo, hi = int(start_week), int(end_week or weeks[-1].number)
        chosen = [w for w in weeks if lo <= w.number <= hi]
        if chosen:
            return chosen
    n = int(C("playoffs.weeks"))
    return weeks[-n:] if weeks else []


def team_games_by_week(conn: sqlite3.Connection, weeks: Iterable[FantasyWeek]) -> dict[str, list[int]]:
    weeks = list(weeks)
    out: dict[str, list[int]] = {}
    for i, w in enumerate(weeks):
        for row in conn.execute(
            "SELECT home, away FROM games WHERE season_type = 2 AND et_date BETWEEN ? AND ?",
            (w.start.isoformat(), w.end.isoformat()),
        ):
            for team in (row["home"], row["away"]):
                out.setdefault(team, [0] * len(weeks))[i] += 1
    return out


@dataclass(frozen=True)
class PlayoffSchedule:
    weeks: list[FantasyWeek]
    team_games: dict[str, list[int]]

    @property
    def totals(self) -> dict[str, int]:
        return {t: sum(g) for t, g in self.team_games.items()}

    @property
    def average_total(self) -> float:
        totals = list(self.totals.values())
        return sum(totals) / len(totals) if totals else 0.0

    def games(self, team: str) -> int:
        return self.totals.get(team, 0)


def playoff_schedule(
    conn: sqlite3.Connection, season: int, settings: Mapping[str, Any] | None = None
) -> PlayoffSchedule:
    weeks = playoff_weeks(fantasy_weeks(conn, season), settings)
    return PlayoffSchedule(weeks, team_games_by_week(conn, weeks))


def playoff_value(per_game_value: float, games: int, average_games: float) -> float:
    """Playoff-weeks value relative to an average schedule: value x games / average games."""
    return per_game_value * games / average_games if average_games else 0.0


def season_progress(today: date, season_start: date, playoff_start: date) -> float:
    """0 at opening night, 1 once the playoffs begin (used to weight playoff schedules in trades)."""
    span = (playoff_start - season_start).days
    if span <= 0:
        return 1.0
    return min(1.0, max(0.0, (today - season_start).days / span))
