"""Shared analytics inputs: projections, valuations, injury statuses, player models."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from fbm.analytics import players as player_tables
from fbm.analytics.availability import load_availability, ros_adjust, with_no_team
from fbm.analytics.simulation import PlayerModel, build_player_models
from fbm.analytics.valuation import PoolStats, ValuationConfig, value_players
from fbm.data.espn import current_season
from fbm.store.db import NO_NBA_TEAM

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
    availability: pd.DataFrame | None = None
    ros_values: pd.DataFrame | None = None
    espn_weight: tuple[float, str] = (0.0, "none")  # non-HT mode: weight on ESPN in `projections` and its source
    teamless: frozenset[str] = frozenset()  # players on no current NBA roster (waived/unsigned): no games, no value
    source: str = "engine"  # "hashtag" (HT mode: every projection is Hashtag's) | "engine" (ESPN-based engine)
    week_view: str = "STREAM7"  # HT view behind this-week sims (lineups, matchups, streams) unless one is passed
    ht_as_of: str | None = None  # date of the HT ROS projections in `projections`
    _rest_cache: tuple[dict[str, float], dict[str, frozenset]] | None = None
    _views: dict[str, pd.DataFrame] = field(default_factory=dict)

    @property
    def hashtag(self) -> bool:
        return self.source == "hashtag"

    def view_projections(self, view: str | None = None, conn: sqlite3.Connection | None = None) -> pd.DataFrame:
        """Per-game projections for an HT view (lazy, cached; players the view lacks keep their ROS rows).
        Non-HT mode has one projection table, returned for every view."""
        if not self.hashtag or not view or view.upper() == "ROS":
            return self.projections
        view = view.upper()
        if view not in self._views:
            frame = player_tables.hashtag_projection_table(conn or self.conn, view, self.season)
            extra = self.projections[~self.projections.index.isin(frame.index)]
            frame = pd.concat([frame, extra[[c for c in frame.columns if c in extra.columns]]])
            frame["team"] = self.projections["team"].reindex(frame.index).fillna(frame["team"])
            self._views[view] = frame
        return self._views[view]

    def view_g(self, view: str | None = None, conn: sqlite3.Connection | None = None) -> pd.DataFrame:
        """Per-category value table (columns g_<cat>) under a view: HT's z values for that view in HT mode, FBM's
        G-scores otherwise."""
        if not self.hashtag:
            return self.values
        frame = self.view_projections(view, conn)
        cols = {f"ht_z_{c.key}": f"g_{c.key}" for c in self.cfg.categories if f"ht_z_{c.key}" in frame.columns}
        return frame[list(cols)].rename(columns=cols).fillna(0.0)

    def has_nba_team(self, pid: str) -> bool:
        return pid not in self.teamless

    def availability_of(self, pid: str) -> tuple[float, str]:
        """Rest-of-season availability share and reason (healthy players = 1.0)."""
        av = self.availability
        if av is None or pid not in av.index:
            return 1.0, "healthy"
        return float(av.at[pid, "availability_ros"]), str(av.at[pid, "availability_reason"])

    def ros_play_probs(self) -> dict[str, float]:
        return {} if self.availability is None else self.availability["availability_ros"].astype(float).to_dict()

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

    def return_dates(self, conn: sqlite3.Connection | None = None) -> dict[str, date]:
        """ESPN expected return date (ET) per injured player, where ESPN gives one."""
        out = {}
        for r in (conn or self.conn).execute("SELECT espn_id, return_date FROM injuries_current"):
            try:
                out[r["espn_id"]] = date.fromisoformat(str(r["return_date"] or "")[:10])
            except ValueError:
                continue
        return out

    def models(
        self,
        player_ids: Iterable[str],
        conn: sqlite3.Connection | None = None,
        horizon: str = "week",
        view: str | None = None,
    ) -> dict[str, PlayerModel]:
        """Pass `conn` when calling from a different thread than the one that built this context.

        horizon="week": play probability from today's injury status (this week's matchups, daily lineups); an Out
        player with an ESPN return date plays from it at the GTD probability.
        horizon="season": play probability = rest-of-season availability share (season-level sims).
        """
        c = conn or self.conn
        ids = list(player_ids)
        # HT mode: the sim's per-game means are Hashtag's (anchor 1.0); game logs only give the spread.
        anchor = 1.0 if self.hashtag else None
        if horizon == "season":
            probs = self.ros_play_probs()
            return build_player_models(
                c,
                ids,
                [self.season, self.prior],
                self.view_projections(view or "ROS", c),
                play_probs={pid: probs.get(pid, 1.0) for pid in ids},
                anchor=anchor,
            )
        rest, b2b = self._rest_inputs(c, ids)
        return build_player_models(
            c,
            ids,
            [self.season, self.prior],
            self.view_projections(view or self.week_view, c),
            self.statuses(c),
            rest_probs=rest,
            b2b_dates=b2b,
            return_dates=self.return_dates(c),
            anchor=anchor,
        )

    def _rest_inputs(self, conn: sqlite3.Connection, ids: list[str]) -> tuple[dict[str, float], dict[str, frozenset]]:
        """Back-to-back rest risk per player and his team's back-to-back dates (computed once per context)."""
        from fbm.analytics.rest import b2b_rest_probs, team_b2b_dates

        if self._rest_cache is None:
            self._rest_cache = (
                b2b_rest_probs(conn, [self.season, self.prior]),
                team_b2b_dates(conn, self.season),
            )
        rates, by_team = self._rest_cache
        teams = self.projections["team"].astype(str) if "team" in self.projections else pd.Series(dtype=str)
        rest = {pid: rates[pid] for pid in ids if pid in rates}
        dates = {pid: by_team.get(teams.get(pid, ""), frozenset()) for pid in rest}
        return rest, dates


