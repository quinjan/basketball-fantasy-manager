"""Player stat tables (per-game means), ESPN projection snapshots, and rest-of-season projections."""

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


RATE_STATS = tuple(s for s in BASE_STATS if s != "minutes")


def recent_form(conn: sqlite3.Connection, season: int, window: int) -> pd.DataFrame:
    """Per-minute production over each player's last `window` played games (sum of stat / sum of minutes)."""
    lines = pd.read_sql_query(
        "SELECT espn_id, et_date, " + ", ".join(BASE_STATS) + " FROM box_lines "
        "WHERE season = ? AND played = 1 ORDER BY et_date DESC",
        conn,
        params=(season,),
    )
    if lines.empty:
        return pd.DataFrame(columns=[*RATE_STATS, "form_games", "form_minutes"])
    lines = lines.dropna(subset=["minutes", "pts"])  # lines without a stat line carry no form signal
    recent = lines.groupby("espn_id").head(window).fillna(0.0)
    sums = recent.groupby("espn_id")[list(BASE_STATS)].sum()
    out = sums[list(RATE_STATS)].div(sums["minutes"].where(sums["minutes"] > 0), axis=0)
    out["form_games"] = recent.groupby("espn_id").size()
    out["form_minutes"] = sums["minutes"]
    return out[out["form_minutes"] > 0]


def apply_recent_form(proj: pd.DataFrame, form: pd.DataFrame) -> pd.DataFrame:
    """Blend recent per-minute rates into the projection's per-minute rates, keeping projected minutes.

    weight = min(projection.form_max_weight, n / (n + projection.form_shrink_games)) for n recent played games;
    rate = (1 - weight) * projected_rate + weight * recent_rate; stat = rate * projected minutes. Conservative by
    design (most hot/cold streaks regress). Players with no recent games or no projected minutes are unchanged.
    """
    shrink = float(C("projection.form_shrink_games"))
    cap = float(C("projection.form_max_weight"))
    out = proj.copy()
    n = form["form_games"].reindex(out.index).fillna(0.0).astype(float) if not form.empty else pd.Series(0.0, out.index)
    w = (n / (n + shrink)).clip(upper=cap).where((n > 0) & (out["minutes"] > 0), 0.0)
    out["form_weight"] = w.round(3)
    if not (w > 0).any():
        return out
    mins = out["minutes"].where(out["minutes"] > 0)
    for stat in RATE_STATS:
        rate = out[stat] / mins
        recent = form[stat].reindex(out.index)
        blended = ((1 - w) * rate + w * recent).where(w > 0, rate)
        out[stat] = (blended * out["minutes"]).where(w > 0, out[stat])
    return out


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


def current_teams(conn: sqlite3.Connection, since: str) -> dict[str, str]:
    """Latest known NBA team per player (rosters + box scores), only if observed on/after `since` (ET date)."""
    return {
        r["espn_id"]: r["team"]
        for r in conn.execute("SELECT espn_id, team FROM players WHERE team_as_of >= ? AND team != ''", (since,))
    }


def espn_snapshot_table(
    conn: sqlite3.Connection,
    season: int,
    on_or_before: str | None = None,
    before: str | None = None,
    earliest: bool = False,
) -> pd.DataFrame:
    """One ESPN projection per player: the latest snapshot (or the earliest), optionally limited by date."""
    cond, params = "season = ?", [season]
    if on_or_before:
        cond += " AND snapshot_date <= ?"
        params.append(on_or_before)
    if before:
        cond += " AND snapshot_date < ?"
        params.append(before)
    agg = "MIN" if earliest else "MAX"
    df = pd.read_sql_query(
        "SELECT p.espn_id, p.snapshot_date, p.name, p.team, p.position, p.gp, "
        + ", ".join(f"p.{s}" for s in BASE_STATS)
        + f" FROM espn_projections p JOIN (SELECT espn_id, {agg}(snapshot_date) AS d FROM espn_projections "
        f"WHERE {cond} GROUP BY espn_id) m ON p.espn_id = m.espn_id AND p.snapshot_date = m.d WHERE p.season = ?",
        conn,
        params=(*params, season),
    )
    return df.set_index("espn_id")


def opening_day(conn: sqlite3.Connection, season: int) -> str | None:
    row = conn.execute("SELECT MIN(et_date) FROM games WHERE season = ? AND season_type = 2", (season,)).fetchone()
    return row[0] if row and row[0] else None


