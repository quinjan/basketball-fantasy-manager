"""Learning loop: grade decisions against what actually happened, and check whether the simulator is calibrated.

- Predictions: every recorded matchup simulation stores per-category win odds and the planned started games. After
  the window ends, realized category results come from Yahoo's FINAL matchup result for the week (stat_winners;
  demo league: box scores of the planned player-days), and
  scored with the Brier score: (p_win + 0.5 p_tie - outcome)^2, outcome in {1, 0.5, 0}.
- Decisions: executed add/drop, waiver, trade and IL-activation proposals are graded by the realized production of
  players acquired (or activated) minus players given up, from the execution date on. Each category is scaled by a
  team-week standard deviation, so +1.0 means the move added about one team-week SD of production summed across
  categories.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from math import sqrt
from typing import Any

from fbm.analytics.categories import Category
from fbm.analytics.simulation import COL, STATS, MatchupOdds, category_values
from fbm.analytics.valuation import PoolStats
from fbm.constants import C
from fbm.gm import proposals as props
from fbm.timeutil import ET
from fbm.yahoo import lineups
from fbm.yahoo.lineups import FinalsProvider

GRADED_KINDS = ("add_drop", "waiver_claim", "trade", "il_activation")

ActualProvider = Callable[[sqlite3.Row], tuple[Mapping[str, float], Mapping[str, float]] | None]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# ------------------------------------------------------------------ predictions


def record_prediction(
    conn: sqlite3.Connection,
    odds: MatchupOdds,
    window: tuple[date, date],
    team_key: str,
    opponent_key: str,
    source: str,
    plan_me: Iterable[tuple[str, date]],
    plan_opp: Iterable[tuple[str, date]],
) -> int:
    payload = {
        "categories": {o.category.key: {"p_win": o.p_win, "p_tie": o.p_tie} for o in odds.categories},
        "expected": odds.expected_score,
        "p_majority": odds.p_majority,
        "plan_me": [[pid, d.isoformat()] for pid, d in plan_me],
        "plan_opp": [[pid, d.isoformat()] for pid, d in plan_opp],
    }
    cur = conn.execute(
        "INSERT INTO predictions(created_at, kind, window_start, window_end, team_key, opponent_key, source, payload) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (
            _now(),
            "matchup",
            window[0].isoformat(),
            window[1].isoformat(),
            team_key,
            opponent_key,
            source,
            json.dumps(payload),
        ),
    )
    conn.commit()
    return cur.lastrowid


def realized_totals(conn: sqlite3.Connection, player_days: Iterable[Sequence[str]]) -> dict[str, float]:
    """Sum of box-score stats for (espn_id, et_date) pairs that were actually played."""
    totals = dict.fromkeys(STATS, 0.0)
    for pid, day in player_days:
        row = conn.execute(
            f"SELECT {', '.join(STATS)} FROM box_lines WHERE espn_id = ? AND et_date = ? AND played = 1", (pid, day)
        ).fetchone()
        if row:
            for s in STATS:
                totals[s] += float(row[s] or 0)
    return totals


def _as_array(totals: Mapping[str, float]):
    import numpy as np

    arr = np.zeros((1, len(STATS)))
    for s, v in totals.items():
        if s in COL:
            arr[0, COL[s]] = v
    return arr


def category_outcome(cat: Category, me: Mapping[str, float], opp: Mapping[str, float]) -> float:
    a = float(category_values(_as_array(me), cat)[0])
    b = float(category_values(_as_array(opp), cat)[0])
    if abs(a - b) < 1e-9:
        return 0.5
    better = a > b if cat.higher_is_better else a < b
    return 1.0 if better else 0.0


def window_complete(conn: sqlite3.Connection, start: str, end: str) -> bool:
    pending = conn.execute(
        "SELECT COUNT(*) FROM games WHERE season_type = 2 AND et_date BETWEEN ? AND ? "
        "AND (completed = 0 OR box_synced = 0)",
        (start, end),
    ).fetchone()[0]
    return pending == 0


def _grade_result(
    payload: Mapping[str, Any],
    by_key: Mapping[str, Category],
    me: Mapping[str, float],
    opp: Mapping[str, float],
    winners: Mapping[str, float] | None = None,
    actual_source: str = "box_scores",
) -> dict[str, Any]:
    """Score a stored forecast against actual totals. Yahoo's stat_winners (when given) decide each category."""
    cats: dict[str, Any] = {}
    for key, p in payload["categories"].items():
        cat = by_key.get(key)
        if cat is None:
            continue
        outcome = float(winners[key]) if winners and key in winners else category_outcome(cat, me, opp)
        forecast = p["p_win"] + 0.5 * p.get("p_tie", 0.0)
        cats[key] = {
            "forecast": round(forecast, 4),
            "outcome": outcome,
            "brier": round((forecast - outcome) ** 2, 4),
        }
    return {
        "categories": cats,
        "expected_cats": payload["expected"],
        "actual_cats": sum(c["outcome"] for c in cats.values()),
        "brier_mean": round(sum(c["brier"] for c in cats.values()) / max(1, len(cats)), 4),
        "actual_me": dict(me),
        "actual_opp": dict(opp),
        "actual_source": actual_source,
    }


