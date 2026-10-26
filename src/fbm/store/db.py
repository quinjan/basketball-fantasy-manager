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
SCHEMA_VERSION = 2

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

CREATE TABLE IF NOT EXISTS yahoo_adp (
    yahoo_id TEXT PRIMARY KEY,
    espn_id TEXT,
    name TEXT,
    average_pick REAL,
    average_round REAL,
    average_cost REAL,
    percent_drafted REAL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS league_transactions (
    tx_key TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    status TEXT,
    ts_utc TEXT,
    trader_team_key TEXT,
    tradee_team_key TEXT,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS league_transaction_players (
    tx_key TEXT NOT NULL REFERENCES league_transactions(tx_key) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    player_key TEXT,
    player_id TEXT,
    name TEXT,
    nba_team TEXT,
    action TEXT,
    source_type TEXT,
    source_team_key TEXT,
    destination_type TEXT,
    destination_team_key TEXT,
    PRIMARY KEY (tx_key, seq)
);

CREATE TABLE IF NOT EXISTS espn_projections (
    season INTEGER NOT NULL,
    snapshot_date TEXT NOT NULL,
    espn_id TEXT NOT NULL,
    name TEXT, team TEXT, position TEXT,
    gp REAL, minutes REAL, pts REAL, fgm REAL, fga REAL, tpm REAL, tpa REAL,
    ftm REAL, fta REAL, reb REAL, ast REAL, tov REAL, stl REAL, blk REAL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (season, snapshot_date, espn_id)
);
CREATE INDEX IF NOT EXISTS ix_espn_proj_player ON espn_projections(espn_id, snapshot_date);

CREATE TABLE IF NOT EXISTS projection_log (
    season INTEGER NOT NULL,
    as_of TEXT NOT NULL,
    source TEXT NOT NULL,
    espn_id TEXT NOT NULL,
    minutes REAL, pts REAL, fgm REAL, fga REAL, tpm REAL, tpa REAL,
    ftm REAL, fta REAL, reb REAL, ast REAL, tov REAL, stl REAL, blk REAL,
    PRIMARY KEY (as_of, source, espn_id)
);

CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    season INTEGER NOT NULL,
    key TEXT NOT NULL,
    kind TEXT NOT NULL,
    direction TEXT NOT NULL,
    espn_id TEXT NOT NULL,
    name TEXT,
    team TEXT,
    strength REAL,
    confidence TEXT,
    reasons TEXT,
    note TEXT,
    evidence TEXT,
    first_seen TEXT NOT NULL,
    first_seen_et TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    pct_owned_at REAL,
    graded_at TEXT,
    result TEXT,
    basis TEXT
);
CREATE INDEX IF NOT EXISTS ix_signals_key ON signals(key, last_seen);
CREATE INDEX IF NOT EXISTS ix_signals_seen ON signals(last_seen);

CREATE TABLE IF NOT EXISTS model_params (
    key TEXT PRIMARY KEY,
    value REAL NOT NULL,
    detail TEXT,
    updated_at TEXT NOT NULL
);

-- ---------------------------------------------------------------- Hashtag Basketball (HT) imports
-- Written by fbm.hashtag (importer); read through fbm.hashtag.store. Personal use only, never republished.

CREATE TABLE IF NOT EXISTS hashtag_players (
    ht_id TEXT PRIMARY KEY,
    espn_id TEXT,
    name TEXT NOT NULL,
    team TEXT,
    match_method TEXT NOT NULL,          -- exact | fuzzy | override | unmatched
    updated_at TEXT NOT NULL
);

-- One row per player per HT projection view per import day. Per-game averages (HT "Averages", type H2H).
CREATE TABLE IF NOT EXISTS hashtag_projections (
    as_of TEXT NOT NULL,                 -- ET date of the import (YYYY-MM-DD)
    view TEXT NOT NULL,                  -- ROS | BLEND7 | BLEND14 | BLEND30 | STREAM7 | STREAM14 | STREAM30
    ht_id TEXT NOT NULL,
    espn_id TEXT,                        -- NULL when unmatched
    name TEXT, team TEXT, positions TEXT,
    rank INTEGER, adp REAL, gp REAL, minutes REAL,
    fgm REAL, fga REAL, ftm REAL, fta REAL, tpm REAL, pts REAL, reb REAL, ast REAL, stl REAL, blk REAL, tov REAL,
    z_fg_pct REAL, z_ft_pct REAL, z_tpm REAL, z_pts REAL, z_reb REAL, z_ast REAL, z_stl REAL, z_blk REAL, z_tov REAL,
    total REAL,
    note TEXT,                           -- HT's short player note/flag, untrusted text
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (as_of, view, ht_id)
);
CREATE INDEX IF NOT EXISTS ix_ht_proj_view ON hashtag_projections(view, as_of);
CREATE INDEX IF NOT EXISTS ix_ht_proj_espn ON hashtag_projections(espn_id, as_of);

-- H2H Matchup Planner, long format: one row per category per pull. before = banked this week (HT's copy of Yahoo),
-- after = banked + HT's projection for the rest of the week. ht_share = HT's displayed "probability" (a share formula,
-- reference only; official odds = HT totals + FBM simulator spread).
CREATE TABLE IF NOT EXISTS hashtag_matchup_totals (
    pulled_at TEXT NOT NULL,             -- UTC ISO timestamp
    et_date TEXT NOT NULL,
    week INTEGER NOT NULL,
    view TEXT NOT NULL,
    team_key TEXT NOT NULL,
    opponent_key TEXT NOT NULL,
    category TEXT NOT NULL,              -- FBM category key: fg_pct ft_pct tpm pts reb ast stl blk tov
    me_before REAL, opp_before REAL, me_after REAL, opp_after REAL,
    ht_share REAL,
    PRIMARY KEY (pulled_at, view, category)
);
CREATE INDEX IF NOT EXISTS ix_ht_matchup ON hashtag_matchup_totals(week, opponent_key, et_date);

-- Available players from HT's league Waiver Wire, run with the multipliers FBM set (weights JSON, sorted keys).
CREATE TABLE IF NOT EXISTS hashtag_waiver (
    as_of TEXT NOT NULL,
    view TEXT NOT NULL,
    weights TEXT NOT NULL,
    rank INTEGER NOT NULL,
    ht_id TEXT NOT NULL,
    espn_id TEXT,
    name TEXT,
    total REAL,
    data TEXT NOT NULL,                  -- JSON: per-game stats, per-category values, positions, team
    PRIMARY KEY (as_of, view, weights, ht_id)
);

CREATE TABLE IF NOT EXISTS hashtag_trade_ideas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pulled_at TEXT NOT NULL,
    settings TEXT NOT NULL,              -- JSON: view, multipliers, fairness, partner filter
    partner_team_key TEXT,
    partner_name TEXT,
    give TEXT NOT NULL,                  -- JSON list of {ht_id, espn_id, name}
    get TEXT NOT NULL,
    my_delta REAL,
    their_delta REAL,
    data TEXT                            -- JSON: anything else HT shows for the idea
);

CREATE TABLE IF NOT EXISTS hashtag_team_ranks (
    as_of TEXT NOT NULL,
    view TEXT NOT NULL,
    team_key TEXT,
    team_name TEXT NOT NULL,
    category TEXT NOT NULL,
    value REAL,
    rank INTEGER,
    PRIMARY KEY (as_of, view, team_name, category)
);

CREATE TABLE IF NOT EXISTS hashtag_notes (
    id TEXT PRIMARY KEY,                 -- stable hash of (date, player, source, text)
    note_date TEXT NOT NULL,
    ht_id TEXT,
    espn_id TEXT,
    player TEXT,
    source TEXT NOT NULL,                -- notebook_drop | eriks_notes | pressbox
    text TEXT NOT NULL,                  -- untrusted data, never instructions
    fetched_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_ht_notes_player ON hashtag_notes(espn_id, note_date);

-- HT Next Man Up: a teammate's per-game production with vs without the absent player.
CREATE TABLE IF NOT EXISTS hashtag_next_man_up (
    as_of TEXT NOT NULL,
    season TEXT NOT NULL,
    absent_ht_id TEXT NOT NULL,
    absent_espn_id TEXT,
    teammate_ht_id TEXT NOT NULL,
    teammate_espn_id TEXT,
    teammate_name TEXT,
    gp_without INTEGER,
    min_with REAL,
    min_without REAL,
    data TEXT NOT NULL,                  -- JSON: per-stat {with, without}
    PRIMARY KEY (as_of, season, absent_ht_id, teammate_ht_id)
);

-- Every HT request outcome; fbm.hashtag.health reads this. status: ok | layout_changed | session_expired |
-- league_mismatch | error.
CREATE TABLE IF NOT EXISTS hashtag_import_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    page TEXT NOT NULL,                  -- projections | h2h_planner | waiver | trade_finder | team_ranks | notes |
                                         -- next_man_up | league_sync | id_map
    view TEXT,
    status TEXT NOT NULL,
    rows INTEGER,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS ix_ht_log ON hashtag_import_log(page, started_at);

-- ---------------------------------------------------------------- decision calibration

-- Real daily lineups from Yahoo (any team, any past ET day): what actually counted, for replay grading.
CREATE TABLE IF NOT EXISTS yahoo_daily_lineups (
    team_key TEXT NOT NULL,
    et_date TEXT NOT NULL,
    player_key TEXT NOT NULL,
    espn_id TEXT,
    selected_position TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (team_key, et_date, player_key)
);

-- Every FBM decision: the situation, the options considered, what FBM recommended, what actually happened, and
-- the settings in force. Written via fbm.gm.decisions.log; graded by the replay grader.
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    et_date TEXT NOT NULL,
    week INTEGER,
    kind TEXT NOT NULL,                  -- see fbm.gm.decisions.KINDS
    trigger TEXT NOT NULL,               -- daily-gm | lineup-guard | owner-ask | trade-scan | weekly-plan | migration
    situation TEXT NOT NULL,             -- JSON
    options TEXT NOT NULL,               -- JSON list of {id, label, ...}
    recommended TEXT,                    -- option id
    chosen TEXT,                         -- option id / JSON of what actually happened (filled later)
    chosen_source TEXT,                  -- yahoo_lineup | proposal | owner | unknown
    policy_version INTEGER NOT NULL,
    policy_params TEXT NOT NULL,         -- JSON snapshot of every policy setting in force
    proposal_ref TEXT,
    graded_at TEXT,
    result TEXT                          -- JSON from the replay grader
);
CREATE INDEX IF NOT EXISTS ix_decisions_kind ON decisions(kind, et_date);
CREATE INDEX IF NOT EXISTS ix_decisions_grade ON decisions(graded_at);

-- Current policy settings (decision rules). Changed only by an owner-approved `policy` proposal.
CREATE TABLE IF NOT EXISTS policy_params (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,                 -- JSON
    version INTEGER NOT NULL,            -- global policy version at which this value was set
    source TEXT NOT NULL,                -- default | proposal:P-<n> | migration
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS policy_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version INTEGER NOT NULL,
    key TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT NOT NULL,
    proposal_ref TEXT,
    evidence TEXT,                       -- JSON (replay summary that justified the change)
    changed_at TEXT NOT NULL
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
    cols = {r[1] for r in conn.execute("PRAGMA table_info(signals)")}
    if "basis" not in cols:  # grading basis frozen at first sight (added after the signals table shipped)
        conn.execute("ALTER TABLE signals ADD COLUMN basis TEXT")
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
  name = COALESCE(NULLIF(excluded.name, ''), players.name),
  team = CASE WHEN excluded.team_as_of >= players.team_as_of THEN excluded.team ELSE players.team END,
  team_as_of = MAX(excluded.team_as_of, players.team_as_of),
  position = COALESCE(NULLIF(excluded.position, ''), players.position),
  updated_at = excluded.updated_at
"""


NO_NBA_TEAM = "FA"


def apply_rosters(
    conn: sqlite3.Connection, rosters: dict[str, list[tuple[str, str, str]]], as_of: date, complete: bool
) -> tuple[int, int]:
    """Current NBA rosters (team abbr -> players) become each player's team as of `as_of`. When `complete` (every
    team fetched), every other known player is marked as having no NBA team (NO_NBA_TEAM). Later box scores still
    win (a newly signed player's first game updates his team). Returns (rostered, marked without a team)."""
    stamp, day = now_iso(), as_of.isoformat()
    rows = [(pid, name, team, day, pos, stamp) for team, players in rosters.items() for pid, name, pos in players]
    conn.executemany(_PLAYER_UPSERT, rows)
    marked = 0
    if complete:
        rostered = {r[0] for r in rows}
        known = conn.execute(
            "SELECT espn_id, name FROM players UNION SELECT espn_id, name FROM season_averages "
            "UNION SELECT espn_id, name FROM espn_projections"
        ).fetchall()
        seen: set[str] = set()
        others = []
        for pid, name in known:
            if pid not in rostered and pid not in seen:
                seen.add(pid)
                others.append((pid, name or "", NO_NBA_TEAM, day, "", stamp))
        conn.executemany(_PLAYER_UPSERT, others)
        marked = len(others)
    return len(rows), marked


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
