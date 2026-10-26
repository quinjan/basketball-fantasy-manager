"""What actually happened in Yahoo: both teams' daily lineups and the FINAL weekly matchup results (read-only).

- Daily lineups (`team/<key>/roster;date=<day>`) go into `yahoo_daily_lineups` (ESPN id via the Yahoo->ESPN mapper).
  "Started" = any selected position other than BN / IL / IL+.
- Final matchup results (league scoreboard for a week: both teams' weekly stats + stat_winners) are cached in
  `yahoo_matchup_finals` (created here). A row is `final` only once Yahoo marks the matchup `postevent`.

Graders read these through `final_for` / `started_days`, or through any callable with the same signature as
`final_for` (a "finals provider"), so tests run offline.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, date, datetime, timedelta
from typing import Any

from fbm.analytics.categories import NINE_CAT
from fbm.yahoo.league_reads import Matchup, banked_stats, fetch_scoreboard
from fbm.yahoo.roster import get_roster

BENCH = ("BN", "IL", "IL+")

SCHEMA = """
CREATE TABLE IF NOT EXISTS yahoo_matchup_finals (
    week INTEGER NOT NULL,
    team_key TEXT NOT NULL,
    opponent_key TEXT,
    week_start TEXT NOT NULL,
    week_end TEXT NOT NULL,
    status TEXT NOT NULL,
    final INTEGER NOT NULL,
    stats TEXT NOT NULL,         -- JSON engine stat columns (pts, reb, ..., fgm, fga, ftm, fta)
    opp_stats TEXT NOT NULL,     -- JSON, same for the opponent
    stat_winners TEXT NOT NULL,  -- JSON {category key: 1 | 0.5 | 0} from team_key's side
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (week, team_key)
);
CREATE INDEX IF NOT EXISTS ix_matchup_finals_window ON yahoo_matchup_finals(week_start, week_end);
"""

# (day, team_key, opponent_key) -> final dict (see final_for) or None
FinalsProvider = Callable[[date, str | None, str | None], dict[str, Any] | None]
Mapper = Callable[[str, str, str], str | None]  # (yahoo_id, name, yahoo team abbr) -> ESPN id

_CAT_BY_STAT_ID = {c.yahoo_stat_id: c.key for c in NINE_CAT}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def ensure(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


# ------------------------------------------------------------------ daily lineups


def store_lineup(
    conn: sqlite3.Connection, team_key: str, day: date | str, rows: Iterable[tuple[str, str | None, str]]
) -> int:
    """rows: (player_key, espn_id, selected_position). Replaces that team-day."""
    rows = list(rows)
    conn.execute("DELETE FROM yahoo_daily_lineups WHERE team_key = ? AND et_date = ?", (team_key, str(day)))
    stamp = _now()
    conn.executemany(
        "INSERT INTO yahoo_daily_lineups(team_key, et_date, player_key, espn_id, selected_position, fetched_at) "
        "VALUES (?,?,?,?,?,?)",
        [(team_key, str(day), pk, eid, (pos or "BN").upper(), stamp) for pk, eid, pos in rows],
    )
    conn.commit()
    return len(rows)


def fetch_lineups(
    conn: sqlite3.Connection, client: Any, team_keys: Iterable[str], days: Iterable[date], mapper: Mapper
) -> int:
    """Read each team's lineup for each ET day from Yahoo and store it. Returns rows stored."""
    n = 0
    days = list(days)
    for tk in team_keys:
        for day in days:
            roster = get_roster(client, tk, day)
            rows = []
            for p in roster.players:
                yid = (p.player_key or "").rsplit(".p.", 1)[-1]
                rows.append((p.player_key, mapper(yid, p.name, p.nba_team), p.selected_position))
            n += store_lineup(conn, tk, day, rows)
    return n


def _pid(row: sqlite3.Row) -> str:
    return row["espn_id"] or f"yahoo:{row['player_key'].rsplit('.p.', 1)[-1]}"


def daily_lineups(conn: sqlite3.Connection, team_key: str, start: date, end: date) -> dict[str, dict[str, str]]:
    """{et_date: {espn_id: selected_position}} for the stored days in [start, end]."""
    out: dict[str, dict[str, str]] = {}
    for r in conn.execute(
        "SELECT * FROM yahoo_daily_lineups WHERE team_key = ? AND et_date BETWEEN ? AND ? ORDER BY et_date",
        (team_key, start.isoformat(), end.isoformat()),
    ):
        out.setdefault(r["et_date"], {})[_pid(r)] = r["selected_position"]
    return out


def started_days(conn: sqlite3.Connection, team_key: str, start: date, end: date) -> dict[str, set[str]]:
    """{et_date: {espn_id started}} (not BN/IL/IL+) for the stored days in [start, end]."""
    return {
        d: {pid for pid, pos in slots.items() if pos.upper() not in BENCH}
        for d, slots in daily_lineups(conn, team_key, start, end).items()
    }


def started(conn: sqlite3.Connection, team_key: str, day: date) -> set[str] | None:
    """Players started that day, or None when the lineup was never fetched."""
    return started_days(conn, team_key, day, day).get(day.isoformat())


