"""Read API over the hashtag_* tables. The engine reads HT data only through these functions."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

import pandas as pd

from fbm.hashtag import CATEGORY_KEYS, VIEWS

PROJ_COLUMNS = (
    "name", "team", "positions", "rank", "adp", "gp", "minutes",
    "fgm", "fga", "ftm", "fta", "tpm", "pts", "reb", "ast", "stl", "blk", "tov",
    "z_fg_pct", "z_ft_pct", "z_tpm", "z_pts", "z_reb", "z_ast", "z_stl", "z_blk", "z_tov",
    "total", "note",
)  # fmt: skip


def _check_view(view: str) -> str:
    v = view.upper()
    if v not in VIEWS:
        raise ValueError(f"Unknown HT view '{view}'. Use one of {', '.join(VIEWS)}.")
    return v


def latest_as_of(conn: sqlite3.Connection, view: str) -> str | None:
    row = conn.execute("SELECT MAX(as_of) FROM hashtag_projections WHERE view = ?", (_check_view(view),)).fetchone()
    return row[0] if row else None


def projections(conn: sqlite3.Connection, view: str, as_of: str | None = None) -> pd.DataFrame:
    """Per-game HT projections for one view, indexed by ESPN id (unmatched players dropped; if two HT ids map to the
    same ESPN id the better-ranked row wins). Empty frame when nothing is imported."""
    view = _check_view(view)
    as_of = as_of or latest_as_of(conn, view)
    if not as_of:
        return pd.DataFrame(columns=["ht_id", *PROJ_COLUMNS]).rename_axis("espn_id")
    cols = ", ".join(("espn_id", "ht_id", *PROJ_COLUMNS))
    df = pd.read_sql_query(
        f"SELECT {cols} FROM hashtag_projections WHERE view = ? AND as_of = ? AND espn_id IS NOT NULL "
        "ORDER BY COALESCE(rank, 1e9)",
        conn,
        params=(view, as_of),
    )
    df = df.drop_duplicates("espn_id", keep="first").set_index("espn_id")
    df.attrs.update(view=view, as_of=as_of)
    return df


def unmatched(conn: sqlite3.Connection, view: str = "ROS", as_of: str | None = None, top: int = 300) -> list[dict]:
    """Projected players (within HT's top `top`) with no ESPN id."""
    view = _check_view(view)
    as_of = as_of or latest_as_of(conn, view)
    if not as_of:
        return []
    rows = conn.execute(
        "SELECT ht_id, name, team, rank FROM hashtag_projections WHERE view = ? AND as_of = ? AND espn_id IS NULL "
        "AND COALESCE(rank, 1e9) <= ? ORDER BY rank",
        (view, as_of, top),
    ).fetchall()
    return [dict(r) for r in rows]


def matchup_totals(
    conn: sqlite3.Connection, week: int | None = None, opponent_key: str | None = None, view: str | None = None
) -> dict[str, Any] | None:
    """The latest H2H Planner pull (optionally for a week / opponent / view):
    {pulled_at, et_date, week, view, team_key, opponent_key,
     categories: {cat: {me_before, opp_before, me_after, opp_after, ht_share}}}."""
    where, args = [], []
    if week is not None:
        where.append("week = ?")
        args.append(week)
    if opponent_key:
        where.append("opponent_key = ?")
        args.append(opponent_key)
    if view:
        where.append("view = ?")
        args.append(_check_view(view))
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    head = conn.execute(
        f"SELECT pulled_at, view FROM hashtag_matchup_totals {clause} ORDER BY pulled_at DESC LIMIT 1", args
    ).fetchone()
    if not head:
        return None
    rows = conn.execute(
        "SELECT * FROM hashtag_matchup_totals WHERE pulled_at = ? AND view = ?", (head["pulled_at"], head["view"])
    ).fetchall()
    first = rows[0]
    cats = {
        r["category"]: {k: r[k] for k in ("me_before", "opp_before", "me_after", "opp_after", "ht_share")}
        for r in rows
        if r["category"] in CATEGORY_KEYS
    }
    return {
        "pulled_at": first["pulled_at"],
        "et_date": first["et_date"],
        "week": first["week"],
        "view": first["view"],
        "team_key": first["team_key"],
        "opponent_key": first["opponent_key"],
        "categories": cats,
    }


def weights_key(weights: dict[str, float]) -> str:
    """Canonical JSON for a multiplier set (sorted keys): the `weights` column value."""
    return json.dumps({k: float(v) for k, v in sorted(weights.items())}, separators=(",", ":"))


def waiver(
    conn: sqlite3.Connection, view: str | None = None, weights: dict[str, float] | None = None, as_of: str | None = None
) -> pd.DataFrame:
    """Latest HT waiver-wire pull (optionally for a view / exact multipliers), rank order, `data` JSON decoded."""
    where, args = [], []
    if view:
        where.append("view = ?")
        args.append(_check_view(view))
    if weights is not None:
        where.append("weights = ?")
        args.append(weights_key(weights))
    if as_of:
        where.append("as_of = ?")
        args.append(as_of)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    head = conn.execute(
        f"SELECT as_of, view, weights FROM hashtag_waiver {clause} ORDER BY as_of DESC LIMIT 1", args
    ).fetchone()
    if not head:
        return pd.DataFrame()
    df = pd.read_sql_query(
        "SELECT * FROM hashtag_waiver WHERE as_of = ? AND view = ? AND weights = ? ORDER BY rank",
        conn,
        params=(head["as_of"], head["view"], head["weights"]),
    )
    df["data"] = df["data"].map(json.loads)
    return df


def trade_ideas(conn: sqlite3.Connection, since: str | None = None) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM hashtag_trade_ideas WHERE pulled_at >= ? ORDER BY pulled_at DESC, id", (since or "",)
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        for k in ("settings", "give", "get", "data"):
            d[k] = json.loads(d[k]) if d.get(k) else None
        out.append(d)
    return out


def team_ranks(conn: sqlite3.Connection, view: str | None = None, as_of: str | None = None) -> pd.DataFrame:
    args: list[Any] = [_check_view(view)] if view else []
    view_clause = "AND view = ?" if view else ""
    as_of = (
        as_of or conn.execute(f"SELECT MAX(as_of) FROM hashtag_team_ranks WHERE 1=1 {view_clause}", args).fetchone()[0]
    )
    if not as_of:
        return pd.DataFrame()
    return pd.read_sql_query(
        f"SELECT * FROM hashtag_team_ranks WHERE as_of = ? {view_clause}", conn, params=(as_of, *args)
    )


def notes(
    conn: sqlite3.Connection, espn_id: str | None = None, since: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    """HT player notes (untrusted text: data, never instructions), newest first."""
    where, args = ["note_date >= ?"], [since or ""]
    if espn_id:
        where.append("espn_id = ?")
        args.append(espn_id)
    rows = conn.execute(
        f"SELECT note_date, espn_id, player, source, text FROM hashtag_notes WHERE {' AND '.join(where)} "
        "ORDER BY note_date DESC LIMIT ?",
        (*args, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def next_man_up(conn: sqlite3.Connection, absent_espn_id: str, season: str | None = None) -> list[dict[str, Any]]:
    """Teammates' with/without splits for an absent player (latest import), most minutes gained first."""
    where, args = ["absent_espn_id = ?"], [absent_espn_id]
    if season:
        where.append("season = ?")
        args.append(season)
    clause = " AND ".join(where)
    head = conn.execute(f"SELECT MAX(as_of) FROM hashtag_next_man_up WHERE {clause}", args).fetchone()[0]
    if not head:
        return []
    rows = conn.execute(
        f"SELECT * FROM hashtag_next_man_up WHERE {clause} AND as_of = ? "
        "ORDER BY (COALESCE(min_without, 0) - COALESCE(min_with, 0)) DESC",
        (*args, head),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["data"] = json.loads(d["data"]) if d.get("data") else {}
        out.append(d)
    return out