def build_context(
    conn: sqlite3.Connection,
    today: date,
    cfg: ValuationConfig | None = None,
    hashtag: bool = False,
    week_view: str = "STREAM7",
) -> AnalyticsContext:
    """Projections, values and availability for the analytics.

    hashtag=True (HT mode): projections are Hashtag's ROS view (`fbm.hashtag.store`); other views are loaded lazily
    per decision (`view_projections`). hashtag=False: FBM's ESPN-based engine blended with ESPN's projection at the
    constant `projection.espn_weight` (demo league / installs without HT).
    """
    from fbm.analytics.projection_calibration import resolve_espn_weight

    season = current_season(today)
    prior = season - 1
    weight: tuple[float, str] = (0.0, "none")
    if hashtag:
        projections = player_tables.hashtag_projection_table(conn, "ROS", season)
        if projections.empty:
            raise RuntimeError("No Hashtag projections imported. Run `fbm hashtag import` (or refresh_data).")
    else:
        engine = player_tables.engine_projection_table(conn, season, prior)
        weight = resolve_espn_weight(conn)
        projections = player_tables.blend_espn(engine, player_tables.espn_snapshot_table(conn, season), weight[0])
        if projections.empty:
            raise RuntimeError("No player stats stored. Run `fbm data sync` and `fbm data backfill`.")
    has_current = bool(projections["gp_current"].sum())
    current = player_tables.current_teams(conn, f"{season - 1}-07-01")
    if current:
        projections["team"] = [current.get(str(pid), team) for pid, team in projections["team"].items()]
    teamless = frozenset(projections.index[projections["team"].astype(str) == NO_NBA_TEAM])
    weekly = player_tables.weekly_table(conn, season if has_current else prior)
    values, pool = value_players(projections, cfg, weekly=None if weekly.empty else weekly)
    cfg = cfg or ValuationConfig()
    availability = with_no_team(load_availability(conn, projections["team"].astype(str).to_dict(), today), teamless)
    ros = ros_adjust(values, availability["availability_ros"].to_dict(), cfg.pool_size)
    return AnalyticsContext(
        conn,
        season,
        prior,
        projections,
        values,
        pool,
        cfg,
        availability,
        ros,
        espn_weight=weight,
        teamless=teamless,
        source="hashtag" if hashtag else "engine",
        week_view=week_view,
        ht_as_of=projections.attrs.get("as_of") if hashtag else None,
    )
