"""Sync jobs: ESPN -> SQLite. Idempotent; safe to re-run."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date

from fbm.data.espn import REGULAR_SEASON, EspnClient, current_season, parse_boxscore, summary_is_final
from fbm.data.http import FetchError
from fbm.store import db

Progress = Callable[[int, int], None]


@dataclass
class SyncResult:
    job: str
    count: int = 0
    failures: int = 0
    notes: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        extra = f", {self.failures} failed" if self.failures else ""
        notes = f" ({'; '.join(self.notes)})" if self.notes else ""
        return f"{self.job}: {self.count}{extra}{notes}"


def sync_teams(conn: sqlite3.Connection, espn: EspnClient) -> SyncResult:
    n = db.upsert_teams(conn, espn.teams())
    db.log_sync(conn, "teams", f"{n} teams")
    conn.commit()
    return SyncResult("teams", n)


def _team_ids(conn: sqlite3.Connection, espn: EspnClient) -> list[str]:
    ids = [r["espn_id"] for r in conn.execute("SELECT espn_id FROM nba_teams")]
    if not ids:
        sync_teams(conn, espn)
        ids = [r["espn_id"] for r in conn.execute("SELECT espn_id FROM nba_teams")]
    return ids


def sync_schedule(
    conn: sqlite3.Connection, espn: EspnClient, season: int, season_type: int = REGULAR_SEASON, ttl: float = 3 * 3600
) -> SyncResult:
    result = SyncResult(f"schedule {season}")
    games = {}
    for team_id in _team_ids(conn, espn):
        try:
            for g in espn.team_schedule(team_id, season, season_type, ttl=ttl):
                games[g.event_id] = g
        except FetchError as err:
            result.failures += 1
            result.notes.append(str(err)[:80])
    result.count = db.upsert_games(conn, games.values())
    db.log_sync(conn, f"schedule:{season}", str(result))
    conn.commit()
    return result


def pending_box_games(conn: sqlite3.Connection, season: int, through: date) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM games WHERE season = ? AND completed = 1 AND box_synced = 0 AND et_date <= ? ORDER BY et_date",
        (season, through.isoformat()),
    ).fetchall()


def sync_boxscores(
    conn: sqlite3.Connection,
    espn: EspnClient,
    season: int,
    through: date,
    workers: int = 4,
    limit: int | None = None,
    progress: Progress | None = None,
) -> SyncResult:
    rows = pending_box_games(conn, season, through)
    if limit is not None:
        rows = rows[:limit]
    result = SyncResult(f"boxscores {season}")
    total = len(rows)
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(espn.summary, r["event_id"]): r for r in rows}
        for done, fut in enumerate(as_completed(futures), 1):
            row = futures[fut]
            try:
                summary = fut.result()
                if summary_is_final(summary):
                    db.store_boxscore(conn, row, parse_boxscore(summary, row["event_id"]))
                    result.count += 1
            except Exception as err:  # one bad game must not abort a long backfill
                result.failures += 1
                if len(result.notes) < 3:
                    result.notes.append(f"{row['event_id']}: {type(err).__name__}: {str(err)[:60]}")
            if done % 25 == 0:
                conn.commit()
            if progress:
                progress(done, total)
    db.log_sync(conn, f"boxscores:{season}", str(result))
    conn.commit()
    return result


def sync_season_averages(conn: sqlite3.Connection, espn: EspnClient, season: int, today: date) -> SyncResult:
    avgs = espn.season_averages(season)
    if not avgs:  # ESPN has no stats for this season yet: anything stored for it came from a season fallback
        conn.execute("DELETE FROM season_averages WHERE season = ?", (season,))
        db.log_sync(conn, f"averages:{season}", "0 players (ESPN has no stats for this season yet)")
        conn.commit()
        return SyncResult(f"season averages {season}", 0, notes=["ESPN has no stats for this season yet"])
    as_of = min(today, date(season, 6, 30))
    n = db.upsert_season_averages(conn, avgs, as_of)
    db.log_sync(conn, f"averages:{season}", f"{n} players")
    conn.commit()
    return SyncResult(f"season averages {season}", n)


def sync_injuries(conn: sqlite3.Connection, espn: EspnClient) -> tuple[SyncResult, list[dict[str, str]]]:
    injuries = espn.injuries()
    changes = db.replace_injuries(conn, injuries)
    db.log_sync(conn, "injuries", f"{len(injuries)} listed, {len(changes)} changes")
    conn.commit()
    return SyncResult("injuries", len(injuries), notes=[f"{len(changes)} status changes"]), changes


def sync_news(conn: sqlite3.Connection, espn: EspnClient, limit: int = 50) -> SyncResult:
    n = db.upsert_news(conn, espn.news(limit))
    db.log_sync(conn, "news", f"{n} fetched")
    conn.commit()
    return SyncResult("news", n)


def sync_fantasy_meta(conn: sqlite3.Connection, espn: EspnClient, season: int) -> SyncResult:
    from fbm.data import espn_fantasy

    n = espn_fantasy.store(conn, season, espn_fantasy.fetch(espn.fetcher, season))
    return SyncResult(f"fantasy meta {season}", n)


def sync_espn_projections(conn: sqlite3.Connection, espn: EspnClient, season: int, snapshot_date: date) -> SyncResult:
    """Daily snapshot of ESPN's season projections (the last pre-opening-night snapshot is the preseason baseline)."""
    from fbm.constants import C
    from fbm.data import espn_fantasy

    team_abbr = {r["espn_id"]: r["abbr"] for r in conn.execute("SELECT espn_id, abbr FROM nba_teams")}
    if not team_abbr:
        sync_teams(conn, espn)
        team_abbr = {r["espn_id"]: r["abbr"] for r in conn.execute("SELECT espn_id, abbr FROM nba_teams")}
    ttl = float(C("projection.espn_snapshot_hours")) * 3600
    lines = espn_fantasy.fetch_projections(espn.fetcher, season, team_abbr, ttl=ttl)
    n = espn_fantasy.store_projections(conn, season, snapshot_date.isoformat(), lines)
    return SyncResult(f"espn projections {season}", n, notes=[f"snapshot {snapshot_date}"])


