"""Read API for importer-owned HT tables that are not in the core schema (see `importer.ensure_tables`)."""

from __future__ import annotations

import sqlite3

import pandas as pd

from fbm.hashtag.importer import SEASON_DURATIONS, ensure_tables

SEASON_COLUMNS = ("ht_id", "name", "team", "rank", "gp", "minutes", "fgm", "fga", "ftm", "fta", "tpm", "pts", "reb",
                  "ast", "stl", "blk", "tov", "total")  # fmt: skip


def season_stats(conn: sqlite3.Connection, duration: str = "season", as_of: str | None = None) -> pd.DataFrame:
    """HT season-to-date per-game stats (the market's view of a player), indexed by ESPN id (unmatched dropped; the
    better-ranked row wins duplicates). duration: season | last7 | last14 | last30. Empty frame if never imported."""
    if duration not in SEASON_DURATIONS:
        raise ValueError(f"Unknown duration '{duration}'. Use one of {', '.join(SEASON_DURATIONS)}.")
    ensure_tables(conn)
    as_of = (
        as_of
        or conn.execute("SELECT MAX(as_of) FROM hashtag_season_stats WHERE duration = ?", (duration,)).fetchone()[0]
    )
    if not as_of:
        return pd.DataFrame(columns=list(SEASON_COLUMNS)).rename_axis("espn_id")
    df = pd.read_sql_query(
        f"SELECT espn_id, {', '.join(SEASON_COLUMNS)} FROM hashtag_season_stats "
        "WHERE duration = ? AND as_of = ? AND espn_id IS NOT NULL ORDER BY COALESCE(rank, 1e9)",
        conn,
        params=(duration, as_of),
    )
    df = df.drop_duplicates("espn_id", keep="first").set_index("espn_id")
    df.attrs.update(duration=duration, as_of=as_of)
    return df
