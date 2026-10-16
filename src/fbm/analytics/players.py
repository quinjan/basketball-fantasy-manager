"""Player stat tables (per-game means) and rest-of-season projections from the local store."""

from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd

from fbm.analytics.categories import BASE_STATS
from fbm.constants import C


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


def recent_minutes(conn: sqlite3.Connection, season: int, window: int) -> pd.DataFrame:
    """Average minutes over each player's last `window` played games this season."""
    lines = pd.read_sql_query(
        "SELECT espn_id, et_date, minutes FROM box_lines WHERE season = ? AND played = 1 ORDER BY et_date DESC",
        conn,
        params=(season,),
    )
    if lines.empty:
        return pd.DataFrame(columns=["recent_mpg", "recent_games"])
    recent = lines.groupby("espn_id").head(window).groupby("espn_id")["minutes"]
    return pd.DataFrame({"recent_mpg": recent.mean(), "recent_games": recent.size()})


def apply_minutes_trend(proj: pd.DataFrame, current_mpg: pd.Series, recent: pd.DataFrame) -> pd.DataFrame:
    """Shift projections toward recent minutes when a role change is detected.

    Role change: recent minus current-season minutes >= projection.trend_min_change over >= trend_min_games recent
    games. Projected minutes = (1-w) * blended + w * recent, w = trend_max_weight * recent_games / recent_games
    window. Counting stats, makes and attempts scale with minutes, so shooting percentages are unchanged.
    """
    window = int(C("projection.recent_games"))
    min_games = int(C("projection.trend_min_games"))
    min_change = float(C("projection.trend_min_change"))
    max_weight = float(C("projection.trend_max_weight"))
    out = proj.copy()
    out["recent_mpg"] = recent["recent_mpg"].reindex(out.index)
    out["minutes_trend"] = (out["recent_mpg"] - current_mpg.reindex(out.index)).round(1)
    out["role"] = ""
    eligible = (recent["recent_games"].reindex(out.index).fillna(0) >= min_games) & (
        out["minutes_trend"].abs() >= min_change
    )
    for pid in out.index[eligible]:
        n = float(recent.at[pid, "recent_games"])
        w = max_weight * min(1.0, n / window)
        base = float(out.at[pid, "minutes"])
        if base <= 0:
            continue
        projected = (1 - w) * base + w * float(out.at[pid, "recent_mpg"])
        scale = projected / base
        for stat in BASE_STATS:
            if stat != "minutes":
                out.at[pid, stat] = out.at[pid, stat] * scale
        out.at[pid, "minutes"] = projected
        out.at[pid, "role"] = "up" if out.at[pid, "minutes_trend"] > 0 else "down"
    return out


def projection_table(
    conn: sqlite3.Connection, current: int, prior: int, shrink_games: float | None = None
) -> pd.DataFrame:
    """Rest-of-season per-game projection: current season shrunk toward the prior season by games played,
    then adjusted for recent minutes trends (role changes).

    weight_current = gp_current / (gp_current + shrink_games). Players without prior data use current stats only;
    players without current data use the prior (pre-season / injured).
    """
    shrink = float(C("projection.shrink_games")) if shrink_games is None else shrink_games
    cur = season_table(conn, current)
    prev = season_table(conn, prior)
    ids = cur.index.union(prev.index)
    cur = cur.reindex(ids)
    prev = prev.reindex(ids)
    gp_cur = cur["gp"].fillna(0.0)
    w = (gp_cur / (gp_cur + shrink)).where(prev["gp"].notna(), 1.0).where(cur["gp"].notna(), 0.0)

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
    recent = recent_minutes(conn, current, int(C("projection.recent_games")))
    if recent.empty:
        out["recent_mpg"] = np.nan
        out["minutes_trend"] = np.nan
        out["role"] = ""
        return out
    return apply_minutes_trend(out, cur["minutes"], recent)