def grade_predictions(
    conn: sqlite3.Connection,
    categories: Sequence[Category],
    today: date,
    actual_provider: ActualProvider | None = None,
    finals: FinalsProvider | None = None,
) -> list[dict[str, Any]]:
    """Grade every ungraded prediction whose window has ended.

    The actual outcome of a Yahoo matchup forecast is Yahoo's FINAL result for that week (full-week totals and
    stat_winners), whatever day the forecast was made: a mid-week forecast includes the stats already banked, so it
    must be graded on the whole week, not on the remaining days' planned player-days (bug fixed 2026-10-26). Such
    predictions wait until Yahoo marks the matchup final. Demo-league predictions (source "demo") have no Yahoo
    result and keep the box-score path (planned player-days, once every game is final and synced)."""
    graded = []
    by_key = {c.key: c for c in categories}
    finals = finals or lineups.db_provider(conn)
    rows = conn.execute(
        "SELECT * FROM predictions WHERE graded_at IS NULL AND window_end < ? ORDER BY id", (today.isoformat(),)
    ).fetchall()
    for row in rows:
        payload = json.loads(row["payload"])
        actual = actual_provider(row) if actual_provider else None
        winners = None
        source = "provider"
        if actual is None and row["source"] != "demo":
            final = finals(date.fromisoformat(row["window_end"]), row["team_key"], None)
            if not final or not final.get("final"):
                continue
            if final.get("opponent_key") and row["opponent_key"] and final["opponent_key"] != row["opponent_key"]:
                skipped = {"skipped": "forecast opponent was not the real matchup", "real": final["opponent_key"]}
                conn.execute(
                    "UPDATE predictions SET graded_at = ?, result = ? WHERE id = ?",
                    (_now(), json.dumps(skipped), row["id"]),
                )
                continue
            actual, winners, source = (final["me"], final["opp"]), final.get("stat_winners"), "yahoo_final"
        elif actual is None:
            if not window_complete(conn, row["window_start"], row["window_end"]):
                continue
            actual = (realized_totals(conn, payload["plan_me"]), realized_totals(conn, payload["plan_opp"]))
            source = "box_scores"
        me, opp = actual
        result = _grade_result(payload, by_key, me, opp, winners, source)
        conn.execute(
            "UPDATE predictions SET graded_at = ?, result = ? WHERE id = ?", (_now(), json.dumps(result), row["id"])
        )
        graded.append({"id": row["id"], "window": f"{row['window_start']}..{row['window_end']}", **result})
    conn.commit()
    return graded


