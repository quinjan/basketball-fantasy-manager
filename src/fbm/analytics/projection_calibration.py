"""Retired (2026-10-26): FBM no longer calibrates projections (Hashtag Basketball owns projection accuracy).

The ESPN blend weight is the constant `projection.espn_weight` (non-HT mode only); `projection_log` is kept
read-only. `played_lines` / `window_actuals` moved to `fbm.analytics.boxscores` (re-exported here).
"""

from __future__ import annotations

import sqlite3

from fbm.analytics.boxscores import played_lines, window_actuals
from fbm.constants import C, overrides

WEIGHT_KEY = "projection.espn_weight"

__all__ = ["WEIGHT_KEY", "played_lines", "resolve_espn_weight", "window_actuals"]


def resolve_espn_weight(conn: sqlite3.Connection | None = None) -> tuple[float, str]:
    """Weight on ESPN in the non-HT projection blend: constants override, else the default (no measuring)."""
    return float(C(WEIGHT_KEY)), "override (config/constants.toml)" if WEIGHT_KEY in overrides() else "default"
