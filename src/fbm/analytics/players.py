"""Player stat tables (per-game means) and rest-of-season projections from the local store."""

from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd

from fbm.analytics.categories import BASE_STATS

# Games of current-season data at which current stats and the prior get equal weight.
DEFAULT_SHRINK_GAMES = 15.0


def season_table(conn: sqlite3.Connection, season: int) -> pd.DataFrame:
    """Per-game season averages (ESPN) indexed by espn_id."""
    df = pd.read_sql_query(
        "SELECT espn_id, name, team, position, gp, " + ", ".join(BASE_STATS) + " FROM season_averages WHERE season = ?",
        conn,
        params=(season,),
    )
    if "tpa" in df.columns:
        df["tpa"] = df["tpa"].fillna(0.0)
    return df.set_index("espn_id")


def weekly_table(conn: sqlite3.Connection, season: int) -> pd.DataFrame:
    """Player-week per-game averages from box scores (weeks are Mon-Sun ET), for week-to-week variance."""
    lines = pd.read_sql_query(
        "SELECT espn_id, et_date, minutes, pts, reb, ast, stl, blk, tov, tpm, tpa, fgm, fga, ftm, fta "
        "FROM box_lines WHERE season = ? AND played = 1",
        conn,
        params=(season,),
    )
    if lines.empty:
        return lines
    dates = pd.to_datetime(lines["et_date"])
    lines["week"] = (dates - pd.to_timedelta(dates.dt.weekday, unit="D")).dt.strftime("%Y-%m-%d")
    grouped = lines.groupby(["espn_id", "week"])
    weekly = grouped[list(BASE_STATS)].mean()
    weekly["games"] = grouped.size()
    return weekly.reset_index()


def projection_table(
    conn: sqlite3.Connection, current: int, prior: int, shrink_games: float = DEFAULT_SHRINK_GAMES
) -> pd.DataFrame:
    """Rest-of-season per-game projection: current season shrunk toward the prior season by games played.

    weight_current = gp_current / (gp_current + shrink_games). Players without prior data use current stats only;
    players without current data use the prior (pre-season / injured).
    """
    cur = season_table(conn, current)
    prev = season_table(conn, prior)
    ids = cur.index.union(prev.index)
    cur = cur.reindex(ids)
    prev = prev.reindex(ids)
    gp_cur = cur["gp"].fillna(0.0)
    w = (gp_cur / (gp_cur + shrink_games)).where(prev["gp"].notna(), 1.0).where(cur["gp"].notna(), 0.0)

    out = pd.DataFrame(index=ids)
    for col in ("name", "team", "position"):
        out[col] = cur[col].combine_first(prev[col])
    out["gp_current"] = gp_cur.astype(int)
    out["gp_prior"] = prev["gp"].fillna(0).astype(int)
    out["weight_current"] = w.round(3)
    for stat in BASE_STATS:
        out[stat] = w * cur[stat].fillna(0.0) + (1 - w) * prev[stat].fillna(0.0)
    out["gp"] = np.maximum(out["gp_current"], out["gp_prior"])
    out.index.name = "espn_id"
    return out
