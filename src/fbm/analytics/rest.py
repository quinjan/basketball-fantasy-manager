"""Back-to-back rest risk: how often a player sits one leg of his team's back-to-backs.

A player's history gives k back-to-backs where he played exactly one leg out of n where he played at least one.
Typical regulars do this ~15% of the time for non-rest reasons (injury boundaries, illness), so only the excess over
`sim.b2b_rest_baseline` counts as rest, shrunk toward that baseline with `sim.b2b_rest_prior_pairs` pseudo-pairs:

    rest_per_leg = max(0, k - n * baseline) / (n + prior_pairs) / 2

(a player who sits one of the two legs has half that chance of missing any given leg).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import date, timedelta

import pandas as pd

from fbm.constants import C


def _team_games(conn: sqlite3.Connection, seasons: Iterable[int], completed_only: bool) -> pd.DataFrame:
    season_list = ",".join(str(int(s)) for s in seasons)
    games = pd.read_sql_query(
        f"SELECT et_date, home, away, season FROM games WHERE season_type = 2 AND season IN ({season_list})"
        + (" AND completed = 1" if completed_only else ""),
        conn,
    )
    if games.empty:
        return pd.DataFrame(columns=["team", "season", "d"])
    out = pd.concat(
        [
            games[["et_date", "home", "season"]].rename(columns={"home": "team"}),
            games[["et_date", "away", "season"]].rename(columns={"away": "team"}),
        ]
    )
    out["d"] = pd.to_datetime(out["et_date"]).dt.date
    return out[["team", "season", "d"]].drop_duplicates()


def back_to_back_pairs(conn: sqlite3.Connection, seasons: Iterable[int], completed_only: bool = True) -> pd.DataFrame:
    """(team, season, d1, d2) for every pair of games on consecutive ET days."""
    rows = []
    for (team, season), g in _team_games(conn, seasons, completed_only).groupby(["team", "season"]):
        ds = sorted(g["d"])
        rows += [(team, season, a, b) for a, b in zip(ds, ds[1:], strict=False) if b - a == timedelta(days=1)]
    return pd.DataFrame(rows, columns=["team", "season", "d1", "d2"])


def team_b2b_dates(conn: sqlite3.Connection, season: int) -> dict[str, frozenset[date]]:
    """Every ET date (played or scheduled) that is one leg of a back-to-back, per NBA team."""
    pairs = back_to_back_pairs(conn, [season], completed_only=False)
    out: dict[str, set[date]] = {}
    for r in pairs.itertuples():
        out.setdefault(r.team, set()).update((r.d1, r.d2))
    return {t: frozenset(ds) for t, ds in out.items()}


def b2b_rest_probs(conn: sqlite3.Connection, seasons: Iterable[int]) -> dict[str, float]:
    """Per-leg extra chance each player sits a back-to-back game (only players above the baseline appear)."""
    seasons = list(seasons)
    pairs = back_to_back_pairs(conn, seasons)
    if pairs.empty:
        return {}
    season_list = ",".join(str(int(s)) for s in seasons)
    lines = pd.read_sql_query(
        f"SELECT espn_id, team, season, et_date FROM box_lines WHERE played = 1 AND season IN ({season_list})", conn
    )
    if lines.empty:
        return {}
    lines["d"] = pd.to_datetime(lines["et_date"]).dt.date
    lines = lines[["espn_id", "team", "season", "d"]].drop_duplicates()
    keys = ["espn_id", "team", "season", "d1", "d2"]
    leg1 = pairs.merge(lines.rename(columns={"d": "d1"}), on=["team", "season", "d1"])[keys]
    leg2 = pairs.merge(lines.rename(columns={"d": "d2"}), on=["team", "season", "d2"])[keys]
    both = leg1.merge(leg2, on=keys, how="outer", indicator=True)
    agg = both.groupby("espn_id")["_merge"].agg(n="size", k=lambda m: int((m != "both").sum()))
    baseline = float(C("sim.b2b_rest_baseline"))
    prior = float(C("sim.b2b_rest_prior_pairs"))
    rest = ((agg["k"] - agg["n"] * baseline).clip(lower=0) / (agg["n"] + prior) / 2).astype(float)
    return {str(pid): float(v) for pid, v in rest[rest > 0].items()}
