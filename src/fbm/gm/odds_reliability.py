"""Odds reliability: how good were FBM's official weekly odds (HT projected totals + simulator spread)?

Every `ht_spread` forecast in `predictions` is graded against Yahoo's FINAL result for that week (stat_winners), never
against the remaining days only. Because each payload stores HT's means and the simulator spread, the odds can be
recomputed for any spread scale (`policy.odds_spread_scale` candidates) and compared per HT view.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from math import erf, sqrt
from typing import Any

from fbm.analytics.categories import NINE_CAT, Category
from fbm.yahoo import lineups
from fbm.yahoo.lineups import FinalsProvider

SOURCE = "ht_spread"
BUCKET_WIDTH = 0.1
PRIOR_N = 4.0  # pseudo-observations at 50% when mapping a bucket's forecast to its observed win rate


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


def graded_forecasts(
    conn: sqlite3.Connection,
    *,
    scale: float | None = None,
    view: str | None = None,
    finals: FinalsProvider | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """[{id, window, view, forecasts {cat: p}, outcomes {cat: 1|0.5|0}}] for finished weeks + count still pending."""
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
        out.append(
            {
                "id": row["id"],
                "window": f"{row['window_start']}..{row['window_end']}",
                "week": final.get("week"),
                "view": payload.get("view"),
                "forecasts": {k: fc[k] for k in cats},
                "outcomes": {k: float(winners[k]) for k in cats},
            }
        )
    return out, pending


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
        "brier_by_category": {k: round(sum(v) / len(v), 4) for k, v in by_cat.items()},
        "mean_expected_cats": round(sum(expected) / len(expected), 2),
        "mean_actual_cats": round(sum(actual) / len(actual), 2),
    }


def report(
    conn: sqlite3.Connection,
    *,
    scale: float | None = None,
    view: str | None = None,
    finals: FinalsProvider | None = None,
) -> dict[str, Any]:
    """Brier vs coin flip, reliability buckets (with a shrunk-to-50% mapping), per-category Brier, expected vs actual
    categories, and the same per HT view. `scale` recomputes every forecast's odds from its stored means/sd."""
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
