"""Read helpers over the SQLite store, used by analytics and the CLI."""

from __future__ import annotations

import sqlite3
from collections import Counter
from datetime import date


def games_on(conn: sqlite3.Connection, day: date) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM games WHERE et_date = ? AND season_type = 2 ORDER BY start_utc", (day.isoformat(),)
    ).fetchall()


def team_game_counts(conn: sqlite3.Connection, start: date, end: date) -> Counter[str]:
    """NBA games per team in [start, end] (ET dates, inclusive)."""
    counts: Counter[str] = Counter()
    for row in conn.execute(
        "SELECT home, away FROM games WHERE et_date BETWEEN ? AND ? AND season_type = 2",
        (start.isoformat(), end.isoformat()),
    ):
        counts[row["home"]] += 1
        counts[row["away"]] += 1
    return counts


def player_lines(conn: sqlite3.Connection, espn_id: str, season: int, played_only: bool = True) -> list[sqlite3.Row]:
    sql = "SELECT * FROM box_lines WHERE espn_id = ? AND season = ?"
    if played_only:
        sql += " AND played = 1"
    return conn.execute(sql + " ORDER BY et_date", (espn_id, season)).fetchall()


def injury(conn: sqlite3.Connection, espn_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM injuries_current WHERE espn_id = ?", (espn_id,)).fetchone()


def find_players(conn: sqlite3.Connection, name_fragment: str, limit: int = 10) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM players WHERE name LIKE ? ORDER BY name LIMIT ?", (f"%{name_fragment}%", limit)
    ).fetchall()


def store_status(conn: sqlite3.Connection) -> dict[str, object]:
    def scalar(sql: str, *args: object) -> object:
        return conn.execute(sql, args).fetchone()[0]

    seasons = [
        dict(r)
        for r in conn.execute(
            "SELECT season, COUNT(*) AS games, SUM(completed) AS completed, SUM(box_synced) AS boxed, "
            "MIN(et_date) AS first, MAX(et_date) AS last FROM games WHERE season_type = 2 GROUP BY season"
        )
    ]
    return {
        "teams": scalar("SELECT COUNT(*) FROM nba_teams"),
        "players": scalar("SELECT COUNT(*) FROM players"),
        "box_lines": scalar("SELECT COUNT(*) FROM box_lines"),
        "season_averages": [dict(r) for r in conn.execute(
            "SELECT season, COUNT(*) AS players FROM season_averages GROUP BY season")],
        "injuries": scalar("SELECT COUNT(*) FROM injuries_current"),
        "news": scalar("SELECT COUNT(*) FROM news"),
        "seasons": seasons,
        "sync_log": [dict(r) for r in conn.execute("SELECT * FROM sync_log ORDER BY job")],
    }