def espn_baseline_table(conn: sqlite3.Connection, season: int) -> pd.DataFrame:
    """Preseason baseline: each player's last ESPN snapshot before opening night. Players ESPN first projected after
    opening night (or every player, when FBM was installed mid-season) use their earliest snapshot instead."""
    opening = opening_day(conn, season)
    pre = espn_snapshot_table(conn, season, before=opening)
    if opening is None:
        return pre
    later = espn_snapshot_table(conn, season, earliest=True)
    return pd.concat([pre, later[~later.index.isin(pre.index)]])


def prior_table(conn: sqlite3.Connection, current: int, prior: int) -> pd.DataFrame:
    """What we expected before this season's games: ESPN's preseason projection, else last season's per-game stats.
    `prior_source` says which (gp is ESPN's projected games for the ESPN rows)."""
    base = espn_baseline_table(conn, current).drop(columns=["snapshot_date"], errors="ignore")
    last = season_table(conn, prior)
    last = last[~last.index.isin(base.index)]
    parts = [t for t in (base.assign(prior_source="espn_preseason"), last.assign(prior_source="last_season")) if len(t)]
    if not parts:
        return pd.DataFrame(columns=["name", "team", "position", "gp", *BASE_STATS, "prior_source"])
    out = pd.concat(parts)
    out.index.name = "espn_id"
    return out


def engine_projection_table(
    conn: sqlite3.Connection, current: int, prior: int, shrink_games: float | None = None
) -> pd.DataFrame:
    """FBM's own rest-of-season per-game projection: current season shrunk toward the prior (ESPN preseason
    projection, else last season) by games played, then adjusted for recent form and minutes trends (role changes).

    weight_current = gp_current / (gp_current + shrink_games). Recent per-minute form (apply_recent_form) is blended
    in before the minutes trend. Players without a prior use current stats only; players without current data use
    the prior (pre-season / injured / rookies).
    """
    shrink = float(C("projection.shrink_games")) if shrink_games is None else shrink_games
    cur = season_table(conn, current)
    prev = prior_table(conn, current, prior)
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
    out["prior_source"] = prev["prior_source"].fillna("none")
    out["weight_current"] = w.round(3)
    for stat in BASE_STATS:
        out[stat] = w * cur[stat].fillna(0.0) + (1 - w) * prev[stat].fillna(0.0)
    out["gp"] = np.maximum(out["gp_current"], out["gp_prior"])
    out.index.name = "espn_id"
    out = apply_recent_form(out, recent_form(conn, current, int(C("projection.form_games"))))
    recent = recent_minutes(conn, current, int(C("projection.recent_games")))
    if recent.empty:
        out["recent_mpg"] = np.nan
        out["minutes_trend"] = np.nan
        out["role"] = ""
        return out
    return apply_minutes_trend(out, cur["minutes"], recent)


def blend_espn(engine: pd.DataFrame, espn: pd.DataFrame, weight: float) -> pd.DataFrame:
    """Final projection = (1 - weight) * engine + weight * ESPN's current projection, stat by stat, for players
    both project (others keep the engine line). `espn_weight` records the weight each player got."""
    out = engine.copy()
    out["espn_weight"] = 0.0
    if espn.empty or weight <= 0 or out.empty:
        return out
    both = out.index.intersection(espn.index[espn["minutes"].fillna(0) > 0])
    both = both[out.loc[both, "minutes"] > 0]
    if not len(both):
        return out
    w = min(1.0, float(weight))
    for stat in BASE_STATS:
        out.loc[both, stat] = (1 - w) * out.loc[both, stat] + w * espn.loc[both, stat].fillna(0.0).astype(float)
    out.loc[both, "espn_weight"] = w
    return out


def projection_table(
    conn: sqlite3.Connection,
    current: int,
    prior: int,
    shrink_games: float | None = None,
    espn_weight: float | None = None,
) -> pd.DataFrame:
    """Rest-of-season per-game projection used everywhere: FBM's engine blended with ESPN's current projection.

    The ESPN weight is `espn_weight` if given, else the resolved weight (constants override > measured by
    projection_calibration > projection.espn_weight default).
    """
    from fbm.analytics.projection_calibration import resolve_espn_weight

    engine = engine_projection_table(conn, current, prior, shrink_games)
    weight = resolve_espn_weight(conn)[0] if espn_weight is None else espn_weight
    return blend_espn(engine, espn_snapshot_table(conn, current), weight)
