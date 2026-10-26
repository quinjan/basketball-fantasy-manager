"""Per-game box scores: a player's game log, fantasy-week breakdowns per team, and season role splits (minutes,
starts, usage). Pure helpers over rows from `box_lines` (joined to `games` for the opponent)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from typing import Any

import pandas as pd

from fbm.analytics.categories import NINE_CAT, Category
from fbm.analytics.valuation import PoolStats, ValuationConfig, score_table

COUNT_COLS = ("pts", "reb", "ast", "stl", "blk", "tov", "tpm", "fgm", "fga", "ftm", "fta")
BOX_COLS = ("minutes", *COUNT_COLS)
SEASON_TYPE_LABEL = {1: "preseason", 3: "playoffs", 5: "play-in"}

_LINE_SQL = (
    "SELECT b.espn_id, b.event_id, b.et_date, b.season, b.team, b.starter, b.played, b.dnp_reason, "
    "b.minutes, b.pts, b.reb, b.ast, b.stl, b.blk, b.tov, b.tpm, b.fgm, b.fga, b.ftm, b.fta, "
    "g.home, g.away, g.season_type FROM box_lines b JOIN games g ON g.event_id = b.event_id"
)


def _n(value: Any) -> float:
    return float(value) if value is not None else 0.0


def _r(x: float, nd: int = 2) -> float:
    return round(float(x), nd)


def _pct(made: float, att: float) -> float | None:
    return round(made / att, 3) if att else None


# ------------------------------------------------------------------ game log


def recent_lines(conn: sqlite3.Connection, espn_id: str, last_n: int, season: int | None = None) -> list[sqlite3.Row]:
    """A player's most recent games played, newest first (all seasons unless `season` is given)."""
    sql = _LINE_SQL + " WHERE b.espn_id = ? AND b.played = 1"
    params: list[Any] = [espn_id]
    if season is not None:
        sql += " AND b.season = ?"
        params.append(season)
    sql += " ORDER BY b.et_date DESC, b.event_id DESC LIMIT ?"
    params.append(max(1, int(last_n)))
    return conn.execute(sql, params).fetchall()


def game_row(line: Mapping[str, Any]) -> dict[str, Any]:
    home = line["team"] == line["home"]
    row: dict[str, Any] = {
        "date_et": line["et_date"],
        "season": line["season"],
        "opp": line["away"] if home else line["home"],
        "home": home,
        "started": bool(line["starter"]),
        "min": _r(_n(line["minutes"]), 0),
        "pts": int(_n(line["pts"])),
        "reb": int(_n(line["reb"])),
        "ast": int(_n(line["ast"])),
        "stl": int(_n(line["stl"])),
        "blk": int(_n(line["blk"])),
        "tov": int(_n(line["tov"])),
        "3pm": int(_n(line["tpm"])),
        "fg": f"{int(_n(line['fgm']))}/{int(_n(line['fga']))}",
        "ft": f"{int(_n(line['ftm']))}/{int(_n(line['fta']))}",
    }
    if line["season_type"] in SEASON_TYPE_LABEL:
        row["type"] = SEASON_TYPE_LABEL[line["season_type"]]
    return row


