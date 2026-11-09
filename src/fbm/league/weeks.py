"""Which fantasy week is it? One pure resolver so the week number, its dates and the opponent always agree.

Yahoo keeps reporting a finished week as current (status `midevent`) for a while after its last day (e.g. Monday ET
before it rolls over), while the calendar already says the next week. `effective_week` settles that from today's ET
date plus Yahoo's own week bounds: past the end of Yahoo's week, the effective week is the next Yahoo week (its
bounds and opponent come from the Yahoo schedule via `lookup`; the Monday-Sunday calendar is only the fallback).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, timedelta

from fbm.analytics.week import fantasy_week
from fbm.yahoo.league_reads import Matchup

MAX_ROLLS = 4  # a snapshot older than this many weeks is not worth rolling by schedule lookups


@dataclass(frozen=True)
class WeekInfo:
    number: int
    start: date
    end: date
    opponent_key: str | None = None
    source: str = "yahoo"  # yahoo (snapshot) | yahoo schedule | calendar (Monday-Sunday after the previous week)


@dataclass(frozen=True)
class EffectiveWeek:
    week: WeekInfo
    rolled_from: WeekInfo | None  # Yahoo's reported (finished) week when `week` is the next one; else None
    note: str = ""


WeekLookup = Callable[[int], "WeekInfo | None"]  # Yahoo schedule: week number -> its info, None when unavailable


def week_info_from_matchups(week: int, matchups: Iterable[Matchup], my_team_key: str) -> WeekInfo | None:
    """Bounds from any matchup of that Yahoo week (they share them); the opponent from mine (None on a bye)."""
    matchups = list(matchups)
    dated = next((m for m in matchups if m.week_start and m.week_end), None)
    if dated is None or dated.week_start is None or dated.week_end is None:
        return None
    mine = next((m for m in matchups if m.team(my_team_key)), None)
    opp = mine.opponent_of(my_team_key) if mine else None
    return WeekInfo(week, dated.week_start, dated.week_end, opp.team_key if opp else None, "yahoo schedule")


def calendar_week_after(prev: WeekInfo) -> WeekInfo:
    start = prev.end + timedelta(days=1)
    return WeekInfo(prev.number + 1, start, fantasy_week(start)[1], None, "calendar")


def next_week_info(week: WeekInfo, lookup: WeekLookup | None = None) -> WeekInfo:
    """The week after `week`: Yahoo's schedule entry when it is consistent (right number, starts after `week` ends),
    else the Monday-Sunday calendar week."""
    info = lookup(week.number + 1) if lookup else None
    if info and info.number == week.number + 1 and info.start > week.end and info.end >= info.start:
        return info
    return calendar_week_after(week)


def effective_week(yahoo: WeekInfo, today: date, lookup: WeekLookup | None = None) -> EffectiveWeek:
    """The fantasy week `today` (ET) belongs to, given Yahoo's reported current week."""
    week = yahoo
    for _ in range(MAX_ROLLS):
        if today <= week.end:
            break
        week = next_week_info(week, lookup)
    if week is yahoo:
        return EffectiveWeek(yahoo, None)
    note = (
        f"Yahoo still reports week {yahoo.number} ({yahoo.start}..{yahoo.end}) as current and has not rolled over; "
        f"FBM treats week {week.number} ({week.start}..{week.end}) as this week "
        + (
            "(Yahoo schedule)"
            if week.source == "yahoo schedule"
            else "(Monday-Sunday calendar: Yahoo's schedule was unavailable, so the opponent is unknown)"
        )
        + "."
    )
    return EffectiveWeek(week, yahoo, note)
