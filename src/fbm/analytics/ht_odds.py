"""Official weekly matchup odds in HT mode: projected week totals (means) + FBM's simulator spread.

Owner decision 6 (2026-10-26): HT's displayed "probability" is a share formula, not a win probability. FBM's official
per-category odds use projected week totals as the means and the per-category standard deviation of (me - opp) from
FBM's simulator (HT per-game means, real game-to-game spread; banked stats are a fixed offset, so they add no
spread), scaled by `policy.odds_spread_scale`:

    p_win(cat) = Phi(sign * (me - opp) / (scale * sd))      sign = -1 for TO (lower is better)

P(tie) ~ 0 for these continuous totals (exactly 0.5 when both sides' totals are final and equal). Matchup-level
numbers treat categories as independent.

Means (issue #13): banked stats (Yahoo scoreboard) + the simulator's expected stats for the games still to play, at
HT's per-game means for the policy view (`means_from_totals`). They do NOT come from HT's H2H Planner AFTER totals:
that page counts every day of the week unless the owner's "Days Played" days are ticked, so banked days were counted
twice. The planner is only a cross-check (`ht_rest_gap` + `planner_check`: flags an AFTER - BEFORE that exceeds the
simulated rest of the week).

Injured players (owner request, 2026-10-26): the simulator already respects injuries (Out players can't start until
their return date, GTD/Q/D at their play probability, healthy bench players fill the freed spots). HT's grid counts
every rostered player on each of his game days, so `injury_adjustment` (the change in expected totals between the
week planned as HT counts it and the week planned with injuries respected) puts HT's gap on the same footing for the
cross-check.
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


def means_from_totals(
    totals_me: np.ndarray, totals_opp: np.ndarray, categories: Iterable[Category | str]
) -> dict[str, tuple[float, float]]:
    """Expected week totals {cat: (me, opp)} from simulated week totals (rows = simulations, banked stats included,
    simulation.STATS columns). Percent categories are mean made / mean attempted."""
    from fbm.analytics.simulation import COL

    out: dict[str, tuple[float, float]] = {}
    for c in map(_cat, categories):
        pair = []
        for t in (totals_me, totals_opp):
            m = t.mean(axis=0) if len(t) else np.zeros(t.shape[1])
            if c.kind == "pct":
                att = m[COL[c.att]]
                pair.append(float(m[COL[c.made]] / att) if att > 0 else 0.0)
            else:
                pair.append(float(m[COL[c.stat]]))
        out[c.key] = (pair[0], pair[1])
    return out


def sim_rest(
    totals_me: np.ndarray,
    totals_opp: np.ndarray,
    banked_me: Mapping[str, float],
    banked_opp: Mapping[str, float],
    categories: Iterable[Category | str],
) -> dict[str, dict[str, tuple[float, float]]]:
    """Counting categories only: the simulated games still to play, {"rest": {cat: (me, opp)} expected totals,
    "sd": {cat: (me, opp)} sd of each side's simulated total} (banked stats removed from the rest)."""
    from fbm.analytics.simulation import COL

    rest: dict[str, tuple[float, float]] = {}
    sd: dict[str, tuple[float, float]] = {}
    for c in map(_cat, categories):
        if c.kind == "pct":
            continue
        col = COL[c.stat]
        vals, sds = [], []
        for t, banked in ((totals_me, banked_me), (totals_opp, banked_opp)):
            vals.append(float(t[:, col].mean() - float(banked.get(c.stat, 0.0))) if len(t) else 0.0)
            sds.append(float(t[:, col].std()) if len(t) else 0.0)
        rest[c.key] = (vals[0], vals[1])
        sd[c.key] = (sds[0], sds[1])
    return {"rest": rest, "sd": sd}


def ht_rest_gap(
    row: Mapping[str, Mapping[str, Any]],
    categories: Iterable[Category | str],
    adj_me: Mapping[str, float] | None = None,
    adj_opp: Mapping[str, float] | None = None,
) -> dict[str, tuple[float, float]]:
    """What HT's H2H Planner implies for the rest of the week: AFTER - BEFORE per counting category, plus each side's
    injury adjustment (so it is comparable with the simulator's rest). Percent categories can't be split (no
    attempts) and are skipped, as are categories missing a value."""
    adj_me, adj_opp = adj_me or {}, adj_opp or {}
    out: dict[str, tuple[float, float]] = {}
    for c in map(_cat, categories):
        v = row.get(c.key) or {}
        if c.kind == "pct" or any(v.get(k) is None for k in ("me_before", "opp_before", "me_after", "opp_after")):
            continue
        out[c.key] = (
            float(v["me_after"]) - float(v["me_before"]) + float(adj_me.get(c.key, 0.0)),
            float(v["opp_after"]) - float(v["opp_before"]) + float(adj_opp.get(c.key, 0.0)),
        )
    return out


GUARD_SD = 3.0  # sds of a side's simulated total
GUARD_REL = 0.35  # plus this share of the simulated rest
GUARD_ABS = 2.0  # plus a couple of units of slack (rounding, a game that just ended)


def planner_check(
    gap: Mapping[str, tuple[float, float]],
    rest: Mapping[str, tuple[float, float]],
    sd: Mapping[str, tuple[float, float]],
    n_sd: float = GUARD_SD,
) -> dict[str, Any]:
    """Sanity guard (issue #13): flag every category side where HT's AFTER - BEFORE exceeds the simulator's rest of
    the week by more than `n_sd` sds (+ GUARD_REL of the rest + GUARD_ABS). That is the signature of a planner that
    counts days already played (or of an HT pull older than the banked stats). Over-counts only."""
    flags = []
    for key, g in gap.items():
        if key not in rest:
            continue
        for i, side in enumerate(("me", "opp")):
            limit = n_sd * sd.get(key, (0.0, 0.0))[i] + GUARD_REL * abs(rest[key][i]) + GUARD_ABS
            excess = g[i] - rest[key][i]
            if excess > limit:
                flags.append(
                    {
                        "cat": key,
                        "side": side,
                        "ht_rest": round(g[i], 2),
                        "sim_rest": round(rest[key][i], 2),
                        "excess": round(excess, 2),
                        "limit": round(limit, 2),
                    }
                )
    ok = not flags
    return {
        "ok": ok,
        "checked": sorted(gap),
        "flags": flags,
        "message": "HT planner AFTER - BEFORE agrees with the simulated rest of the week."
        if ok
        else f"HT planner AFTER - BEFORE exceeds the simulated rest of the week in {len(flags)} category side(s): the "
        "planner is counting days already played (or was pulled before the latest banked stats). Official odds use "
        "banked + simulated rest, so they are unaffected; re-run `fbm hashtag import` (it ticks the planner's Days "
        "Played).",
    }


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