def _frame(lines: Sequence[Mapping[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame([{c: _n(line[c]) for c in BOX_COLS} for line in lines], columns=list(BOX_COLS))
    df["gp"] = 1.0
    return df


def game_scores(
    lines: Sequence[Mapping[str, Any]], pool: PoolStats | None, cfg: ValuationConfig | None
) -> list[dict[str, float]] | None:
    """Single-game G/Z totals on the same scale as season per-game values (one game vs the average rostered player's
    per-game line). Percentage categories are volume-weighted, so a 12/14 FG night counts for more than 1/1."""
    if pool is None or cfg is None or not lines:
        return None
    scored = score_table(_frame(lines), pool, cfg)
    return [{"g": _r(g), "z": _r(z)} for g, z in zip(scored["g_total"], scored["z_total"], strict=True)]


def game_log(
    lines: Sequence[Mapping[str, Any]], pool: PoolStats | None = None, cfg: ValuationConfig | None = None
) -> list[dict[str, Any]]:
    rows = [game_row(line) for line in lines]
    scores = game_scores(lines, pool, cfg)
    if scores:
        for row, s in zip(rows, scores, strict=True):
            row["g_total"], row["z_total"] = s["g"], s["z"]
    return rows


def averages(lines: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    if not lines:
        return {}
    df = _frame(lines)
    means = df[list(BOX_COLS)].mean()
    return {
        "min": _r(means["minutes"], 1),
        "pts": _r(means["pts"], 1),
        "reb": _r(means["reb"], 1),
        "ast": _r(means["ast"], 1),
        "stl": _r(means["stl"], 1),
        "blk": _r(means["blk"], 1),
        "tov": _r(means["tov"], 1),
        "3pm": _r(means["tpm"], 1),
        "fg%": _pct(df["fgm"].sum(), df["fga"].sum()) or 0.0,
        "ft%": _pct(df["ftm"].sum(), df["fta"].sum()) or 0.0,
    }


# ------------------------------------------------------------------ fantasy-week breakdown


def week_lines(
    conn: sqlite3.Connection, espn_ids: Iterable[str], start: date, end: date
) -> dict[str, list[sqlite3.Row]]:
    """Games played by each player on ET dates in [start, end] (regular season), oldest first."""
    ids = list(dict.fromkeys(espn_ids))
    out: dict[str, list[sqlite3.Row]] = {pid: [] for pid in ids}
    if not ids:
        return out
    marks = ",".join("?" * len(ids))
    sql = (
        f"{_LINE_SQL} WHERE b.espn_id IN ({marks}) AND b.played = 1 AND g.season_type = 2 "
        "AND b.et_date BETWEEN ? AND ? ORDER BY b.et_date, b.event_id"
    )
    for row in conn.execute(sql, [*ids, start.isoformat(), end.isoformat()]):
        out[row["espn_id"]].append(row)
    return out


def _totals(lines: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    return {c: sum(_n(line[c]) for line in lines) for c in BOX_COLS}


def _cat_value(cat: Category, t: Mapping[str, float]) -> float | None:
    if cat.kind == "pct":
        return _pct(t[cat.made], t[cat.att])
    return t[cat.stat]


def week_box(
    players: Sequence[Mapping[str, Any]],
    lines_by_pid: Mapping[str, Sequence[Mapping[str, Any]]],
    pool: PoolStats | None = None,
    cfg: ValuationConfig | None = None,
    categories: Sequence[Category] = NINE_CAT,
) -> dict[str, Any]:
    """Per-player and team weekly totals from box scores.

    `players`: dicts with at least `id` and `player` (name); extra keys (slot, nba) are passed through.
    Each player gets games/min, counting totals, FG/FT makes/attempts, `share` of the team total per counting
    category, `pct_effect_pp` (team FG%/FT% with him minus without him, in percentage points) and `g_sum`
    (sum of his single-game G-scores), so carriers and drags are easy to read.
    """
    team_tot = _totals([line for p in players for line in lines_by_pid.get(p["id"], ())])
    rows: list[dict[str, Any]] = []
    for p in players:
        lines = list(lines_by_pid.get(p["id"], ()))
        if not lines:
            rows.append({**p, "games": 0})
            continue
        tot = _totals(lines)
        without = {c: team_tot[c] - tot[c] for c in BOX_COLS}
        cats: dict[str, Any] = {}
        for cat in categories:
            if cat.kind == "pct":
                with_pct = team_tot[cat.made] / team_tot[cat.att] if team_tot[cat.att] else 0.0
                wo_pct = without[cat.made] / without[cat.att] if without[cat.att] else 0.0
                cats[cat.label] = {
                    "made": int(tot[cat.made]),
                    "att": int(tot[cat.att]),
                    "pct": _pct(tot[cat.made], tot[cat.att]),
                    "pct_effect_pp": _r((with_pct - wo_pct) * 100, 2) if without[cat.att] else None,
                }
            else:
                cats[cat.label] = {
                    "total": int(tot[cat.stat]),
                    "share": _pct(tot[cat.stat], team_tot[cat.stat]),
                }
        row = {
            **p,
            "games": len(lines),
            "dates": [line["et_date"] for line in lines],
            "min": _r(tot["minutes"], 0),
            "totals": {
                "pts": int(tot["pts"]),
                "reb": int(tot["reb"]),
                "ast": int(tot["ast"]),
                "stl": int(tot["stl"]),
                "blk": int(tot["blk"]),
                "tov": int(tot["tov"]),
                "3pm": int(tot["tpm"]),
                "fg": f"{int(tot['fgm'])}/{int(tot['fga'])}",
                "ft": f"{int(tot['ftm'])}/{int(tot['fta'])}",
            },
            "cats": cats,
        }
        scores = game_scores(lines, pool, cfg)
        if scores:
            row["g_sum"] = _r(sum(s["g"] for s in scores))
        rows.append(row)
    rows.sort(key=lambda r: (-r.get("g_sum", -1e9), -r["games"]))
    scored = [r for r in rows if "g_sum" in r]
    team = {
        "games": sum(r["games"] for r in rows),
        "totals": {
            cat.label: (
                {"made": int(team_tot[cat.made]), "att": int(team_tot[cat.att]), "pct": _pct(*_made_att(cat, team_tot))}
                if cat.kind == "pct"
                else int(team_tot[cat.stat])
            )
            for cat in categories
        },
    }
    return {
        "players": rows,
        "team_total": team,
        "carried": [r["player"] for r in scored[:3] if r["g_sum"] > 0],
        "sank": [r["player"] for r in reversed(scored[-3:]) if r["g_sum"] < 0],
    }


def _made_att(cat: Category, totals: Mapping[str, float]) -> tuple[float, float]:
    return totals[cat.made], totals[cat.att]


def compare_banked(team_totals: Mapping[str, Any], banked: Mapping[str, float], categories: Sequence[Category]):
    """Box-score team totals minus Yahoo's banked stats per counting category (non-zero = moves / bench games)."""
    diff: dict[str, float] = {}
    for cat in categories:
        if cat.kind == "count" and cat.stat in banked and cat.label in team_totals:
            diff[cat.label] = _r(float(team_totals[cat.label]) - float(banked[cat.stat]), 1)
    return diff


# ------------------------------------------------------------------ season role split


def role_stats(gp: int, minutes: float, starts: int, fga: float, fta: float, tov: float) -> dict[str, Any]:
    """Role summary for one season: games, minutes, starts and a per-36 usage proxy (FGA + 0.44 FTA + TOV)."""
    if gp <= 0:
        return {"gp": 0}
    out: dict[str, Any] = {
        "gp": int(gp),
        "mpg": _r(minutes / gp, 1),
        "starts": int(starts),
        "start_rate": _r(starts / gp, 2),
    }
    if minutes > 0:
        out["usage_per36"] = _r((fga + 0.44 * fta + tov) / minutes * 36, 1)
    return out


def season_roles(
    conn: sqlite3.Connection, espn_ids: Iterable[str], seasons: Sequence[int]
) -> dict[str, dict[int, dict]]:
    """{player: {season: role_stats}} from games played (box scores)."""
    ids = list(dict.fromkeys(espn_ids))
    out: dict[str, dict[int, dict]] = {pid: {} for pid in ids}
    if not ids:
        return out
    id_marks, season_marks = ",".join("?" * len(ids)), ",".join("?" * len(seasons))
    sql = (
        "SELECT espn_id, season, COUNT(*) AS gp, SUM(minutes) AS minutes, SUM(starter) AS starts, "
        "SUM(fga) AS fga, SUM(fta) AS fta, SUM(tov) AS tov FROM box_lines "
        f"WHERE played = 1 AND espn_id IN ({id_marks}) AND season IN ({season_marks}) GROUP BY espn_id, season"
    )
    for r in conn.execute(sql, [*ids, *seasons]):
        out[r["espn_id"]][r["season"]] = role_stats(
            r["gp"], _n(r["minutes"]), int(_n(r["starts"])), _n(r["fga"]), _n(r["fta"]), _n(r["tov"])
        )
    return out


def role_delta(current: Mapping[str, Any], prior: Mapping[str, Any] | None) -> dict[str, float]:
    """current - prior for mpg, start_rate and usage_per36 (empty when either season has no games)."""
    if not prior or not prior.get("gp") or not current.get("gp"):
        return {}
    return {
        k: _r(current[k] - prior[k], 2 if k == "start_rate" else 1)
        for k in ("mpg", "start_rate", "usage_per36")
        if k in current and k in prior
    }


def season_role(roles: Mapping[int, Mapping[str, Any]], season: int, prior: int) -> dict[str, Any]:
    cur = dict(roles.get(season) or {"gp": 0})
    pri = dict(roles[prior]) if roles.get(prior) else None
    out: dict[str, Any] = {"season": season, "current": cur, "prior_season": prior, "prior": pri}
    out["delta"] = role_delta(cur, pri)
    if cur["gp"] < 5:
        out["note"] = f"only {cur['gp']} game(s) so far in {season}: small sample, deltas are noisy"
    return out


def compact_role(role: Mapping[str, Any]) -> dict[str, Any]:
    """Short form for roster rows: this season's role plus last season's minutes and the deltas."""
    cur, pri, delta = role["current"], role.get("prior") or {}, role.get("delta", {})
    out: dict[str, Any] = {k: cur[k] for k in ("gp", "mpg", "starts", "usage_per36") if k in cur}
    if pri.get("gp"):
        out["prior_mpg"] = pri["mpg"]
    if "mpg" in delta:
        out["d_mpg"] = delta["mpg"]
    if "usage_per36" in delta:
        out["d_usage"] = delta["usage_per36"]
    return out


def played_lines(conn: sqlite3.Connection, season: int) -> pd.DataFrame:
    """Every played box-score line of a season (ET dates), lines without minutes dropped."""
    from fbm.analytics.categories import BASE_STATS

    return pd.read_sql_query(
        "SELECT espn_id, et_date, " + ", ".join(BASE_STATS) + " FROM box_lines WHERE season = ? AND played = 1",
        conn,
        params=(season,),
    ).dropna(subset=["minutes"])


def window_actuals(lines: pd.DataFrame, start: date, end: date, min_games: int) -> pd.DataFrame:
    """Per-game averages over [start, end] (ET dates) for players with at least `min_games` games played."""
    from fbm.analytics.categories import BASE_STATS

    sub = lines[(lines["et_date"] >= start.isoformat()) & (lines["et_date"] <= end.isoformat())]
    if sub.empty:
        return pd.DataFrame(columns=[*BASE_STATS, "games"])
    grouped = sub.groupby("espn_id")
    out = grouped[list(BASE_STATS)].mean()
    out["games"] = grouped.size()
    return out[out["games"] >= min_games]
