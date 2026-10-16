"""9-cat player valuation: Z-scores and G-scores (Rosenof, arXiv:2307.02188).

G = (mu_q - mu) / sqrt(sigma^2 + kappa * tau^2)
  sigma: SD of per-game means across the pool (player-to-player)
  tau:   RMS over pool players of each player's week-to-week SD (period-to-period)
  kappa: 2N / (2N - 1), N = roster size per team
Percentage cats use volume-weighted impact: (att_q / mean_att) * (pct_q - pool_pct), pool_pct = sum(made)/sum(att).
Z-scores are the special case tau = 0.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from fbm.analytics.categories import NINE_CAT, Category


@dataclass(frozen=True)
class ValuationConfig:
    pool_size: int = 156  # rostered players in the league (teams x roster spots)
    roster_size: int = 13
    punts: tuple[str, ...] = ()  # category keys excluded from totals
    min_gp: int = 5
    min_minutes: float = 10.0
    iterations: int = 3
    categories: Sequence[Category] = field(default=NINE_CAT)

    @property
    def kappa(self) -> float:
        n = self.roster_size
        return 2 * n / (2 * n - 1)


@dataclass
class PoolStats:
    mean: dict[str, float]
    sd: dict[str, float]
    tau2: dict[str, float]
    pool_pct: dict[str, float]
    mean_att: dict[str, float]
    pool_ids: list[str]


def _impact(df: pd.DataFrame, cat: Category, pool_pct: float, mean_att: float) -> pd.Series:
    att = df[cat.att].astype(float)
    pct = np.where(att > 0, df[cat.made].astype(float) / att.replace(0, np.nan), pool_pct)
    return (att / mean_att) * (pd.Series(pct, index=df.index) - pool_pct)


def _metric(df: pd.DataFrame, cat: Category, pool_pct: float, mean_att: float) -> pd.Series:
    """The per-player quantity being standardized for a category (raw for counts, impact for pcts)."""
    if cat.kind == "pct":
        return _impact(df, cat, pool_pct, mean_att)
    return df[cat.stat].astype(float)


def _tau2(
    weekly: pd.DataFrame | None,
    pool_ids: list[str],
    cat: Category,
    pool_pct: float,
    mean_att: float,
    min_weeks: int = 4,
) -> float:
    if weekly is None or weekly.empty:
        return 0.0
    w = weekly[weekly["espn_id"].isin(pool_ids)]
    if w.empty:
        return 0.0
    values = _metric(w, cat, pool_pct, mean_att)
    by_player = values.groupby(w["espn_id"])
    counts = by_player.size()
    variances = by_player.var(ddof=0)[counts >= min_weeks]
    return float(variances.mean()) if len(variances) else 0.0


def _pool_stats(df: pd.DataFrame, pool_ids: list[str], cfg: ValuationConfig, weekly: pd.DataFrame | None) -> PoolStats:
    pool = df.loc[pool_ids]
    stats = PoolStats({}, {}, {}, {}, {}, pool_ids)
    for cat in cfg.categories:
        if cat.kind == "pct":
            att_sum = float(pool[cat.att].sum())
            stats.pool_pct[cat.key] = float(pool[cat.made].sum()) / att_sum if att_sum else 0.0
            stats.mean_att[cat.key] = float(pool[cat.att].mean()) or 1.0
        else:
            stats.pool_pct[cat.key] = 0.0
            stats.mean_att[cat.key] = 1.0
        metric = _metric(pool, cat, stats.pool_pct[cat.key], stats.mean_att[cat.key])
        stats.mean[cat.key] = 0.0 if cat.kind == "pct" else float(metric.mean())
        stats.sd[cat.key] = float(metric.std(ddof=0)) or 1.0
        stats.tau2[cat.key] = _tau2(weekly, pool_ids, cat, stats.pool_pct[cat.key], stats.mean_att[cat.key])
    return stats


def _score(df: pd.DataFrame, stats: PoolStats, cfg: ValuationConfig) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)
    punts = set(cfg.punts)
    z_total = pd.Series(0.0, index=df.index)
    g_total = pd.Series(0.0, index=df.index)
    for cat in cfg.categories:
        metric = _metric(df, cat, stats.pool_pct[cat.key], stats.mean_att[cat.key])
        diff = metric - stats.mean[cat.key]
        if not cat.higher_is_better:
            diff = -diff
        sd = stats.sd[cat.key]
        z = diff / sd
        g = diff / np.sqrt(sd**2 + cfg.kappa * stats.tau2[cat.key])
        out[f"z_{cat.key}"] = z
        out[f"g_{cat.key}"] = g
        if cat.key not in punts:
            z_total += z
            g_total += g
    out["z_total"] = z_total
    out["g_total"] = g_total
    return out


def value_players(
    table: pd.DataFrame, cfg: ValuationConfig | None = None, weekly: pd.DataFrame | None = None
) -> tuple[pd.DataFrame, PoolStats]:
    """Score every qualified player. Pool = top `pool_size` by Z total, refined iteratively."""
    cfg = cfg or ValuationConfig()
    df = table[(table["gp"] >= cfg.min_gp) & (table["minutes"] >= cfg.min_minutes)].copy()
    if df.empty:
        raise ValueError("No players qualify for valuation (check min_gp / min_minutes or sync data).")
    pool_ids = list(df.index)
    stats = _pool_stats(df, pool_ids, cfg, None)
    for _ in range(cfg.iterations):
        scores = _score(df, stats, cfg)
        pool_ids = list(scores["z_total"].nlargest(min(cfg.pool_size, len(df))).index)
        stats = _pool_stats(df, pool_ids, cfg, None)
    stats = _pool_stats(df, pool_ids, cfg, weekly)
    scores = _score(df, stats, cfg)
    result = df.join(scores)
    result["rank_z"] = result["z_total"].rank(ascending=False, method="min").astype(int)
    result["rank_g"] = result["g_total"].rank(ascending=False, method="min").astype(int)
    return result.sort_values("g_total", ascending=False), stats


def config_from_settings(summary: dict | None, punts: tuple[str, ...] = (), **overrides) -> ValuationConfig:
    """Derive pool/roster size and categories from Yahoo settings (falls back to 12 teams x 13 spots, 9-cat)."""
    from fbm.analytics.categories import from_league_settings

    if not summary:
        return ValuationConfig(punts=punts, **overrides)
    teams = int(summary.get("num_teams") or 12)
    active = [r for r in summary.get("roster_slots", []) if r["position"] not in ("IL", "IL+", "NA")]
    roster = sum(r["count"] for r in active) or 13
    return ValuationConfig(
        pool_size=teams * roster,
        roster_size=roster,
        punts=punts,
        categories=from_league_settings(summary),
        **overrides,
    )
