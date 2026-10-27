"""Opponent intel: manager profiles from league transaction history + the category build.

Pure functions over `fbm.yahoo.league_reads.Transaction` records (as stored by `fbm.league.tx_history`).

- adds: player moves with action "add" whose destination is the team; waiver claims (source "waivers") vs
  free-agent adds (source "freeagents").
- streams: an add the same team drops again within C("intel.stream_days"); streaming propensity = streams / adds.
- trades: completed trades the team was part of; trades it proposed (trader) of any status.
- claim activity: observed share of weeks with at least one waiver claim. waiver_decision uses the empirical-Bayes
  posterior instead (`fbm.analytics.manager_model`: shrunk toward the league-wide prior, recency-weighted).
- activity tier: inactive if no move for C("intel.inactive_days"), else by recent adds/week vs the tier constants.
- confidence by sample size (moves): low < C("intel.min_moves") <= medium < 3x <= high. Profiles with low
  confidence never replace engine defaults (`has_history` is False).
- build: category win rates (league profile) -> strong / weak / likely punts.
- wire lean: net category G per add over the last C("intel.wire_days") (added players' G minus dropped players' G,
  current values, divided by scored adds). >= C("intel.wire_lean_g") -> a category they chase (`wire_targets`),
  <= -that -> one they shed (`wire_sheds`); reported once C("intel.wire_min_adds") adds are scored.
  `trade_punts` = likely punts + weak categories they shed: the build a trade partner is judged by.
- trade partners: ranked by activity group (very active/active, then casual, then unknown, then inactive), then by
  complementary fit = sum over categories of (their surplus x my need) + (my surplus x their need), where surplus /
  need are distances above / below a 0.5 category win rate.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

from fbm.analytics.manager_model import ManagerModel, claim_probability, estimate_managers
from fbm.constants import C
from fbm.yahoo.league_reads import Transaction

TIERS = ("inactive", "casual", "active", "very active")


@dataclass
class ManagerProfile:
    team_key: str
    name: str
    weeks_observed: float
    adds: int = 0
    waiver_claims: int = 0
    fa_adds: int = 0
    drops: int = 0
    streams: int = 0
    adds_per_week: float = 0.0
    recent_adds_per_week: float = 0.0
    streaming_propensity: float | None = None
    claim_activity: float | None = None  # share of weeks with >= 1 waiver claim
    trades_completed: int = 0
    trades_proposed: int = 0
    last_activity: str | None = None  # ISO UTC
    days_since_activity: float | None = None
    tier: str = "unknown"
    confidence: str = "none"
    moves: int = 0
    strong_cats: list[str] = field(default_factory=list)
    weak_cats: list[str] = field(default_factory=list)
    likely_punts: list[str] = field(default_factory=list)
    cat_win_rate: dict[str, float] = field(default_factory=dict)
    wire_adds_scored: int = 0
    wire_cat_lean: dict[str, float] = field(default_factory=dict)  # net G per add, by category
    wire_targets: list[str] = field(default_factory=list)
    wire_sheds: list[str] = field(default_factory=list)
    model: ManagerModel | None = None  # empirical-Bayes behavior model (prior vs observed vs posterior)

    @property
    def has_history(self) -> bool:
        return self.confidence in ("medium", "high")

    @property
    def trade_punts(self) -> list[str]:
        """Categories a trade partner is assumed not to value: likely punts, plus weak categories they shed."""
        return [*self.likely_punts, *(k for k in self.wire_sheds if k in self.weak_cats and k not in self.likely_punts)]

    def typical_adds(self) -> int:
        """Adds to expect from this manager in a week: recent rate, rounded."""
        return int(round(self.recent_adds_per_week))

    def to_dict(self) -> dict:
        out = asdict(self)
        out["model"] = self.model.to_dict() if self.model else None
        out["has_history"] = self.has_history
        out["trade_punts"] = self.trade_punts
        return out


def _ok(tx: Transaction) -> bool:
    return tx.type in ("add", "drop", "add/drop") and tx.status in ("successful", "")


def _confidence(moves: int) -> str:
    need = int(C("intel.min_moves"))
    if moves == 0:
        return "none"
    if moves < need:
        return "low"
    return "medium" if moves < 3 * need else "high"


def _tier(recent_apw: float, days_since: float | None, span_days: float = 0.0) -> str:
    if days_since is None:
        # No move yet: "inactive" only once we have watched long enough; before that, "casual" (never above it).
        return "inactive" if span_days >= float(C("intel.inactive_days")) else "casual"
    if days_since > float(C("intel.inactive_days")):
        return "inactive"
    if recent_apw >= float(C("intel.tier_very_active")):
        return "very active"
    if recent_apw >= float(C("intel.tier_active")):
        return "active"
    return "casual"


def build_summary(rates: Mapping[str, float]) -> dict[str, list[str]]:
    strong, weak, punt = float(C("intel.strong_cat")), float(C("intel.weak_cat")), float(C("intel.punt_cat"))
    return {
        "strong_cats": [k for k, v in rates.items() if v >= strong],
        "weak_cats": [k for k, v in rates.items() if punt < v <= weak],
        "likely_punts": [k for k, v in rates.items() if v <= punt],
    }


def wire_lean(
    adds: Sequence[str], drops: Sequence[str], player_g: Mapping[str, Mapping[str, float]]
) -> tuple[int, dict[str, float], list[str], list[str]]:
    """(scored adds, net G per add by category, categories chased, categories shed) from one team's wire moves."""
    scored = [player_g[p] for p in adds if p in player_g]
    shed = [player_g[p] for p in drops if p in player_g]
    if not scored:
        return 0, {}, [], []
    cats = list(scored[0])
    lean = {k: round((sum(g.get(k, 0.0) for g in scored) - sum(g.get(k, 0.0) for g in shed)) / len(scored), 2)
            for k in cats}  # fmt: skip
    if len(scored) < int(C("intel.wire_min_adds")):
        return len(scored), lean, [], []
    thr = float(C("intel.wire_lean_g"))
    return len(scored), lean, [k for k, v in lean.items() if v >= thr], [k for k, v in lean.items() if v <= -thr]


