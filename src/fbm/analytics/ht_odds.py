"""Official weekly matchup odds in HT mode: Hashtag's projected week totals (means) + FBM's simulator spread.

Owner decision 6 (2026-10-26): HT's displayed "probability" is a share formula, not a win probability. FBM's official
per-category odds use HT's H2H Planner totals (banked + projected rest of week) as the means and the per-category
standard deviation of (me - opp) from FBM's simulator (HT per-game means, real game-to-game spread; banked stats are a
fixed offset, so they add no spread), scaled by `policy.odds_spread_scale`:

    p_win(cat) = Phi(sign * (me - opp) / (scale * sd))      sign = -1 for TO (lower is better)

P(tie) ~ 0 for these continuous totals. Matchup-level numbers treat categories as independent.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

import numpy as np

from fbm.analytics.categories import NINE_CAT, Category

_BY_KEY = {c.key: c for c in NINE_CAT}


def _cat(c: Category | str) -> Category:
    return c if isinstance(c, Category) else _BY_KEY[str(c)]


def _phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def odds_from_totals(
    means: Mapping[str, tuple[float, float]],
    sd: Mapping[str, float],
    scale: float,
    categories: Iterable[Category | str],
) -> dict[str, float]:
    """{cat: P(I win the category)} from projected week totals {cat: (me, opp)} and the sd of (me - opp)."""
    out: dict[str, float] = {}
    for c in map(_cat, categories):
        if c.key not in means:
            continue
        me, opp = (float(x) for x in means[c.key])
        diff = me - opp if c.higher_is_better else opp - me
        s = float(sd.get(c.key, 0.0) or 0.0) * float(scale)
        if s <= 0 or not math.isfinite(s):
            out[c.key] = 1.0 if diff > 0 else 0.0 if diff < 0 else 0.5
        else:
            out[c.key] = _phi(diff / s)
    return out


def win_distribution(p_win: Mapping[str, float]) -> list[float]:
    """P(exactly k categories won), k = 0..n, for independent categories (no ties)."""
    dist = [1.0]
    for p in p_win.values():
        p = min(max(float(p), 0.0), 1.0)
        nxt = [0.0] * (len(dist) + 1)
        for k, q in enumerate(dist):
            nxt[k] += q * (1 - p)
            nxt[k + 1] += q * p
        dist = nxt
    return dist


def summary(p_win: Mapping[str, float]) -> dict[str, float]:
    """expected categories won, P(win the matchup) = P(won > lost), P(lose it)."""
    n = len(p_win)
    dist = win_distribution(p_win)
    return {
        "expected": float(sum(p_win.values())),
        "p_majority": float(sum(q for k, q in enumerate(dist) if k > n - k)),
        "p_majority_loss": float(sum(q for k, q in enumerate(dist) if k < n - k)),
    }


def spread(totals_me: np.ndarray, totals_opp: np.ndarray, categories: Sequence[Category | str]) -> dict[str, float]:
    """Per-category sd of (me - opp) over simulated week totals (rows = simulations; simulation.STATS columns).
    Percent categories use made/attempted of each simulated week."""
    from fbm.analytics.simulation import category_values

    out = {}
    for c in map(_cat, categories):
        d = category_values(totals_me, c) - category_values(totals_opp, c)
        out[c.key] = float(np.std(d)) if len(d) else 0.0
    return out


def payload(
    p_win: Mapping[str, float],
    means: Mapping[str, tuple[float, float]],
    sd: Mapping[str, float],
    scale: float,
    view: str,
    banked_at: str | None = None,
    plan_me: list | None = None,
    plan_opp: list | None = None,
) -> dict[str, Any]:
    """The `predictions.payload` of a `ht_spread` forecast (contract B)."""
    s = summary(p_win)
    return {
        "categories": {k: {"p_win": round(float(p), 4), "p_tie": 0.0} for k, p in p_win.items()},
        "expected": round(s["expected"], 4),
        "p_majority": round(s["p_majority"], 4),
        "view": view,
        "method": "ht_totals+sim_spread",
        "spread_scale": float(scale),
        "means": {k: {"me": float(m[0]), "opp": float(m[1])} for k, m in means.items()},
        "sd": {k: float(v) for k, v in sd.items()},
        "banked_at": banked_at,
        "plan_me": plan_me or [],
        "plan_opp": plan_opp or [],
    }
