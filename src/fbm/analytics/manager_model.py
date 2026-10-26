"""Empirical-Bayes manager behavior model: shrink each team's observed transaction rates toward a league prior.

Pure functions over `fbm.yahoo.league_reads.Transaction` records. For every team:

- rates per week (Gamma-Poisson): adds, waiver claims (incl. lost claims if the feed exposes them), free-agent
  adds, drops, trade events (proposed, or completed as a party);
- probabilities (Beta-Binomial): claim participation (share of weeks with >= 1 waiver claim), stream fraction
  (adds dropped again within C("intel.stream_days")), near-limit rate (share of weeks with adds within
  C("intel.near_limit_margin") of the league's weekly add limit: the streaming signal).

Prior = league-wide pooled rate (all teams' transactions, same recency weights), blended with the engine constant
as a weak anchor (C("intel.league_prior_weight")); with no league history at all it is the constant alone.
Observations are recency-weighted (half-life C("intel.half_life_days")); the days before the season start (the
draft-day-after churn) count at C("intel.preseason_weight"). Each `Estimate` carries prior, observed, posterior,
sd and `ess` (the weighted weeks/trials of evidence) so a consumer can tell evidence from prior.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta

from fbm.constants import C
from fbm.yahoo.league_reads import Transaction

LOST_STATUSES = ("failed", "lost", "denied", "rejected")  # a claim that did not go through


@dataclass(frozen=True)
class Estimate:
    prior: float
    observed: float | None  # None until there is exposure to observe
    posterior: float
    sd: float
    ess: float  # weighted weeks (rates) or weighted weeks/adds (probabilities) of evidence
    evidence_share: float  # share of the posterior's weight that comes from observations (0 = all prior)

    def to_dict(self) -> dict:
        return {k: None if v is None else round(v, 3) for k, v in asdict(self).items()}


def rate_estimate(count: float, exposure: float, prior_mean: float, strength: float) -> Estimate:
    """Gamma-Poisson: events per unit of exposure, prior worth `strength` units at `prior_mean`."""
    alpha, beta = prior_mean * strength + count, strength + exposure
    return Estimate(
        prior=prior_mean,
        observed=count / exposure if exposure > 0 else None,
        posterior=alpha / beta,
        sd=math.sqrt(alpha) / beta,
        ess=exposure,
        evidence_share=exposure / beta if beta > 0 else 0.0,
    )


def prob_estimate(successes: float, trials: float, prior_mean: float, strength: float) -> Estimate:
    """Beta-Binomial: probability of success, prior worth `strength` trials at `prior_mean`."""
    a = prior_mean * strength + successes
    b = (1 - prior_mean) * strength + max(trials - successes, 0.0)
    n = a + b
    return Estimate(
        prior=prior_mean,
        observed=successes / trials if trials > 0 else None,
        posterior=a / n if n > 0 else prior_mean,
        sd=math.sqrt(a * b / (n * n * (n + 1))) if n > 0 else 0.0,
        ess=trials,
        evidence_share=trials / n if n > 0 else 0.0,
    )


@dataclass
class ManagerModel:
    team_key: str
    name: str
    league_history: bool  # False: the prior is just the engine constants
    exposure_weeks: float  # recency/pre-season weighted weeks observed
    adds_per_week: Estimate
    claims_per_week: Estimate
    fa_adds_per_week: Estimate
    drops_per_week: Estimate
    trade_events_per_week: Estimate
    claim_participation: Estimate
    stream_fraction: Estimate
    near_limit_rate: Estimate | None  # None when the league has no weekly add limit
    claims_lost: int = 0
    pre_season_adds: int = 0

    def to_dict(self) -> dict:
        out = {k: v.to_dict() if isinstance(v, Estimate) else v for k, v in self.__dict__.items()}
        out.pop("team_key", None)
        out.pop("name", None)
        return out

    def summary(self) -> dict:
        """Compact posterior view for tool rows."""
        return {
            "adds_per_week": round(self.adds_per_week.posterior, 2),
            "claim_participation": round(self.claim_participation.posterior, 2),
            "stream_fraction": round(self.stream_fraction.posterior, 2),
            "evidence_weeks": round(self.exposure_weeks, 2),
            "evidence_share": round(self.adds_per_week.evidence_share, 2),
            "league_history": self.league_history,
        }


@dataclass
class _Raw:
    adds: float = 0.0
    claims: float = 0.0
    fa: float = 0.0
    drops: float = 0.0
    trades: float = 0.0
    streams: float = 0.0
    claim_bins: set[int] = field(default_factory=set)
    adds_by_bin: dict[int, int] = field(default_factory=dict)
    lost: int = 0
    pre_adds: int = 0


def _weights(window_start: datetime, now: datetime, half_life: float, cut: datetime | None, pre_w: float):
    """(weight of an instant, per-week-bin weighted weeks, total weighted weeks, bin of an instant)."""
    half_life = max(half_life, 1e-9)
    monday = (window_start - timedelta(days=window_start.weekday())).date()

    def weight(ts: datetime) -> float:
        w = 0.5 ** (max((now - ts).total_seconds(), 0.0) / 86400 / half_life)
        return w * pre_w if cut is not None and ts < cut else w

    def bin_of(ts: datetime) -> int:
        return (ts.date() - monday).days // 7

    bins: dict[int, float] = {}
    day = datetime.combine(window_start.date(), datetime.min.time(), UTC)
    while day < now:
        lo, hi = max(day, window_start), min(day + timedelta(days=1), now)
        if hi > lo:
            frac = (hi - lo).total_seconds() / 86400
            b = bin_of(lo)
            bins[b] = bins.get(b, 0.0) + frac * weight(lo + (hi - lo) / 2) / 7
        day += timedelta(days=1)
    return weight, bins, sum(bins.values()), bin_of


def _lost_claim(tx: Transaction) -> bool:
    return tx.type in ("add", "add/drop", "waiver") and tx.status in LOST_STATUSES


def _ok(tx: Transaction) -> bool:
    return tx.type in ("add", "drop", "add/drop") and tx.status in ("successful", "")


def estimate_managers(
    transactions: Iterable[Transaction],
    teams: Mapping[str, str],
    now: datetime,
    league_start: datetime | None = None,
    max_weekly_adds: int | None = None,
) -> dict[str, ManagerModel]:
    """Empirical-Bayes model for every team in `teams` (team_key -> name)."""
    txs = sorted((t for t in transactions if t.timestamp is not None), key=lambda t: t.timestamp)
    half_life, pre_w = float(C("intel.half_life_days")), float(C("intel.preseason_weight"))
    k_rate, k_prob = float(C("intel.prior_weeks")), float(C("intel.prior_trials"))
    k_league = float(C("intel.league_prior_weight"))
    stream_window = timedelta(days=float(C("intel.stream_days")))
    near = None if max_weekly_adds is None else max(int(max_weekly_adds) - int(C("intel.near_limit_margin")), 1)
    window_start = txs[0].timestamp if txs else now
    if league_start is not None:
        window_start = min(window_start, league_start)
    cut = league_start if league_start is not None and txs and txs[0].timestamp < league_start else None
    weight, bins, exposure, bin_of = _weights(window_start, now, half_life, cut, pre_w)

    raw = {k: _Raw() for k in teams}
    adds: dict[str, list[tuple[datetime, str]]] = {k: [] for k in teams}
    drops: dict[str, list[tuple[datetime, str]]] = {k: [] for k in teams}
    for tx in txs:
        ts, w = tx.timestamp, weight(tx.timestamp)
        if tx.type == "trade":
            parties = {tx.trader_team_key, tx.tradee_team_key} if tx.status == "successful" else {tx.trader_team_key}
            for key in parties & set(teams):
                raw[key].trades += w
            continue
        lost = _lost_claim(tx)
        if not (lost or _ok(tx)):
            continue
        for p in tx.players:
            if p.action == "add" and p.destination_team_key in teams:
                r = raw[p.destination_team_key]
                if lost:
                    r.lost += 1
                    r.claims += w
                    r.claim_bins.add(bin_of(ts))
                    continue
                r.adds += w
                r.adds_by_bin[bin_of(ts)] = r.adds_by_bin.get(bin_of(ts), 0) + 1
                r.pre_adds += 1 if cut is not None and ts < cut else 0
                adds[p.destination_team_key].append((ts, p.player_id))
                if p.source_type == "waivers":
                    r.claims += w
                    r.claim_bins.add(bin_of(ts))
                elif p.source_type == "freeagents":
                    r.fa += w
            elif p.action == "drop" and p.source_team_key in teams and not lost:
                raw[p.source_team_key].drops += w
                drops[p.source_team_key].append((ts, p.player_id))
    for key, items in adds.items():
        for ts, pid in items:
            if any(pid == dp and ts < dts <= ts + stream_window for dts, dp in drops[key]):
                raw[key].streams += weight(ts)

    n = max(len(teams), 1)
    history = bool(txs) and exposure > 0

    def pooled_rate(attr: str, fallback: float | None) -> float:
        if not history:
            return fallback or 0.0
        total = sum(getattr(r, attr) for r in raw.values())
        if fallback is None:
            return total / (exposure * n)
        return (total + k_league * fallback) / (exposure * n + k_league)

    # per-team week-bin outcomes: (successes, trials) with trials = weighted weeks observed
    def claim_counts(r: _Raw) -> tuple[float, float]:
        return sum(bins[b] for b in r.claim_bins if b in bins), exposure

    def near_counts(r: _Raw) -> tuple[float, float]:
        return sum(bins[b] for b, c in r.adds_by_bin.items() if near is not None and c >= near and b in bins), exposure

    def pooled_prob(successes: float, trials: float, fallback: float) -> float:
        if not history or trials <= 0:
            return fallback
        return (successes + k_league * fallback) / (trials + k_league)

    floor = float(C("intel.activity_floor"))
    part_prior = pooled_prob(
        sum(claim_counts(r)[0] for r in raw.values()), exposure * n, float(C("waiver.manager_activity"))
    )
    near_prior = pooled_prob(
        sum(near_counts(r)[0] for r in raw.values()), exposure * n, float(C("intel.prior_near_limit"))
    )
    stream_prior = pooled_prob(
        sum(r.streams for r in raw.values()),
        sum(r.adds for r in raw.values()),
        float(C("intel.prior_stream_fraction")),
    )
    prior_adds = pooled_rate("adds", float(C("sim.opponent_stream_adds")))
    prior = {
        "claims": pooled_rate("claims", None),
        "fa": pooled_rate("fa", None),
        "drops": pooled_rate("drops", None),
        "trades": pooled_rate("trades", None),
    }
    part_prior = min(max(part_prior, floor), 1 - floor)

    out = {}
    for key, name in teams.items():
        r = raw[key]
        out[key] = ManagerModel(
            team_key=key,
            name=name,
            league_history=history,
            exposure_weeks=exposure,
            adds_per_week=rate_estimate(r.adds, exposure, prior_adds, k_rate),
            claims_per_week=rate_estimate(r.claims, exposure, prior["claims"], k_rate),
            fa_adds_per_week=rate_estimate(r.fa, exposure, prior["fa"], k_rate),
            drops_per_week=rate_estimate(r.drops, exposure, prior["drops"], k_rate),
            trade_events_per_week=rate_estimate(r.trades, exposure, prior["trades"], k_rate),
            claim_participation=prob_estimate(claim_counts(r)[0], exposure, part_prior, k_prob),
            stream_fraction=prob_estimate(r.streams, r.adds, stream_prior, k_prob),
            near_limit_rate=None if near is None else prob_estimate(near_counts(r)[0], exposure, near_prior, k_prob),
            claims_lost=r.lost,
            pre_season_adds=r.pre_adds,
        )
    return out


def claim_probability(model: ManagerModel, gain: float) -> float:
    """P(this team claims a specific player it would improve with): claim participation x interest in him.

    interest = gain / (gain + C("waiver.claim_gain_half")), so a marginal upgrade is rarely claimed and a big one
    approaches the team's full claim participation. Clamped by C("intel.activity_floor").
    """
    floor = float(C("intel.activity_floor"))
    half = float(C("waiver.claim_gain_half"))
    interest = gain / (gain + half) if gain > 0 else 0.0
    return min(max(model.claim_participation.posterior * interest, floor), 1 - floor)


def expected_adds(model: ManagerModel, used: int | None = None, limit: int | None = None) -> int:
    """Whole adds to expect from this manager over the rest of the week: modeled adds/week minus the adds already
    used this week (if known), rounded half up, capped by the adds they have left under the league limit."""
    mean = model.adds_per_week.posterior - (used or 0)
    adds = int(math.floor(max(mean, 0.0) + 0.5))
    if limit is not None:
        adds = min(adds, max(0, limit - (used or 0)))
    return adds