def profile_teams(
    transactions: Iterable[Transaction],
    teams: Mapping[str, str],
    now: datetime,
    season_start: datetime | None = None,
    builds: Mapping[str, Mapping[str, float]] | None = None,
    league_start: datetime | None = None,
    max_weekly_adds: int | None = None,
    player_g: Mapping[str, Mapping[str, float]] | None = None,
) -> dict[str, ManagerProfile]:
    """Profiles for every team in `teams` (team_key -> name). `season_start` defaults to the first transaction.

    `league_start` / `max_weekly_adds` feed the empirical-Bayes behavior model (`ManagerProfile.model`).
    `player_g`: transaction player id -> G by category (same category names as `builds`) for the wire lean."""
    txs = sorted((t for t in transactions if t.timestamp is not None), key=lambda t: t.timestamp)
    models = estimate_managers(txs, teams, now, league_start, max_weekly_adds)
    start = season_start or (txs[0].timestamp if txs else now)
    days = max((now - start).total_seconds() / 86400, 0.0)
    weeks = max(days / 7, 1.0)
    recent_days = float(C("intel.recent_days"))
    recent_cut = now - timedelta(days=recent_days)
    stream_window = timedelta(days=float(C("intel.stream_days")))
    n_weeks = max(math.ceil(days / 7), 1)

    adds: dict[str, list[tuple[datetime, str, str]]] = {k: [] for k in teams}  # (ts, player, source)
    drops: dict[str, list[tuple[datetime, str]]] = {k: [] for k in teams}
    completed = dict.fromkeys(teams, 0)
    proposed = dict.fromkeys(teams, 0)
    last: dict[str, datetime] = {}

    def touch(key: str, ts: datetime) -> None:
        if key in teams and (key not in last or ts > last[key]):
            last[key] = ts

    for tx in txs:
        ts = tx.timestamp
        if tx.type == "trade":
            if tx.trader_team_key in teams:
                proposed[tx.trader_team_key] += 1
            if tx.status == "successful":
                for key in {tx.trader_team_key, tx.tradee_team_key}:
                    if key in teams:
                        completed[key] += 1
                        touch(key, ts)
            continue
        if not _ok(tx):
            continue
        for p in tx.players:
            if p.action == "add" and p.destination_team_key in teams:
                adds[p.destination_team_key].append((ts, p.player_id, p.source_type))
                touch(p.destination_team_key, ts)
            elif p.action == "drop" and p.source_team_key in teams:
                drops[p.source_team_key].append((ts, p.player_id))
                touch(p.source_team_key, ts)

    builds = builds or {}
    wire_cut = now - timedelta(days=float(C("intel.wire_days")))
    out = {}
    for key, name in teams.items():
        a, d = adds[key], drops[key]
        lean = wire_lean(
            [pid for ts, pid, _ in a if ts >= wire_cut], [pid for ts, pid in d if ts >= wire_cut], player_g or {}
        )
        streams = sum(1 for ts, pid, _ in a if any(pid == dp and ts < dts <= ts + stream_window for dts, dp in d))
        claims = [x for x in a if x[2] == "waivers"]
        claim_weeks = {int((ts - start).total_seconds() // (7 * 86400)) for ts, _, _ in claims}
        recent = sum(1 for ts, _, _ in a if ts >= recent_cut)
        recent_weeks = max(min(recent_days, days) / 7, 1.0)
        moves = len(a) + len(d) + completed[key] + proposed[key]
        since = (now - last[key]).total_seconds() / 86400 if key in last else None
        rates = {k: round(float(v), 3) for k, v in (builds.get(key) or {}).items()}
        prof = ManagerProfile(
            team_key=key,
            name=name,
            weeks_observed=round(weeks, 1),
            adds=len(a),
            waiver_claims=len(claims),
            fa_adds=sum(1 for x in a if x[2] == "freeagents"),
            drops=len(d),
            streams=streams,
            adds_per_week=round(len(a) / weeks, 2),
            recent_adds_per_week=round(recent / recent_weeks, 2),
            streaming_propensity=round(streams / len(a), 2) if a else None,
            claim_activity=round(len(claim_weeks) / n_weeks, 2) if txs else None,
            trades_completed=completed[key],
            trades_proposed=proposed[key],
            last_activity=last[key].isoformat() if key in last else None,
            days_since_activity=round(since, 1) if since is not None else None,
            confidence=_confidence(moves),
            moves=moves,
            cat_win_rate=rates,
            wire_adds_scored=lean[0],
            wire_cat_lean=lean[1],
            wire_targets=lean[2],
            wire_sheds=lean[3],
            model=models[key],
            **build_summary(rates),
        )
        prof.tier = _tier(prof.recent_adds_per_week, since, days) if txs else "unknown"
        out[key] = prof
    return out


def claim_activity(
    profiles: Mapping[str, ManagerProfile], gains: Mapping[str, float] | None = None
) -> dict[str, float]:
    """Per-team probability of claiming a specific player (waiver_decision).

    Empty when the league has no transaction history (callers fall back to C("waiver.manager_activity")).
    Otherwise every team gets its empirical-Bayes claim participation (posterior), weighted by its interest in the
    player (`gains`: team_key -> need-weighted gain; without it, the bare participation)."""
    out = {}
    floor = float(C("intel.activity_floor"))
    for k, p in profiles.items():
        m = p.model
        if m is None or not m.league_history:
            continue
        if gains is None:
            out[k] = min(max(m.claim_participation.posterior, floor), 1 - floor)
        elif k in gains:
            out[k] = claim_probability(m, gains[k])
    return out


def complementary_fit(mine: Mapping[str, float], theirs: Mapping[str, float]) -> float:
    fit = 0.0
    for k, m in mine.items():
        t = theirs.get(k)
        if t is None:
            continue
        fit += max(t - 0.5, 0) * max(0.5 - m, 0) + max(m - 0.5, 0) * max(0.5 - t, 0)
    return round(fit, 3)


_GROUP = {"very active": 0, "active": 0, "casual": 1, "unknown": 2, "inactive": 3}


def partner_rank_key(p: ManagerProfile, fit: float) -> tuple:
    return (_GROUP.get(p.tier, 2), -fit, -(p.trades_completed + p.trades_proposed))


def rank_partners(
    profiles: Mapping[str, ManagerProfile], my_key: str, mine: Mapping[str, float]
) -> list[tuple[ManagerProfile, float]]:
    rows = [(p, complementary_fit(mine, p.cat_win_rate)) for k, p in profiles.items() if k != my_key]
    return sorted(rows, key=lambda r: partner_rank_key(*r))


def ranked_by_activity(profiles: Sequence[ManagerProfile]) -> list[ManagerProfile]:
    return sorted(profiles, key=lambda p: (-p.recent_adds_per_week, -p.adds_per_week, -p.moves))


def profile_markdown(p: ManagerProfile, as_of: str) -> str:
    def pct(x: float | None) -> str:
        return "n/a" if x is None else f"{x:.0%}"

    def num(x: float | None) -> str:
        return "n/a" if x is None else f"{x:.2f}"

    lines = [
        f"_Generated by the engine from league transactions as of {as_of}; overwritten on refresh._",
        "",
        f"- Activity: {p.tier} (confidence {p.confidence}, {p.moves} moves over {p.weeks_observed} weeks)",
        f"- Adds/week: {p.adds_per_week} season, {p.recent_adds_per_week} recent "
        f"({p.waiver_claims} waiver claims, {p.fa_adds} free-agent adds, {p.drops} drops)",
        f"- Streaming propensity: {pct(p.streaming_propensity)} ({p.streams} quick drops)",
        f"- Waiver claim activity: {pct(p.claim_activity)} of weeks",
        f"- Trades: {p.trades_completed} completed, {p.trades_proposed} proposed",
        f"- Last activity (UTC): {p.last_activity or 'none'}",
    ]
    m = p.model
    if m is not None:
        src = "league prior" if m.league_history else "engine constants (no league history)"
        lines += [
            f"- Modeled behavior (empirical Bayes: observed shrunk toward {src}; evidence "
            f"{m.adds_per_week.evidence_share:.0%} of the estimate, {m.exposure_weeks:.1f} weighted weeks):",
            f"  - Adds/week: prior {m.adds_per_week.prior:.2f}, observed {num(m.adds_per_week.observed)}, "
            f"posterior {m.adds_per_week.posterior:.2f} (sd {m.adds_per_week.sd:.2f})",
            f"  - Claim participation (weeks with a claim): prior {m.claim_participation.prior:.0%}, observed "
            f"{pct(m.claim_participation.observed)}, posterior {m.claim_participation.posterior:.0%}",
            f"  - Stream fraction (quick drops): prior {m.stream_fraction.prior:.0%}, observed "
            f"{pct(m.stream_fraction.observed)}, posterior {m.stream_fraction.posterior:.0%}",
        ]
        if m.near_limit_rate is not None:
            lines.append(
                f"  - Weeks near the weekly add limit: observed {pct(m.near_limit_rate.observed)}, "
                f"posterior {m.near_limit_rate.posterior:.0%}"
            )
    if p.cat_win_rate:
        lines += [
            f"- Strong cats: {', '.join(p.strong_cats) or '-'}; weak: {', '.join(p.weak_cats) or '-'}; "
            f"likely punts: {', '.join(p.likely_punts) or '-'}",
        ]
    if p.wire_adds_scored:
        lines.append(
            f"- Wire lean (last {int(C('intel.wire_days'))} days, {p.wire_adds_scored} adds scored): chasing "
            f"{', '.join(p.wire_targets) or '-'}; shedding {', '.join(p.wire_sheds) or '-'}"
        )
    return "\n".join(lines)


SECTION = "## Engine profile (auto)"


def upsert_section(text: str, body: str, heading: str = SECTION) -> str:
    """Replace (or append) `heading` and its body up to the next `## ` heading; keep everything else."""
    lines = text.splitlines()
    block = [heading, "", *body.splitlines(), ""]
    if heading in lines:
        i = lines.index(heading)
        j = next((n for n in range(i + 1, len(lines)) if lines[n].startswith("## ")), len(lines))
        lines = lines[:i] + block + lines[j:]
    else:
        if lines and lines[-1].strip():
            lines.append("")
        lines += block
    return "\n".join(lines).rstrip() + "\n"
