"""Odds reliability: how good were FBM's official weekly odds (HT projected totals + simulator spread)?

Every `ht_spread` forecast in `predictions` is graded against Yahoo's FINAL result for that week (stat_winners), never
against the remaining days only. Because each payload stores HT's means and the simulator spread, the odds can be
recomputed for any spread scale (`policy.odds_spread_scale` candidates) and compared per HT view.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, date, datetime
from math import erf, sqrt
from typing import Any

from fbm.analytics.categories import NINE_CAT, Category
from fbm.yahoo import lineups
from fbm.yahoo.lineups import FinalsProvider

SOURCE = "ht_spread"
BUCKET_WIDTH = 0.1
PRIOR_N = 4.0  # pseudo-observations at 50% when mapping a bucket's forecast to its observed win rate
CONFIDENCE_EDGES = (0.5, 0.6, 0.7, 0.8, 0.9, 1.0)
MIN_PROJECTED_SHARE = 0.5  # a forecast's projected (not yet banked) part must be at least this share of its total
SHRINK_FLOOR = 0.6  # opponent shrink factors never go below this (and never above 1)
SHRINK_KEY = "policy.opponent_shrink"
BASIS_POST_FIX = "post_fix"
BASIS_PRE_FIX_DAY0 = "pre_fix_day0"


def _phi(z: float) -> float:
    return 0.5 * (1.0 + erf(z / sqrt(2.0)))


def normal_odds(
    means: Mapping[str, tuple[float, float]],
    sd: Mapping[str, float],
    scale: float,
    categories: Sequence[Category] = NINE_CAT,
) -> dict[str, float]:
    """Same contract as fbm.analytics.ht_odds.odds_from_totals: P(win) per category, normal approximation of
    (me - opp) with sd * scale; TO lower is better; ties ~ 0."""
    out = {}
    for cat in categories:
        if cat.key not in means:
            continue
        me, opp = (float(x) for x in means[cat.key])
        diff = (me - opp) if cat.higher_is_better else (opp - me)
        s = float(sd.get(cat.key) or 0.0) * float(scale)
        out[cat.key] = (1.0 if diff > 0 else 0.0 if diff < 0 else 0.5) if s <= 0 else _phi(diff / s)
    return out


def odds_from_totals(means, sd, scale, categories=NINE_CAT) -> dict[str, float]:
    try:
        from fbm.analytics.ht_odds import odds_from_totals as engine_odds
    except ImportError:
        return normal_odds(means, sd, scale, categories)
    return engine_odds(means, sd, scale, categories)


def _forecast(payload: Mapping[str, Any], scale: float | None) -> dict[str, float]:
    means, sd = payload.get("means"), payload.get("sd")
    if scale is not None and means and sd:
        pairs = {k: (v["me"], v["opp"]) for k, v in means.items()}
        return {k: float(v) for k, v in odds_from_totals(pairs, sd, scale, NINE_CAT).items()}
    return {k: float(v["p_win"]) + 0.5 * float(v.get("p_tie", 0.0)) for k, v in payload["categories"].items()}


def _utc(text: Any) -> datetime | None:
    try:
        d = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def _before_first_tip(conn: sqlite3.Connection, day: str, stamp: Any) -> bool:
    """True when `stamp` (UTC) is before the first NBA tip-off of ET `day` (nothing can be banked yet)."""
    at = _utc(stamp)
    if at is None:
        return False
    tip = conn.execute(
        "SELECT MIN(start_utc) FROM games WHERE et_date = ? AND season_type = 2 AND start_utc IS NOT NULL", (day,)
    ).fetchone()[0]
    if not tip:
        return True
    first = _utc(tip)
    return first is not None and at < first


def _ratio_input(
    conn: sqlite3.Connection, row: sqlite3.Row, payload: Mapping[str, Any], final: Mapping[str, Any]
) -> tuple[dict[str, Any] | None, str | None]:
    """What the realized/projected ratios need from one graded forecast, or (None, reason) when its projected part
    can't be isolated. post_fix: means = banked + simulated rest (issue #13) with the banked stats stored in the
    payload. pre_fix_day0: a pre-#13 forecast made before the week's first tip-off (nothing banked, so its means are
    the projected week); kept apart, reference only. Any other pre-fix forecast can't be recomputed (no banked
    stats)."""
    means = payload.get("means_raw") or payload.get("means")
    if not means:
        return None, "no_means"
    banked: dict[str, Any] | None
    if payload.get("means_source"):
        banked, basis = payload.get("banked"), BASIS_POST_FIX
        if not isinstance(banked, Mapping):
            return None, "no_banked"
    else:
        banked, basis = {"me": {}, "opp": {}}, BASIS_PRE_FIX_DAY0
        if final.get("week_start") != row["window_start"] or not _before_first_tip(
            conn, row["window_start"], payload.get("banked_at")
        ):
            return None, "pre_fix_not_day0"
    return {
        "id": row["id"],
        "week": final.get("week"),
        "opponent_key": row["opponent_key"],
        "basis": basis,
        "means": {k: (float(v["me"]), float(v["opp"])) for k, v in means.items()},
        "banked": {"me": dict(banked.get("me") or {}), "opp": dict(banked.get("opp") or {})},
        "final_me": dict(final.get("me") or {}),
        "final_opp": dict(final.get("opp") or {}),
        "sd": dict(payload.get("sd") or {}),
        "scale": float(payload.get("spread_scale") or 1.0),
    }, None


def graded_forecasts(
    conn: sqlite3.Connection,
    *,
    scale: float | None = None,
    view: str | None = None,
    finals: FinalsProvider | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """[{id, window, view, forecasts {cat: p}, outcomes {cat: 1|0.5|0}, ratio_input}] for finished weeks + count still
    pending. `ratio_input` (None when the projected part can't be isolated) feeds `realized_ratios`."""
    finals = finals or lineups.db_provider(conn)
    out, pending = [], 0
    for row in conn.execute("SELECT * FROM predictions WHERE source = ? ORDER BY id", (SOURCE,)):
        payload = json.loads(row["payload"])
        if view and payload.get("view") != view:
            continue
        final = finals(date.fromisoformat(row["window_end"]), row["team_key"], None)
        if not final or not final.get("final"):
            pending += 1
            continue
        if final.get("opponent_key") and row["opponent_key"] and final["opponent_key"] != row["opponent_key"]:
            continue  # forecast against a team that wasn't the real opponent: nothing to grade
        winners = final.get("stat_winners") or {}
        fc = _forecast(payload, scale)
        cats = [k for k in fc if k in winners]
        ratio_input, excluded = _ratio_input(conn, row, payload, final)
        out.append(
            {
                "id": row["id"],
                "window": f"{row['window_start']}..{row['window_end']}",
                "week": final.get("week"),
                "view": payload.get("view"),
                "means_source": payload.get("means_source"),
                "forecasts": {k: fc[k] for k in cats},
                "outcomes": {k: float(winners[k]) for k in cats},
                "ratio_input": ratio_input,
                "ratio_excluded": excluded,
            }
        )
    return out, pending


def _confidence_bands(pairs: Sequence[tuple[str, float, float]]) -> list[dict[str, Any]]:
    """Calibration by confidence: every forecast is folded onto its favorite (p -> max(p, 1-p), outcome -> did the
    favorite win), then grouped in 10-point bands. `gap` = favorite win rate - mean confidence (negative = the
    favorites in that band won less often than forecast)."""
    rows = []
    n_bands = len(CONFIDENCE_EDGES) - 1
    folded = [(f, o) if f >= 0.5 else (1 - f, 1 - o) for _, f, o in pairs]
    for i in range(n_bands):
        lo, hi = CONFIDENCE_EDGES[i], CONFIDENCE_EDGES[i + 1]
        sel = [(c, w) for c, w in folded if lo <= c < hi or (i == n_bands - 1 and c >= hi)]
        if not sel:
            continue
        conf = sum(c for c, _ in sel) / len(sel)
        won = sum(w for _, w in sel) / len(sel)
        rows.append(
            {
                "band": f"{round(lo * 100)}-{round(hi * 100)}%",
                "n": len(sel),
                "mean_confidence": round(conf, 3),
                "favorite_won": round(won, 3),
                "gap": round(won - conf, 3),
                "brier": round(sum((c - w) ** 2 for c, w in sel) / len(sel), 4),
            }
        )
    return rows


def _rest_pieces(rec: Mapping[str, Any], categories: Sequence[Category], min_share: float):
    """[(side, cat, projected rest, realized rest)] for one forecast: counting categories whose projected part (mean
    total - banked) is at least `min_share` of the mean total, with the realized part = Yahoo final - banked."""
    for c in categories:
        if c.kind != "count" or c.key not in rec["means"]:
            continue
        for i, (side, key) in enumerate((("us", "me"), ("opponents", "opp"))):
            mean, final = rec["means"][c.key][i], rec[f"final_{key}"].get(c.stat)
            banked = float(rec["banked"][key].get(c.stat, 0.0))
            proj = mean - banked
            if final is None or mean <= 0 or proj <= 0 or proj / mean < min_share:
                continue
            yield side, c.key, proj, float(final) - banked


def realized_ratios(
    records: Iterable[Mapping[str, Any]],
    categories: Sequence[Category] = NINE_CAT,
    basis: str = BASIS_POST_FIX,
    min_share: float = MIN_PROJECTED_SHARE,
) -> dict[str, Any]:
    """Realized / projected ratios per counting category (issue #15): how much of what the official odds projected for
    the part of the week still to play actually happened, for our side and for opponents (pooled and per opponent
    team). One forecast per week (the earliest usable one: the longest projected part); only forecasts of `basis`
    whose projected part is isolated (banked stats removed from both mean and final), so stats already banked never
    dilute the ratio. ratio = sum(realized) / sum(projected) over weeks (volume-weighted)."""
    chosen: dict[Any, int] = {}
    pooled: dict[str, dict[str, list[float]]] = {"us": {}, "opponents": {}}
    per_opp: dict[str, dict[str, dict[str, list[float]]]] = {}
    opp_weeks: dict[str, set] = {}
    for rec in sorted((r for r in records if r["basis"] == basis), key=lambda r: r["id"]):
        week = rec.get("week")
        if week in chosen:
            continue
        pieces = list(_rest_pieces(rec, categories, min_share))
        if not pieces:
            continue
        chosen[week] = rec["id"]
        opp_weeks.setdefault(rec.get("opponent_key") or "?", set()).add(week)
        for side, cat, proj, real in pieces:
            for acc in (pooled[side], per_opp.setdefault(rec.get("opponent_key") or "?", {}).setdefault(side, {})):
                tot = acc.setdefault(cat, [0.0, 0.0])
                tot[0] += proj
                tot[1] += real

    def ratios(acc: Mapping[str, list[float]]) -> dict[str, float]:
        return {k: round(v[1] / v[0], 3) for k, v in acc.items() if v[0] > 0}

    return {
        "basis": basis,
        "n_weeks": len(chosen),
        "forecast_ids": sorted(chosen.values()),
        "us": ratios(pooled["us"]),
        "opponents": ratios(pooled["opponents"]),
        "by_opponent": {
            k: {"weeks": len(opp_weeks[k]), "us": ratios(v.get("us", {})), "opponents": ratios(v.get("opponents", {}))}
            for k, v in per_opp.items()
        },
    }


def shrink_factors(ratios: Mapping[str, Any], min_weeks: int, floor: float = SHRINK_FLOOR) -> dict[str, float]:
    """Opponent projection factors {cat: f in [floor, 1]} from `realized_ratios`; empty until `min_weeks` weeks of
    isolated forecasts exist. Only ever shrinks (a ratio above 1 gives 1.0)."""
    if int(ratios.get("n_weeks", 0)) < int(min_weeks):
        return {}
    return {k: round(min(1.0, max(float(floor), float(r))), 3) for k, r in (ratios.get("opponents") or {}).items()}


def _mean(values: Iterable[float]) -> float | None:
    vals = list(values)
    return round(sum(vals) / len(vals), 3) if vals else None


def evaluate_shrink(
    rows: Sequence[Mapping[str, Any]], categories: Sequence[Category] = NINE_CAT, floor: float = SHRINK_FLOOR
) -> dict[str, Any]:
    """Brier of the post-fix forecasts' raw means vs the same means with the opponent shrunk by the ratios of the
    OTHER weeks (leave-one-week-out, so a week never shrinks itself). Only weeks with at least one other usable
    week are evaluated."""
    from fbm.analytics import ht_odds

    recs = [r["ratio_input"] for r in rows if r.get("ratio_input") and r["ratio_input"]["basis"] == BASIS_POST_FIX]
    off = on = 0.0
    n = 0
    weeks = set()
    for r in rows:
        rec = r.get("ratio_input")
        if not rec or rec["basis"] != BASIS_POST_FIX:
            continue
        others = realized_ratios([x for x in recs if x["week"] != rec["week"]], categories)
        if not others["n_weeks"]:
            continue
        factors = shrink_factors(others, 1, floor)
        shrunk = ht_odds.shrink_opponent_means(rec["means"], rec["banked"]["opp"], factors, categories)
        p_off = odds_from_totals(rec["means"], rec["sd"], rec["scale"], categories)
        p_on = odds_from_totals(shrunk, rec["sd"], rec["scale"], categories)
        for k, o in r["outcomes"].items():
            if k in p_off and k in p_on:
                off += (p_off[k] - o) ** 2
                on += (p_on[k] - o) ** 2
                n += 1
        weeks.add(rec["week"])
    return {
        "n_weeks": len(weeks),
        "n_forecasts": n,
        "brier_off": round(off / n, 4) if n else None,
        "brier_on": round(on / n, 4) if n else None,
    }


def _summary(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(rows)
    pairs = [(k, r["forecasts"][k], r["outcomes"][k]) for r in rows for k in r["forecasts"]]
    if not pairs:
        return {"forecasts": len(rows), "category_forecasts": 0, "brier": None, "n_weeks": 0, "n_forecasts": 0}
    brier = sum((f - o) ** 2 for _, f, o in pairs) / len(pairs)
    base = sum((0.5 - o) ** 2 for _, _, o in pairs) / len(pairs)
    buckets = []
    n_buckets = round(1 / BUCKET_WIDTH)
    for i in range(n_buckets):
        lo, hi = i * BUCKET_WIDTH, (i + 1) * BUCKET_WIDTH
        sel = [(f, o) for _, f, o in pairs if lo <= f < hi or (i == n_buckets - 1 and f >= hi)]
        if not sel:
            continue
        observed = sum(o for _, o in sel) / len(sel)
        buckets.append(
            {
                "range": f"{lo:.1f}-{hi:.1f}",
                "n": len(sel),
                "mean_forecast": round(sum(f for f, _ in sel) / len(sel), 3),
                "observed": round(observed, 3),
                "mapped": round((observed * len(sel) + 0.5 * PRIOR_N) / (len(sel) + PRIOR_N), 3),
            }
        )
    by_cat: dict[str, list[float]] = {}
    for k, f, o in pairs:
        by_cat.setdefault(k, []).append((f - o) ** 2)
    expected = [sum(r["forecasts"].values()) for r in rows]
    actual = [sum(r["outcomes"].values()) for r in rows]
    return {
        "forecasts": len(rows),
        "weeks": len({r["week"] for r in rows}),
        "category_forecasts": len(pairs),
        "n_weeks": len({r["week"] for r in rows}),
        "n_forecasts": len(pairs),
        "brier": round(brier, 4),
        "brier_coinflip_baseline": round(base, 4),
        "skill_vs_coinflip": round(1 - brier / base, 3) if base else None,
        "reliability": buckets,
        "calibration": _confidence_bands(pairs),
        "brier_by_category": {k: round(sum(v) / len(v), 4) for k, v in by_cat.items()},
        "mean_expected_cats": round(sum(expected) / len(expected), 2),
        "mean_actual_cats": round(sum(actual) / len(actual), 2),
    }


def _brief(rows: Sequence[Mapping[str, Any]], with_calibration: bool = False) -> dict[str, Any]:
    s = _summary(rows)
    keys = ("n_weeks", "n_forecasts", "brier", "mean_expected_cats", "mean_actual_cats")
    out = {k: s.get(k) for k in keys}
    if with_calibration:
        out["calibration"] = s.get("calibration", [])
    return out


def _shrink_status(conn: sqlite3.Connection, ratios: Mapping[str, Any]) -> dict[str, Any]:
    from fbm.constants import C
    from fbm.gm import policy

    enabled = bool(policy.get(conn, SHRINK_KEY))
    min_weeks = int(C("policy.min_weeks"))
    factors = shrink_factors(ratios, min_weeks)
    return {
        "setting": SHRINK_KEY,
        "enabled": enabled,
        "min_weeks": min_weeks,
        "graded_weeks": ratios["n_weeks"],
        "active": enabled and bool(factors),
        "factors_if_enabled": factors,
        "note": "Opponent rest-of-week means are multiplied by these factors only when the policy setting is on AND "
        "enough weeks are graded; it changes only through an owner-approved policy proposal.",
    }


def live_shrink(conn: sqlite3.Connection, params: Mapping[str, Any], view: str | None = None) -> dict[str, Any]:
    """The opponent shrink the official odds apply right now: {"enabled", "n_weeks", "factors"}. Does not read the
    forecasts at all unless the policy setting is on (default off), so the default path is unchanged."""
    from fbm.constants import C

    if not params.get(SHRINK_KEY, False):
        return {"enabled": False, "n_weeks": 0, "factors": {}}
    rows, _ = graded_forecasts(conn, view=view)
    ratios = realized_ratios([r["ratio_input"] for r in rows if r["ratio_input"]])
    return {
        "enabled": True,
        "n_weeks": ratios["n_weeks"],
        "factors": shrink_factors(ratios, int(C("policy.min_weeks"))),
    }


def opponent_ratio_report(
    conn: sqlite3.Connection, rows: Sequence[Mapping[str, Any]], detail: bool = False
) -> dict[str, Any]:
    """Compact realized/projected view of the official forecasts (issue #15), plus the shrink status."""
    usable = [r["ratio_input"] for r in rows if r.get("ratio_input")]
    post = realized_ratios(usable)
    out: dict[str, Any] = {
        "basis": "post-#13 forecasts only: (Yahoo final - banked) / (mean total - banked), counting categories, one "
        f"forecast per week, projected part >= {MIN_PROJECTED_SHARE:.0%} of the total",
        "n_weeks": post["n_weeks"],
        "forecast_ids": post["forecast_ids"],
        "us": post["us"],
        "opponents": post["opponents"],
        "mean_us": _mean(post["us"].values()),
        "mean_opponents": _mean(post["opponents"].values()),
        "by_opponent": {
            k: {"weeks": v["weeks"], "mean_ratio": _mean(v["opponents"].values())}
            | ({"opponents": v["opponents"], "us": v["us"]} if detail else {})
            for k, v in post["by_opponent"].items()
        },
        "shrink": _shrink_status(conn, post),
    }
    excluded: dict[str, int] = {}
    for r in rows:
        if not r.get("ratio_input") and r.get("ratio_excluded"):
            excluded[r["ratio_excluded"]] = excluded.get(r["ratio_excluded"], 0) + 1
    if excluded:
        out["excluded_forecasts"] = excluded
    legacy = realized_ratios(usable, basis=BASIS_PRE_FIX_DAY0)
    if legacy["n_weeks"]:
        out["pre_fix_day0_reference"] = {
            "note": "pre-#13 forecasts made before the week's first tip-off (HT grid totals as means, nothing "
            "banked): reference for the issue-#15 finding, never used for the shrink.",
            "n_weeks": legacy["n_weeks"],
            "forecast_ids": legacy["forecast_ids"],
            "us": legacy["us"],
            "opponents": legacy["opponents"],
        }
    evaluation = evaluate_shrink(rows)
    if evaluation["n_weeks"]:
        out["shrink_leave_one_week_out"] = evaluation
    return out


def report(
    conn: sqlite3.Connection,
    *,
    scale: float | None = None,
    view: str | None = None,
    finals: FinalsProvider | None = None,
    detail: bool = False,
) -> dict[str, Any]:
    """Brier vs coin flip, reliability buckets (with a shrunk-to-50% mapping), calibration by confidence band,
    per-category Brier, expected vs actual categories, the realized/projected ratios per side and opponent team
    (`opponent_ratio`, with the opponent-shrink status), and the same per HT view. `scale` recomputes every
    forecast's odds from its stored means/sd; `detail` adds per-category ratios for each opponent."""
    rows, pending = graded_forecasts(conn, scale=scale, view=view, finals=finals)
    out = {
        "source": SOURCE,
        "scale": scale if scale is not None else "as recorded",
        "view": view or "all",
        "pending_weeks_not_final": pending,
        "graded_against": "Yahoo final matchup results (stat_winners)",
        **_summary(rows),
    }
    if not rows:
        out["note"] = "No ht_spread forecasts with a Yahoo final result yet (they grade once the week is final)."
    legacy = [r for r in rows if not r.get("means_source")]
    if legacy:
        out["pre_fix_forecasts"] = len(legacy)
        out["pre_fix_note"] = (
            f"{len(legacy)} graded forecast(s) predate the issue #13 fix: their means were HT planner totals that "
            "counted days already played (inflated after day 1), and the payload has no banked stats to recompute "
            "them. Read their Brier with that in mind; forecasts with `means_source` are banked + simulated rest. "
            "They stay out of `opponent_ratio` (except the day-0 reference) and the shrink."
        )
        out["by_basis"] = {
            "pre_fix": _brief(legacy),
            "post_fix": _brief([r for r in rows if r.get("means_source")], with_calibration=True),
        }
    out["opponent_ratio"] = opponent_ratio_report(conn, rows, detail)
    views = sorted({r["view"] for r in rows if r["view"]})
    if not view and len(views) > 1:
        out["by_view"] = {v: _summary(r for r in rows if r["view"] == v) for v in views}
    return out


def best_scale(
    conn: sqlite3.Connection, candidates: Iterable[float], *, finals: FinalsProvider | None = None
) -> dict[str, Any]:
    """Brier per candidate spread scale (lowest first)."""
    results = []
    for s in candidates:
        r = report(conn, scale=float(s), finals=finals)
        if r.get("category_forecasts"):
            results.append({"scale": float(s), "brier": r["brier"], "category_forecasts": r["category_forecasts"]})
    results.sort(key=lambda x: x["brier"])
    return {"best": results[0]["scale"] if results else None, "candidates": results}
