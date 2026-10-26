"""Category multipliers sent to Hashtag Basketball (waiver wire / trade finder weights).

HT only accepts a fixed set of multipliers per category (HT_STEPS); a punted category is simply not ticked, so it is
left out of the returned dict.

Season multipliers (`season_multipliers`): `policy.season_weights` (owner-approved via a policy proposal) wins when
non-empty; otherwise derived from the approved strategy: targets x1.25, everything else x1, punts excluded. Soft
concessions written only in the strategy notes are not machine-readable and are ignored.

Week multipliers (`week_multipliers`) map `choosers.classify`/`week_weights` onto HT steps:
    punt     -> excluded
    concede  -> x0.25
    lock     -> x0.5
    hold     -> x0.5 (already won or lost but not decided: the weight floor region)
    chase    -> 1 + 0.5 * closeness, where closeness = (week_weight - floor) / (1 - floor) in [0, 1],
                snapped: a dead-even category gets x1.5, the edge of the contested band x1.
`combine(season, week)` multiplies the two and snaps again; a category missing from either side is excluded.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from fbm.gm import choosers
from fbm.hashtag import CATEGORY_KEYS

HT_STEPS = (0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0)
TARGET_MULTIPLIER = 1.25
DISPLAY_TO_KEY = {
    "FG%": "fg_pct",
    "FT%": "ft_pct",
    "3PM": "tpm",
    "3PTM": "tpm",
    "PTS": "pts",
    "REB": "reb",
    "AST": "ast",
    "STL": "stl",
    "BLK": "blk",
    "TO": "tov",
    "TOV": "tov",
}


def cat_key(name: str) -> str:
    """'FT%' / 'ft_pct' / 'TO' -> FBM category key; raises ValueError for unknown names."""
    s = str(name).strip()
    if s.lower() in CATEGORY_KEYS:
        return s.lower()
    key = DISPLAY_TO_KEY.get(s.upper())
    if key is None:
        raise ValueError(f"Unknown category '{name}'.")
    return key


def snap(x: float) -> float:
    """Nearest HT multiplier step (ties go to the lower step)."""
    return min(HT_STEPS, key=lambda s: (abs(s - float(x)), s))


def season_multipliers(strategy: Mapping[str, Any], params: Mapping[str, Any]) -> dict[str, float]:
    punts = {cat_key(c) for c in strategy.get("punts") or []}
    explicit = params.get("policy.season_weights") or {}
    if explicit:
        mults = {cat_key(k): snap(v) for k, v in explicit.items()}
        out = {c: mults.get(c, 1.0) for c in CATEGORY_KEYS}
    else:
        targets = {cat_key(c) for c in strategy.get("targets") or []}
        out = {c: (TARGET_MULTIPLIER if c in targets else 1.0) for c in CATEGORY_KEYS}
    return {c: snap(v) for c, v in out.items() if c not in punts}


def week_multipliers(
    odds: Mapping[str, float], params: Mapping[str, Any], punts: Iterable[str] = ()
) -> dict[str, float]:
    punted = {cat_key(c) for c in punts}
    odds = {cat_key(c): float(p) for c, p in odds.items()}
    status = choosers.classify(odds, params, punted)
    weights = choosers.week_weights(odds, params, punted)
    floor = float(params["policy.weight_floor"])
    out: dict[str, float] = {}
    for cat, st in status.items():
        if st == "punt":
            continue
        if st == "concede":
            out[cat] = 0.25
        elif st in ("lock", "hold"):
            out[cat] = 0.5
        else:
            closeness = (weights[cat] - floor) / (1.0 - floor) if floor < 1 else 1.0
            out[cat] = snap(1.0 + 0.5 * max(0.0, min(1.0, closeness)))
    return out


def combine(season: Mapping[str, float], week: Mapping[str, float]) -> dict[str, float]:
    return {c: snap(float(season[c]) * float(week[c])) for c in season if c in week}
