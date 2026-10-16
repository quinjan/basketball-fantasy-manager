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
        "SELECT * FROM games WHERE season = ? AND completed = 1 AND box_synced = 0 AND et_date <= ? "
        "ORDER BY et_date",
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
            except FetchError:
                result.failures += 1
                continue
            if summary_is_final(summary):
                db.store_boxscore(conn, row, parse_boxscore(summary, row["event_id"]))
                result.count += 1
            if done % 25 == 0:
                conn.commit()
            if progress:
                progress(done, total)
    db.log_sync(conn, f"boxscores:{season}", str(result))
    conn.commit()
    return result


def sync_season_averages(conn: sqlite3.Connection, espn: EspnClient, season: int, today: date) -> SyncResult:
    avgs = espn.season_averages(season)
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


def daily_sync(conn: sqlite3.Connection, espn: EspnClient, today: date, progress: Progress | None = None
               ) -> list[SyncResult]:
    """Everything an in-season run needs: schedule status, new box scores, averages, injuries, news."""
    season = current_season(today)
    results = [sync_schedule(conn, espn, season, ttl=1800)]
    results.append(sync_boxscores(conn, espn, season, through=today, progress=progress))
    played = conn.execute("SELECT COUNT(*) FROM games WHERE season = ? AND completed = 1", (season,)).fetchone()[0]
    if played:
        results.append(sync_season_averages(conn, espn, season, today))
    inj, _changes = sync_injuries(conn, espn)
    results.append(inj)
    results.append(sync_news(conn, espn))
    return results


def backfill_season(conn: sqlite3.Connection, espn: EspnClient, season: int, today: date, workers: int = 4,
                    progress: Progress | None = None) -> list[SyncResult]:
    """One-time history load for a completed season (priors + per-game variance for G-scores)."""
    results = [sync_schedule(conn, espn, season, ttl=30 * 86400)]
    results.append(sync_boxscores(conn, espn, season, through=today, workers=workers, progress=progress))
    results.append(sync_season_averages(conn, espn, season, today))
    return results