# ------------------------------------------------------------------ final matchup results


def _winners(m: Matchup, team_key: str) -> dict[str, float]:
    out = {}
    for stat_id, winner in m.stat_winners.items():
        cat = _CAT_BY_STAT_ID.get(stat_id)
        if cat:
            out[cat] = 0.5 if winner is None else (1.0 if winner == team_key else 0.0)
    return out


def store_final(
    conn: sqlite3.Connection,
    *,
    week: int,
    team_key: str,
    opponent_key: str | None,
    week_start: date | str,
    week_end: date | str,
    me: Mapping[str, float],
    opp: Mapping[str, float],
    stat_winners: Mapping[str, float],
    status: str = "postevent",
) -> None:
    ensure(conn)
    conn.execute(
        "INSERT OR REPLACE INTO yahoo_matchup_finals(week, team_key, opponent_key, week_start, week_end, status, "
        "final, stats, opp_stats, stat_winners, fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            week,
            team_key,
            opponent_key,
            str(week_start),
            str(week_end),
            status,
            int(status == "postevent"),
            json.dumps(dict(me)),
            json.dumps(dict(opp)),
            json.dumps(dict(stat_winners)),
            _now(),
        ),
    )
    conn.commit()


def store_matchups(conn: sqlite3.Connection, matchups: Iterable[Matchup]) -> int:
    n = 0
    for m in matchups:
        if m.week is None or not m.week_start or not m.week_end or len(m.teams) != 2:
            continue
        a, b = m.teams
        for me, opp in ((a, b), (b, a)):
            store_final(
                conn,
                week=m.week,
                team_key=me.team_key,
                opponent_key=opp.team_key,
                week_start=m.week_start,
                week_end=m.week_end,
                me=banked_stats(me.stats),
                opp=banked_stats(opp.stats),
                stat_winners=_winners(m, me.team_key),
                status=m.status,
            )
            n += 1
    return n


def fetch_finals(conn: sqlite3.Connection, client: Any, league_key: str, week: int) -> int:
    """Read one week's scoreboard (both teams' weekly totals + stat_winners for every matchup) and cache it."""
    return store_matchups(conn, fetch_scoreboard(client, league_key, week))


def _decode(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "week": row["week"],
        "team_key": row["team_key"],
        "opponent_key": row["opponent_key"],
        "week_start": row["week_start"],
        "week_end": row["week_end"],
        "status": row["status"],
        "final": bool(row["final"]),
        "me": json.loads(row["stats"]),
        "opp": json.loads(row["opp_stats"]),
        "stat_winners": json.loads(row["stat_winners"]),
    }


def final_for(
    conn: sqlite3.Connection, day: date | str, team_key: str | None = None, opponent_key: str | None = None
) -> dict[str, Any] | None:
    """The cached matchup covering ET `day` for `team_key` (or, when unknown, the team that faced `opponent_key`).
    {week, team_key, opponent_key, week_start, week_end, status, final, me, opp, stat_winners}."""
    ensure(conn)
    sql = "SELECT * FROM yahoo_matchup_finals WHERE week_start <= ? AND week_end >= ?"
    args: list[Any] = [str(day), str(day)]
    if team_key:
        sql += " AND team_key = ?"
        args.append(team_key)
    elif opponent_key:
        sql += " AND opponent_key = ?"
        args.append(opponent_key)
    else:
        return None
    row = conn.execute(sql + " ORDER BY final DESC LIMIT 1", args).fetchone()
    return _decode(row) if row else None


def db_provider(conn: sqlite3.Connection) -> FinalsProvider:
    return lambda day, team_key, opponent_key: final_for(conn, day, team_key, opponent_key)


def cached_weeks(conn: sqlite3.Connection, final_only: bool = True) -> set[int]:
    ensure(conn)
    sql = "SELECT DISTINCT week FROM yahoo_matchup_finals" + (" WHERE final = 1" if final_only else "")
    return {int(r[0]) for r in conn.execute(sql)}


def sync_weeks(
    conn: sqlite3.Connection,
    client: Any,
    league_key: str,
    my_team_key: str,
    weeks: Iterable[int],
    mapper: Mapper,
) -> dict[str, Any]:
    """For each ended week not yet cached as final: cache the finals, then both teams' 7 daily lineups (my matchup).
    Network reads only (Yahoo, read-only)."""
    done = cached_weeks(conn)
    report: dict[str, Any] = {"weeks": [], "lineup_rows": 0}
    for wk in weeks:
        if wk in done:
            continue
        fetch_finals(conn, client, league_key, wk)
        row = conn.execute(
            "SELECT * FROM yahoo_matchup_finals WHERE week = ? AND team_key = ?", (wk, my_team_key)
        ).fetchone()
        if not row:
            continue
        start, end = date.fromisoformat(row["week_start"]), date.fromisoformat(row["week_end"])
        days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
        keys = [k for k in (my_team_key, row["opponent_key"]) if k]
        report["lineup_rows"] += fetch_lineups(conn, client, keys, days, mapper)
        report["weeks"].append({"week": wk, "final": bool(row["final"])})
    return report
