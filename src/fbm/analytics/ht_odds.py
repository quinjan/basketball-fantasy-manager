"""Official weekly matchup odds in HT mode: Hashtag's projected week totals (means) + FBM's simulator spread.

Owner decision 6 (2026-10-26): HT's displayed "probability" is a share formula, not a win probability. FBM's official
per-category odds use HT's H2H Planner totals (banked + projected rest of week) as the means and the per-category
standard deviation of (me - opp) from FBM's simulator (HT per-game means, real game-to-game spread; banked stats are a
fixed offset, so they add no spread), scaled by `policy.odds_spread_scale`:

    p_win(cat) = Phi(sign * (me - opp) / (scale * sd))      sign = -1 for TO (lower is better)

P(tie) ~ 0 for these continuous totals. Matchup-level numbers treat categories as independent.

Injured players (owner request, 2026-10-26): HT's grid counts every rostered player on each of his game days (Out and
IL players included), capped at the active spots. FBM removes that by adding `injury_adjustment` to HT's totals: the
change in expected totals (HT per-game means) between the week planned as HT counts it (everyone healthy) and the
week planned with injuries respected (Out players can't start until their return date, GTD/Q/D at their play
probability, healthy bench players fill the freed spots).
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


def shift_opponent(
    means: Mapping[str, tuple[float, float]], opp_delta: Mapping[str, float]
) -> dict[str, tuple[float, float]]:
    """Projected week totals with the opponent's totals moved by `opp_delta` (e.g. their modeled streams)."""
    return {k: (float(me), float(opp) + float(opp_delta.get(k, 0.0))) for k, (me, opp) in means.items()}


def expected_line(games: Iterable[tuple[str, Any]], models: Mapping[str, Any], available: bool) -> np.ndarray:
    """Expected stat totals (simulation.STATS columns) of started games [(player_id, ET day)] at each player's per-game
    mean; `available` weights each game by injury/status availability (Out = 0 until the return date, GTD/Q/D
    their play probability; no back-to-back rest risk), else every game counts in full (as HT's grid does)."""
    from fbm.analytics.simulation import STATS

    out = np.zeros(len(STATS))
    for pid, day in games:
        m = models.get(pid)
        if m is None:
            continue
        out += m.mean * (m.game_play_prob(day, rest=False) if available else 1.0)
    return out


def injury_adjustment(
    as_ht: np.ndarray, available: np.ndarray, categories: Iterable[Category | str]
) -> dict[str, float]:
    """Per-category change to HT's week total from not counting injured players: `as_ht` = expected line of the week
    planned the way HT's grid counts it (every rostered player active and healthy), `available` = the week planned
    with injuries respected (out players can't start, healthy bench players take their spots). Percent categories
    move by the change in made/attempted."""
    from fbm.analytics.simulation import COL

    out: dict[str, float] = {}
    for c in map(_cat, categories):
        if c.kind == "pct":
            a = (as_ht[COL[c.made]], as_ht[COL[c.att]])
            b = (available[COL[c.made]], available[COL[c.att]])
            out[c.key] = (b[0] / b[1] if b[1] > 0 else 0.0) - (a[0] / a[1] if a[1] > 0 else 0.0)
        else:
            out[c.key] = float(available[COL[c.stat]] - as_ht[COL[c.stat]])
    return out


def apply_adjustment(
    means: Mapping[str, tuple[float, float]], me: Mapping[str, float], opp: Mapping[str, float]
) -> dict[str, tuple[float, float]]:
    """HT totals with each side's per-category adjustment added (counting totals floored at 0, pct clipped 0..1)."""
    out = {}
    for k, (m, o) in means.items():
        m2, o2 = float(m) + float(me.get(k, 0.0)), float(o) + float(opp.get(k, 0.0))
        if _cat(k).kind == "pct":
            m2, o2 = min(max(m2, 0.0), 1.0), min(max(o2, 0.0), 1.0)
        else:
            m2, o2 = max(m2, 0.0), max(o2, 0.0)
        out[k] = (m2, o2)
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
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The `predictions.payload` of a `ht_spread` forecast (contract B); `extra` adds audit fields (e.g. the raw HT
    grid totals before the injury adjustment)."""
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
        **(extra or {}),
    }
