"""Shared analytics inputs: projections, valuations, injury statuses, player models."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

import pandas as pd

from fbm.analytics import players as player_tables
from fbm.analytics.simulation import PlayerModel, build_player_models
from fbm.analytics.valuation import PoolStats, ValuationConfig, value_players
from fbm.data.espn import current_season

ESPN_STATUS_TO_YAHOO = {"OUT": "O", "DAY-TO-DAY": "DTD", "SUSPENSION": "SUSP"}


@dataclass
class AnalyticsContext:
    conn: sqlite3.Connection
    season: int
    prior: int
    projections: pd.DataFrame
    values: pd.DataFrame
    pool: PoolStats
    cfg: ValuationConfig

    def value_map(self, column: str = "g_total") -> dict[str, float]:
        return self.values[column].to_dict()

    def statuses(self, conn: sqlite3.Connection | None = None) -> dict[str, str]:
        out = {}
        for r in (conn or self.conn).execute("SELECT espn_id, status, fantasy_status FROM injuries_current"):
            fantasy = (r["fantasy_status"] or "").upper()
            out[r["espn_id"]] = (
                "O"
                if fantasy == "OFS"
                else fantasy or ESPN_STATUS_TO_YAHOO.get((r["status"] or "").upper(), r["status"] or "")
            )
        return out

    def models(self, player_ids: Iterable[str], conn: sqlite3.Connection | None = None) -> dict[str, PlayerModel]:
        """Pass `conn` when calling from a different thread than the one that built this context."""
        c = conn or self.conn
        return build_player_models(c, player_ids, [self.season, self.prior], self.projections, self.statuses(c))


def build_context(conn: sqlite3.Connection, today: date, cfg: ValuationConfig | None = None) -> AnalyticsContext:
    season = current_season(today)
    prior = season - 1
    projections = player_tables.projection_table(conn, season, prior)
    if projections.empty:
        raise RuntimeError("No player stats stored. Run `fbm data sync` and `fbm data backfill`.")
    has_current = bool(projections["gp_current"].sum())
    weekly = player_tables.weekly_table(conn, season if has_current else prior)
    values, pool = value_players(projections, cfg, weekly=None if weekly.empty else weekly)
    return AnalyticsContext(conn, season, prior, projections, values, pool, cfg or ValuationConfig())