def calibration(
    conn: sqlite3.Connection,
    since: date | None = None,
    sources: Sequence[str] | None = None,
    exclude_sources: Sequence[str] = (),
) -> dict[str, Any]:
    sql = "SELECT * FROM predictions WHERE graded_at IS NOT NULL"
    args: list[Any] = []
    if since:
        sql += " AND window_end >= ?"
        args.append(since.isoformat())
    if sources:
        sql += f" AND source IN ({','.join('?' * len(sources))})"
        args.extend(sources)
    if exclude_sources:
        sql += f" AND source NOT IN ({','.join('?' * len(exclude_sources))})"
        args.extend(exclude_sources)
    rows = conn.execute(sql, args).fetchall()
    forecasts: list[tuple[str, float, float]] = []
    exp_vs_actual = []
    for r in rows:
        res = json.loads(r["result"])
        if "categories" not in res:  # skipped (e.g. forecast against a team that wasn't the real opponent)
            continue
        exp_vs_actual.append((res["expected_cats"], res["actual_cats"]))
        for key, c in res["categories"].items():
            forecasts.append((key, c["forecast"], c["outcome"]))
    if not forecasts:
        return {"predictions": 0, "note": "No graded predictions yet (they grade after each window ends)."}
    brier = sum((f - o) ** 2 for _, f, o in forecasts) / len(forecasts)
    baseline = sum((0.5 - o) ** 2 for _, _, o in forecasts) / len(forecasts)
    buckets = []
    for lo in (0.0, 0.2, 0.4, 0.6, 0.8):
        hi = lo + 0.2
        sel = [(f, o) for _, f, o in forecasts if lo <= f < hi or (hi >= 1.0 and f == 1.0)]
        if sel:
            buckets.append(
                {
                    "range": f"{lo:.1f}-{hi:.1f}",
                    "n": len(sel),
                    "mean_forecast": round(sum(f for f, _ in sel) / len(sel), 3),
                    "observed": round(sum(o for _, o in sel) / len(sel), 3),
                }
            )
    by_cat: dict[str, list[float]] = {}
    for key, f, o in forecasts:
        by_cat.setdefault(key, []).append((f - o) ** 2)
    return {
        "predictions": len(rows),
        "category_forecasts": len(forecasts),
        "brier": round(brier, 4),
        "brier_coinflip_baseline": round(baseline, 4),
        "skill_vs_coinflip": round(1 - brier / baseline, 3) if baseline else None,
        "reliability": buckets,
        "brier_by_category": {k: round(sum(v) / len(v), 4) for k, v in by_cat.items()},
        "mean_expected_cats": round(sum(e for e, _ in exp_vs_actual) / len(exp_vs_actual), 2),
        "mean_actual_cats": round(sum(a for _, a in exp_vs_actual) / len(exp_vs_actual), 2),
    }


# ------------------------------------------------------------------ decisions


def contribution(
    conn: sqlite3.Connection,
    pid: str,
    start: date,
    end: date,
    pool: PoolStats,
    categories: Sequence[Category],
    punts: Sequence[str] = (),
) -> dict[str, Any]:
    rows = conn.execute(
        f"SELECT {', '.join(STATS)} FROM box_lines WHERE espn_id = ? AND played = 1 AND et_date BETWEEN ? AND ?",
        (pid, start.isoformat(), end.isoformat()),
    ).fetchall()
    totals = {s: sum(float(r[s] or 0) for r in rows) for s in STATS}
    scale = sqrt(float(C("outcomes.team_starts_per_week")))
    std: dict[str, float] = {}
    for cat in categories:
        sd = pool.sd.get(cat.key) or 1.0
        if cat.kind == "pct":
            impact = (totals[cat.made] - pool.pool_pct.get(cat.key, 0.0) * totals[cat.att]) / (
                pool.mean_att.get(cat.key) or 1.0
            )
            value = impact / (sd * scale)
        else:
            value = totals[cat.stat] / (sd * scale)
            if not cat.higher_is_better:
                value = -value
        std[cat.label] = round(value, 3)
    total = sum(std[c.label] for c in categories if c.key not in punts)
    return {
        "games": len(rows),
        "raw": {k: round(v, 1) for k, v in totals.items()},
        "std": std,
        "total": round(total, 3),
    }