MIN_LEAGUE_ROSTER_PLAYERS = 360  # 30 teams x 12; fewer means a partial/odd ESPN response: don't mark anyone teamless


def sync_rosters(conn: sqlite3.Connection, espn: EspnClient, today: date) -> SyncResult:
    """Current NBA team rosters: each player's team now, and who has no NBA team (waived/unsigned)."""
    result = SyncResult("rosters")
    teams = {r["espn_id"]: r["abbr"] for r in conn.execute("SELECT espn_id, abbr FROM nba_teams")}
    if not teams:
        sync_teams(conn, espn)
        teams = {r["espn_id"]: r["abbr"] for r in conn.execute("SELECT espn_id, abbr FROM nba_teams")}
    rosters: dict[str, list[tuple[str, str, str]]] = {}
    for team_id, abbr in teams.items():
        try:
            rosters[abbr] = espn.team_roster(team_id)
        except FetchError as err:
            result.failures += 1
            result.notes.append(f"{abbr}: {str(err)[:60]}")
    total = sum(len(v) for v in rosters.values())
    complete = not result.failures and total >= MIN_LEAGUE_ROSTER_PLAYERS
    rostered, marked = db.apply_rosters(conn, rosters, today, complete)
    result.count = rostered
    result.notes.append(f"{marked} without an NBA team" if complete else "incomplete: no-team marking skipped")
    db.log_sync(conn, "rosters", str(result))
    conn.commit()
    return result


def daily_sync(
    conn: sqlite3.Connection, espn: EspnClient, today: date, progress: Progress | None = None
) -> list[SyncResult]:
    """Everything an in-season run needs: schedule, box scores, averages, injuries, news, rosters, ADP/eligibility."""
    season = current_season(today)
    results = [sync_schedule(conn, espn, season, ttl=1800)]
    results.append(sync_boxscores(conn, espn, season, through=today, progress=progress))
    played = conn.execute("SELECT COUNT(*) FROM games WHERE season = ? AND completed = 1", (season,)).fetchone()[0]
    if played:
        results.append(sync_season_averages(conn, espn, season, today))
    inj, _changes = sync_injuries(conn, espn)
    results.append(inj)
    results.append(sync_news(conn, espn))
    if hasattr(espn, "team_roster"):
        try:
            results.append(sync_rosters(conn, espn, today))
        except Exception as err:  # never block the daily sync; the last roster sync stays in effect
            results.append(SyncResult("rosters", failures=1, notes=[str(err)[:80]]))
    if getattr(espn, "fetcher", None) is not None:
        try:
            results.append(sync_fantasy_meta(conn, espn, season))
        except Exception as err:  # optional enrichment; never block the daily sync
            results.append(SyncResult(f"fantasy meta {season}", failures=1, notes=[str(err)[:80]]))
        try:
            from fbm.timeutil import nba_today

            results.append(sync_espn_projections(conn, espn, season, nba_today()))
        except Exception as err:  # optional enrichment; never block the daily sync
            results.append(SyncResult(f"espn projections {season}", failures=1, notes=[str(err)[:80]]))
    return results


def backfill_season(
    conn: sqlite3.Connection,
    espn: EspnClient,
    season: int,
    today: date,
    workers: int = 4,
    progress: Progress | None = None,
) -> list[SyncResult]:
    """One-time history load for a completed season (priors + per-game variance for G-scores)."""
    results = [sync_schedule(conn, espn, season, ttl=30 * 86400)]
    results.append(sync_boxscores(conn, espn, season, through=today, workers=workers, progress=progress))
    results.append(sync_season_averages(conn, espn, season, today))
    return results
