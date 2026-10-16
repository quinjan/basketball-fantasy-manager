"""SQLite store (data/fbm.db). Stdlib sqlite3, schema migrated on connect."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import asdict
from datetime import UTC, date, datetime
from pathlib import Path

from fbm.config import DATA_DIR
from fbm.data.espn import BoxLine, Game, Injury, NewsItem, SeasonAverage, Team

DB_PATH = DATA_DIR / "fbm.db"
SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS nba_teams (
    espn_id TEXT PRIMARY KEY,
    abbr TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS games (
    event_id TEXT PRIMARY KEY,
    season INTEGER NOT NULL,
    season_type INTEGER NOT NULL,
    start_utc TEXT NOT NULL,
    et_date TEXT NOT NULL,
    home TEXT NOT NULL,
    away TEXT NOT NULL,
    state TEXT NOT NULL,
    completed INTEGER NOT NULL,
    neutral INTEGER NOT NULL,
    time_valid INTEGER NOT NULL,
    box_synced INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_games_date ON games(et_date);
CREATE INDEX IF NOT EXISTS ix_games_season ON games(season, season_type);

CREATE TABLE IF NOT EXISTS players (
    espn_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    team TEXT,
    team_as_of TEXT NOT NULL DEFAULT '',
    position TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS box_lines (
    event_id TEXT NOT NULL,
    espn_id TEXT NOT NULL,
    et_date TEXT NOT NULL,
    season INTEGER NOT NULL,
    team TEXT NOT NULL,
    starter INTEGER NOT NULL,
    played INTEGER NOT NULL,
    dnp_reason TEXT,
    minutes REAL, pts INTEGER, fgm INTEGER, fga INTEGER, tpm INTEGER, tpa INTEGER,
    ftm INTEGER, fta INTEGER, reb INTEGER, ast INTEGER, tov INTEGER, stl INTEGER, blk INTEGER,
    PRIMARY KEY (event_id, espn_id)
);
CREATE INDEX IF NOT EXISTS ix_box_player ON box_lines(espn_id, et_date);

CREATE TABLE IF NOT EXISTS season_averages (
    season INTEGER NOT NULL,
    espn_id TEXT NOT NULL,
    name TEXT, team TEXT, position TEXT,
    gp INTEGER, minutes REAL, pts REAL, fgm REAL, fga REAL, tpm REAL, tpa REAL,
    ftm REAL, fta REAL, reb REAL, ast REAL, tov REAL, stl REAL, blk REAL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (season, espn_id)
);

CREATE TABLE IF NOT EXISTS injuries_current (
    espn_id TEXT PRIMARY KEY,
    name TEXT, team TEXT, status TEXT, fantasy_status TEXT,
    short_comment TEXT, long_comment TEXT, reported_at TEXT, return_date TEXT, injury_type TEXT,
    fetched_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS injury_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    espn_id TEXT NOT NULL,
    name TEXT,
    team TEXT,
    old_status TEXT,
    new_status TEXT,
    comment TEXT,
    observed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS news (
    news_id TEXT PRIMARY KEY,
    published TEXT,
    headline TEXT,
    description TEXT,
    athlete_ids TEXT,
    link TEXT,
    fetched_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_news_published ON news(published);

CREATE TABLE IF NOT EXISTS sync_log (
    job TEXT PRIMARY KEY,
    last_run TEXT NOT NULL,
    detail TEXT
);

CREATE TABLE IF NOT EXISTS proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    payload TEXT NOT NULL,
    rationale TEXT NOT NULL,
    evidence TEXT,
    status TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    review_verdict TEXT,
    review_confidence REAL,
    review_notes TEXT,
    review_model TEXT,
    decided_at TEXT,
    owner_quote TEXT,
    executed_at TEXT,
    execution TEXT
);
CREATE INDEX IF NOT EXISTS ix_proposals_status ON proposals(status);
CREATE TABLE IF NOT EXISTS proposal_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    proposal_id INTEGER NOT NULL REFERENCES proposals(id),
    at TEXT NOT NULL,
    event TEXT NOT NULL,
    detail TEXT
);
CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    channel TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    status TEXT NOT NULL,
    detail TEXT
);

CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    kind TEXT NOT NULL,
    window_start TEXT NOT NULL,
    window_end TEXT NOT NULL,
    team_key TEXT NOT NULL,
    opponent_key TEXT NOT NULL,
    source TEXT NOT NULL,
    payload TEXT NOT NULL,
    graded_at TEXT,
    result TEXT
);
CREATE INDEX IF NOT EXISTS ix_predictions_window ON predictions(window_end, graded_at);

CREATE TABLE IF NOT EXISTS player_meta (
    espn_id TEXT PRIMARY KEY,
    season INTEGER NOT NULL,
    name TEXT,
    adp REAL,
    rank_standard INTEGER,
    rank_roto INTEGER,
    eligible TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def connect(path: Path | str = DB_PATH) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()


def upsert_teams(conn: sqlite3.Connection, teams: Iterable[Team]) -> int:
    rows = [(t.espn_id, t.abbr, t.name) for t in teams]
    conn.executemany(
        "INSERT INTO nba_teams(espn_id, abbr, name) VALUES (?,?,?) "
        "ON CONFLICT(espn_id) DO UPDATE SET abbr=excluded.abbr, name=excluded.name",
        rows,
    )
    return len(rows)


def upsert_games(conn: sqlite3.Connection, games: Iterable[Game]) -> int:
    stamp = now_iso()
    rows = [
        (
            g.event_id,
            g.season,
            g.season_type,
            g.start_utc.isoformat(),
            g.et_date.isoformat(),
            g.home,
            g.away,
            g.state,
            int(g.completed),
            int(g.neutral),
            int(g.time_valid),
            stamp,
        )
        for g in games
    ]
    conn.executemany(
        """INSERT INTO games(event_id, season, season_type, start_utc, et_date, home, away, state, completed,
                             neutral, time_valid, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(event_id) DO UPDATE SET
             season=excluded.season, season_type=excluded.season_type, start_utc=excluded.start_utc,
             et_date=excluded.et_date, home=excluded.home, away=excluded.away, state=excluded.state,
             completed=excluded.completed, neutral=excluded.neutral, time_valid=excluded.time_valid,
             updated_at=excluded.updated_at""",
        rows,
    )
    return len(rows)


_PLAYER_UPSERT = """
INSERT INTO players(espn_id, name, team, team_as_of, position, updated_at) VALUES (?,?,?,?,?,?)
ON CONFLICT(espn_id) DO UPDATE SET
  name = excluded.name,
  team = CASE WHEN excluded.team_as_of >= players.team_as_of THEN excluded.team ELSE players.team END,
  team_as_of = MAX(excluded.team_as_of, players.team_as_of),
  position = COALESCE(NULLIF(excluded.position, ''), players.position),
  updated_at = excluded.updated_at
"""


def store_boxscore(conn: sqlite3.Connection, game_row: sqlite3.Row, lines: list[BoxLine]) -> int:
    stamp = now_iso()
    conn.executemany(_PLAYER_UPSERT, [(b.espn_id, b.name, b.team, game_row["et_date"], "", stamp) for b in lines])
    conn.executemany(
        """INSERT OR REPLACE INTO box_lines(event_id, espn_id, et_date, season, team, starter, played, dnp_reason,
             minutes, pts, fgm, fga, tpm, tpa, ftm, fta, reb, ast, tov, stl, blk)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [
            (
                b.event_id,
                b.espn_id,
                game_row["et_date"],
                game_row["season"],
                b.team,
                int(b.starter),
                int(b.played),
                b.dnp_reason,
                b.minutes,
                b.pts,
                b.fgm,
                b.fga,
                b.tpm,
                b.tpa,
                b.ftm,
                b.fta,
                b.reb,
                b.ast,
                b.tov,
                b.stl,
                b.blk,
            )
            for b in lines
        ],
    )
    conn.execute("UPDATE games SET box_synced = 1 WHERE event_id = ?", (game_row["event_id"],))
    return len(lines)


