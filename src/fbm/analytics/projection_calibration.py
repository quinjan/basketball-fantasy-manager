"""Projection calibration: which forecaster is right, FBM's engine or ESPN, and how to blend them.

Every sync logs both per-game forecasts (engine = FBM's own projection before the ESPN blend; espn = ESPN's latest
projection). Once a forecast's window has finished, it is graded against what the player actually averaged over the
next `projection.calibration_horizon_days` days (players with at least `projection.calibration_min_games` games).
Forecasts are logged from opening night on, and dates where both forecasts are identical are not counted.

For each stat k the blend error is SSE_k(w) = sum((1 - w) * engine + w * espn - actual)^2, and the best weight has a
closed form: w = -sum((engine - actual) * (espn - engine)) / sum((espn - engine)^2), clipped to [0, 1]. The overall
weight minimises the sum over stats of SSE_k(w) / SST_k (each stat normalised by its spread across players, so
points don't drown out steals). Once enough forecast dates are graded the measured weight is stored and used by
`projection_table`; an explicit `projection.espn_weight` in config/constants.toml always wins.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd

from fbm.analytics.categories import BASE_STATS
from fbm.constants import C, overrides

WEIGHT_KEY = "projection.espn_weight"
SOURCES = ("engine", "espn")
GRADED_STATS = ("pts", "reb", "ast", "stl", "blk", "tov", "tpm", "fgm", "fga", "ftm", "fta")


def log_forecasts(conn: sqlite3.Connection, season: int, as_of: date, engine: pd.DataFrame, espn: pd.DataFrame) -> int:
    """Store today's engine and ESPN forecasts for players both project (one row per source; re-logging the same
    day replaces it)."""
    if engine.empty or espn.empty:
        return 0
    ids = engine.index[engine["minutes"].fillna(0) > 0].intersection(espn.index[espn["minutes"].fillna(0) > 0])
    if not len(ids):
        return 0
    cols = ("season", "as_of", "source", "espn_id", *BASE_STATS)
    rows = []
    for source, table in (("engine", engine), ("espn", espn)):
        sub = table.loc[ids, list(BASE_STATS)].astype(float).fillna(0.0)
        rows += [
            (season, as_of.isoformat(), source, pid, *vals)
            for pid, vals in zip(sub.index, sub.values.tolist(), strict=True)
        ]
    conn.executemany(
        f"INSERT OR REPLACE INTO projection_log({', '.join(cols)}) VALUES ({','.join('?' * len(cols))})", rows
    )
    conn.commit()
    return len(ids)


def played_lines(conn: sqlite3.Connection, season: int) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT espn_id, et_date, " + ", ".join(BASE_STATS) + " FROM box_lines WHERE season = ? AND played = 1",
        conn,
        params=(season,),
    ).dropna(subset=["minutes"])


def window_actuals(box: pd.DataFrame, start: date, end: date, min_games: int) -> pd.DataFrame:
    """Per-game averages over [start, end] (ET dates) for players with at least `min_games` games played."""
    lines = box[(box["et_date"] >= start.isoformat()) & (box["et_date"] <= end.isoformat())]
    if lines.empty:
        return pd.DataFrame(columns=[*BASE_STATS, "games"])
    grouped = lines.groupby("espn_id")
    out = grouped[list(BASE_STATS)].mean()
    out["games"] = grouped.size()
    return out[out["games"] >= min_games]


def _best_weight(e: np.ndarray, s: np.ndarray, a: np.ndarray) -> float:
    d = s - e
    denom = float((d**2).sum())
    return float(np.clip(-((e - a) * d).sum() / denom, 0.0, 1.0)) if denom > 0 else 0.0


def grade(
    conn: sqlite3.Connection, season: int, today: date, horizon: int | None = None, min_games: int | None = None
) -> dict[str, Any]:
    """Grade every logged forecast date whose window has finished. Returns per-stat errors and the best weight."""
    horizon = int(C("projection.calibration_horizon_days")) if horizon is None else horizon
    min_games = int(C("projection.calibration_min_games")) if min_games is None else min_games
    last_as_of = (today - timedelta(days=horizon + 1)).isoformat()
    log = pd.read_sql_query(
        "SELECT as_of, source, espn_id, " + ", ".join(BASE_STATS) + " FROM projection_log "
        "WHERE season = ? AND as_of <= ?",
        conn,
        params=(season, last_as_of),
    )
    empty = {"dates_graded": 0, "player_windows": 0, "horizon_days": horizon, "best_weight": None, "by_stat": {}}
    if log.empty:
        return empty
    box = played_lines(conn, season)
    frames = []
    for as_of, day in log.groupby("as_of"):
        start = date.fromisoformat(as_of) + timedelta(days=1)
        actual = window_actuals(box, start, start + timedelta(days=horizon - 1), min_games)
        eng = day[day["source"] == "engine"].set_index("espn_id")
        esp = day[day["source"] == "espn"].set_index("espn_id")
        ids = eng.index.intersection(esp.index).intersection(actual.index)
        if not len(ids):
            continue
        cols = list(GRADED_STATS)
        if (eng.loc[ids, cols].astype(float) - esp.loc[ids, cols].astype(float)).abs().to_numpy().max() < 1e-9:
            continue  # identical forecasts carry no information about the blend weight
        frame = pd.DataFrame({"as_of": as_of}, index=ids)
        for stat in GRADED_STATS + ("minutes",):
            frame[f"e_{stat}"] = eng.loc[ids, stat].astype(float)
            frame[f"s_{stat}"] = esp.loc[ids, stat].astype(float)
            frame[f"a_{stat}"] = actual.loc[ids, stat].astype(float)
        frames.append(frame)
    if not frames:
        return empty
    df = pd.concat(frames)
    by_stat: dict[str, dict[str, float]] = {}
    num = den = 0.0
    for stat in ("minutes",) + GRADED_STATS:
        e, s, a = (df[f"{p}_{stat}"].to_numpy() for p in ("e", "s", "a"))
        sst = float(((a - a.mean()) ** 2).sum()) or 1.0
        by_stat[stat] = {
            "rmse_engine": round(float(np.sqrt(((e - a) ** 2).mean())), 3),
            "rmse_espn": round(float(np.sqrt(((s - a) ** 2).mean())), 3),
            "best_weight": round(_best_weight(e, s, a), 2),
        }
        if stat in GRADED_STATS:
            num += float(((e - a) * (s - e)).sum()) / sst
            den += float(((s - e) ** 2).sum()) / sst
    best = float(np.clip(-num / den, 0.0, 1.0)) if den > 0 else None

    def _score(w: float) -> float:
        total = 0.0
        for stat in GRADED_STATS:
            e, s, a = (df[f"{p}_{stat}"].to_numpy() for p in ("e", "s", "a"))
            sst = float(((a - a.mean()) ** 2).sum()) or 1.0
            total += float((((1 - w) * e + w * s - a) ** 2).sum()) / sst
        return round(total / len(GRADED_STATS), 4)

    return {
        "dates_graded": int(df["as_of"].nunique()),
        "player_windows": len(df),
        "horizon_days": horizon,
        "best_weight": None if best is None else round(best, 2),
        "normalized_error": {"engine_only": _score(0.0), "espn_only": _score(1.0), "half_half": _score(0.5)}
        | ({"best": _score(best)} if best is not None else {}),
        "by_stat": by_stat,
    }


def resolve_espn_weight(conn: sqlite3.Connection | None) -> tuple[float, str]:
    """Weight on ESPN in the projection blend and where it came from: override > measured > default."""
    if WEIGHT_KEY in overrides():
        return float(C(WEIGHT_KEY)), "override (config/constants.toml)"
    if conn is not None:
        try:
            row = conn.execute("SELECT value FROM model_params WHERE key = ?", (WEIGHT_KEY,)).fetchone()
        except sqlite3.OperationalError:
            row = None
        if row is not None:
            return float(row[0]), "measured (projection_calibration)"
    return float(C(WEIGHT_KEY)), "default"


def measure_and_store(conn: sqlite3.Connection, season: int, today: date) -> dict[str, Any]:
    """Grade forecasts; store the measured weight once enough forecast dates are graded."""
    from fbm.store import db

    report = grade(conn, season, today)
    needed = int(C("projection.calibration_min_dates"))
    stored = False
    if report["best_weight"] is not None and report["dates_graded"] >= needed:
        w = round(float(report["best_weight"]) * 20) / 20  # 0.05 steps: avoid churning on noise
        detail = json.dumps({k: report[k] for k in ("dates_graded", "player_windows", "horizon_days")})
        conn.execute(
            "INSERT INTO model_params(key, value, detail, updated_at) VALUES (?,?,?,?) ON CONFLICT(key) DO UPDATE "
            "SET value=excluded.value, detail=excluded.detail, updated_at=excluded.updated_at",
            (WEIGHT_KEY, w, detail, db.now_iso()),
        )
        conn.commit()
        stored = True
    weight, source = resolve_espn_weight(conn)
    return {
        **report,
        "min_dates_for_measured_weight": needed,
        "stored_measured_weight": stored,
        "espn_weight_in_use": weight,
        "espn_weight_source": source,
    }
