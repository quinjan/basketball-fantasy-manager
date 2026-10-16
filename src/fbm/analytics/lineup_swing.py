"""Swing-category tie-break for the daily lineup: weight categories by how contested they are this week."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

import pandas as pd

from fbm.analytics.simulation import MatchupOdds


def category_weights(
    odds: MatchupOdds | None,
    punts: Iterable[str] = (),
    band: float = 0.25,
    floor: float = 0.2,
) -> dict[str, float]:
    """Per-category weight in [0, 1] from this week's odds.

    p = P(win) + 0.5 P(tie). Weight is 1 at p = 0.5, falls linearly to `floor` at |p - 0.5| = band and stays at the
    floor beyond (locked or lost categories). Punted categories get 0. Empty dict when no odds are available.
    """
    if odds is None:
        return {}
    punted = set(punts)
    out: dict[str, float] = {}
    for o in odds.categories:
        key = o.category.key
        if key in punted:
            out[key] = 0.0
            continue
        dist = abs(o.p_win + 0.5 * o.p_tie - 0.5)
        closeness = max(0.0, 1.0 - dist / band) if band > 0 else 0.0
        out[key] = floor + (1.0 - floor) * closeness
    return out


def swing_categories(weights: Mapping[str, float], floor: float) -> list[str]:
    """Categories weighted above the floor (contested this week), most contested first."""
    return [k for k, w in sorted(weights.items(), key=lambda kv: -kv[1]) if w > floor + 1e-9]


def swing_values(
    values: pd.DataFrame,
    weights: Mapping[str, float],
    fallback: Mapping[str, float],
) -> dict[str, float]:
    """Per-player day value = sum_cat w_cat * g_cat; `fallback` (ROS value) when no weights are available."""
    if not weights:
        return dict(fallback)
    cols = [k for k in weights if f"g_{k}" in values.columns]
    if not cols:
        return dict(fallback)
    w = pd.Series({f"g_{k}": float(weights[k]) for k in cols})
    return values[w.index].mul(w, axis=1).sum(axis=1).to_dict()