def _grade(delta: float, games: int, days: int) -> str:
    threshold = float(C("outcomes.good_threshold"))
    if games < int(C("outcomes.too_early_games")) and days < 4:
        return "too early"
    if delta >= threshold:
        return "good"
    if delta <= -threshold:
        return "bad"
    return "neutral"


def _et_date(iso: str) -> date:
    return datetime.fromisoformat(iso).astimezone(ET).date()


def grade_decisions(
    conn: sqlite3.Connection,
    pool: PoolStats,
    categories: Sequence[Category],
    today: date,
    punts: Sequence[str] = (),
    lookback_days: int = 28,
    names: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    names = names or {}
    cutoff = (datetime.now(UTC) - timedelta(days=lookback_days)).isoformat(timespec="seconds")
    out = []
    for p in props.list_proposals(conn, "done", 500):
        if props.effective_kind(p.kind, p.payload) not in GRADED_KINDS or not p.executed_at or p.executed_at < cutoff:
            continue
        start = _et_date(p.executed_at)
        end = today - timedelta(days=1)
        if end < start:
            end = start
        pay = p.payload
        kind = props.effective_kind(p.kind, pay)
        if kind == "il_activation":
            ins, outs = [pay["activate"]], ([pay["drop"]] if pay.get("drop") else [])
        elif kind == "trade":
            ins, outs = list(pay.get("get", [])), list(pay.get("give", []))
        else:
            ins, outs = [pay["add"]], ([pay["drop"]] if pay.get("drop") else [])
        got = [
            contribution(conn, pid, start, end, pool, categories, punts) | {"player": names.get(pid, pid)}
            for pid in ins
        ]
        gave = [
            contribution(conn, pid, start, end, pool, categories, punts) | {"player": names.get(pid, pid)}
            for pid in outs
        ]
        delta = sum(c["total"] for c in got) - sum(c["total"] for c in gave)
        games = sum(c["games"] for c in got + gave)
        by_cat = {
            c.label: round(sum(x["std"][c.label] for x in got) - sum(x["std"][c.label] for x in gave), 3)
            for c in categories
        }
        out.append(
            {
                "ref": p.ref,
                "kind": p.kind,
                "title": p.title,
                "executed_et": start.isoformat(),
                "window_et": f"{start}..{end}",
                "days": (end - start).days + 1,
                "games_counted": games,
                "delta": round(delta, 3),
                "delta_by_cat": by_cat,
                "grade": _grade(delta, games, (end - start).days + 1),
                "acquired": got,
                "gave_up": gave,
                "expected_at_proposal": p.evidence or {},
            }
        )
    return out


def weekly_retro_data(
    conn: sqlite3.Connection,
    pool: PoolStats,
    categories: Sequence[Category],
    week_start: date,
    today: date,
    punts: Sequence[str] = (),
    names: Mapping[str, str] | None = None,
    finals: FinalsProvider | None = None,
) -> dict[str, Any]:
    week_end = week_start + timedelta(days=6)
    newly_graded = grade_predictions(conn, categories, today, finals=finals)
    lo = datetime.combine(week_start, datetime.min.time(), ET).astimezone(UTC).isoformat(timespec="seconds")
    hi = (
        datetime.combine(week_end + timedelta(days=1), datetime.min.time(), ET)
        .astimezone(UTC)
        .isoformat(timespec="seconds")
    )
    created = [p for p in props.list_proposals(conn, None, 500) if lo <= p.created_at < hi]
    preds = conn.execute(
        "SELECT id, window_start, window_end, result FROM predictions WHERE graded_at IS NOT NULL "
        "AND window_end BETWEEN ? AND ?",
        (week_start.isoformat(), week_end.isoformat()),
    ).fetchall()
    return {
        "week_et": f"{week_start}..{week_end}",
        "decisions": grade_decisions(conn, pool, categories, today, punts, lookback_days=35, names=names),
        "proposals_this_week": [
            {
                "ref": p.ref,
                "kind": p.kind,
                "title": p.title,
                "status": p.status,
                "review": p.review_verdict,
                "owner_quote": p.owner_quote,
            }
            for p in created
        ],
        "predictions_graded_this_run": len(newly_graded),
        "predictions_this_week": [
            {
                "id": r["id"],
                "window": f"{r['window_start']}..{r['window_end']}",
                **{
                    k: v
                    for k, v in json.loads(r["result"]).items()
                    if k in ("expected_cats", "actual_cats", "brier_mean")
                },
            }
            for r in preds
        ],
        "calibration_season": calibration(conn, exclude_sources=("ht_spread",)),
        **decision_retro(conn, pool, categories, week_start, today, finals),
    }


def per_view_comparison(conn: sqlite3.Connection, graded: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Re-choose each graded decision under every value of its kind's HT-view setting (choosers.choose, other
    settings as in force at the time) and sum the replayed categories won by the option each view would have picked.
    This is the retro's "what works best" table."""
    from fbm.gm import choosers, policy
    from fbm.hashtag import VIEWS

    current = policy.all_values(conn)
    table: dict[str, dict[str, Any]] = {}
    for d in graded:
        key = choosers.KIND_VIEW_PARAM.get(d["kind"])
        res = d.get("result") or {}
        if not key or not res.get("options"):
            continue
        row = table.setdefault(d["kind"], {"setting": key, "in_force": {}, "views": {}})
        base = {**current, **(d.get("policy_params") or {})}
        used = base.get(key)
        row["in_force"][used] = row["in_force"].get(used, 0) + 1
        for v in VIEWS:
            try:
                pick = choosers.choose(d, {**base, key: v})
            except ValueError:
                continue
            r = res["options"].get(pick) or {}
            if r.get("cats_won") is None:
                continue
            cell = row["views"].setdefault(v, {"decisions": 0, "cats_won": 0.0, "ros_value": 0.0})
            cell["decisions"] += 1
            cell["cats_won"] += float(r["cats_won"])
            cell["ros_value"] += float(r.get("ros_value") or 0.0)
    for row in table.values():
        for cell in row["views"].values():
            cell["cats_won_mean"] = round(cell["cats_won"] / cell["decisions"], 3)
            cell["ros_value"] = round(cell["ros_value"], 3)
        if row["views"]:
            row["best_view"] = max(
                row["views"], key=lambda v: (row["views"][v]["cats_won_mean"], row["views"][v]["ros_value"])
            )
    return table


def decision_retro(
    conn: sqlite3.Connection,
    pool: PoolStats,
    categories: Sequence[Category],
    week_start: date,
    today: date,
    finals: FinalsProvider | None = None,
) -> dict[str, Any]:
    """Decision-calibration part of the weekly retro: replay grading, regret by kind, lineup regret, odds
    reliability (vs Yahoo finals) and the per-view comparison."""
    from fbm.gm import decisions as dlog
    from fbm.gm import odds_reliability, replay

    week_end = week_start + timedelta(days=6)
    out: dict[str, Any] = {}
    try:
        replay.grade_pending(conn, today, finals=finals, pool=pool, categories=categories)
    except Exception as err:  # the retro must still come back if replay fails
        out["replay_error"] = f"{type(err).__name__}: {err}"
    week = dlog.query(conn, since=week_start.isoformat(), until=week_end.isoformat(), limit=5000)
    graded = [d for d in week if d.get("result") and d["result"].get("complete")]
    rows = [
        {
            "kind": d["kind"],
            "recommended": d["result"].get("recommended"),
            "chosen": d["result"].get("chosen"),
            "best": d["result"].get("best"),
            "regret_cats": d["result"].get("regret_cats"),
        }
        for d in graded
    ]
    lineup = [r["regret_cats"] for r in rows if r["kind"] == "lineup" and r["regret_cats"] is not None]
    out.update(
        {
            "decisions_logged_this_week": len(week),
            "decisions_graded_this_week": len(graded),
            "regret_by_kind": replay.regret_summary(rows),
            "lineup_regret": {"days": len(lineup), "cats_lost_to_lineups": round(sum(lineup), 2)},
            "odds_reliability": odds_reliability.report(conn, finals=finals),
            "per_view": per_view_comparison(conn, graded),
        }
    )
    return out