def upsert_season_averages(conn: sqlite3.Connection, avgs: Iterable[SeasonAverage], as_of: date) -> int:
    """`as_of` is the date the team affiliation reflects (today for the live season, season end otherwise)."""
    stamp = now_iso()
    rows = []
    for a in avgs:
        d = asdict(a)
        rows.append(
            (
                d["season"],
                d["espn_id"],
                d["name"],
                d["team"],
                d["position"],
                d["gp"],
                d["minutes"],
                d["pts"],
                d["fgm"],
                d["fga"],
                d["tpm"],
                d["tpa"],
                d["ftm"],
                d["fta"],
                d["reb"],
                d["ast"],
                d["tov"],
                d["stl"],
                d["blk"],
                stamp,
            )
        )
    conn.executemany(
        """INSERT OR REPLACE INTO season_averages(season, espn_id, name, team, position, gp, minutes, pts, fgm, fga,
             tpm, tpa, ftm, fta, reb, ast, tov, stl, blk, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        rows,
    )
    conn.executemany(_PLAYER_UPSERT, [(r[1], r[2], r[3], as_of.isoformat(), r[4], stamp) for r in rows])
    return len(rows)


def replace_injuries(conn: sqlite3.Connection, injuries: Iterable[Injury]) -> list[dict[str, str]]:
    """Replace the current injury snapshot; record and return status changes (new, changed, cleared)."""
    stamp = now_iso()
    new = {i.espn_id: i for i in injuries if i.espn_id}
    old = {r["espn_id"]: r for r in conn.execute("SELECT * FROM injuries_current")}
    changes: list[dict[str, str]] = []
    for pid, inj in new.items():
        prev = old.get(pid)
        if prev is None or prev["status"] != inj.status:
            changes.append(
                {
                    "espn_id": pid,
                    "name": inj.name,
                    "team": inj.team,
                    "old_status": prev["status"] if prev else "Healthy",
                    "new_status": inj.status,
                    "comment": inj.short_comment,
                }
            )
    for pid, prev in old.items():
        if pid not in new:
            changes.append(
                {
                    "espn_id": pid,
                    "name": prev["name"],
                    "team": prev["team"],
                    "old_status": prev["status"],
                    "new_status": "Cleared",
                    "comment": "",
                }
            )
    conn.execute("DELETE FROM injuries_current")
    conn.executemany(
        """INSERT INTO injuries_current(espn_id, name, team, status, fantasy_status, short_comment, long_comment,
             reported_at, return_date, injury_type, fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        [
            (
                i.espn_id,
                i.name,
                i.team,
                i.status,
                i.fantasy_status,
                i.short_comment,
                i.long_comment,
                i.reported_at,
                i.return_date,
                i.injury_type,
                stamp,
            )
            for i in new.values()
        ],
    )
    conn.executemany(
        "INSERT INTO injury_events(espn_id, name, team, old_status, new_status, comment, observed_at) "
        "VALUES (?,?,?,?,?,?,?)",
        [(c["espn_id"], c["name"], c["team"], c["old_status"], c["new_status"], c["comment"], stamp) for c in changes],
    )
    return changes


def upsert_news(conn: sqlite3.Connection, items: Iterable[NewsItem]) -> int:
    stamp = now_iso()
    rows = [(n.news_id, n.published, n.headline, n.description, ",".join(n.athlete_ids), n.link, stamp) for n in items]
    conn.executemany(
        "INSERT OR IGNORE INTO news(news_id, published, headline, description, athlete_ids, link, fetched_at) "
        "VALUES (?,?,?,?,?,?,?)",
        rows,
    )
    return len(rows)


def log_sync(conn: sqlite3.Connection, job: str, detail: str) -> None:
    conn.execute(
        "INSERT INTO sync_log(job, last_run, detail) VALUES (?,?,?) "
        "ON CONFLICT(job) DO UPDATE SET last_run=excluded.last_run, detail=excluded.detail",
        (job, now_iso(), detail),
    )
