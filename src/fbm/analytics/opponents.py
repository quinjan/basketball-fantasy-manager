"""Opponent intel: manager profiles from league transaction history + the category build.

Pure functions over `fbm.yahoo.league_reads.Transaction` records (as stored by `fbm.league.tx_history`).

- adds: player moves with action "add" whose destination is the team; waiver claims (source "waivers") vs
  free-agent adds (source "freeagents").
- streams: an add the same team drops again within C("intel.stream_days"); streaming propensity = streams / adds.
- trades: completed trades the team was part of; trades it proposed (trader) of any status.
- claim activity (feeds waiver_decision): share of observed weeks with at least one waiver claim.
- activity tier: inactive if no move for C("intel.inactive_days"), else by recent adds/week vs the tier constants.
- confidence by sample size (moves): low < C("intel.min_moves") <= medium < 3x <= high. Profiles with low
  confidence never replace engine defaults (`has_history` is False).
- build: category win rates (league profile) -> strong / weak / likely punts.
- trade partners: ranked by activity group (very active/active, then casual, then unknown, then inactive), then by
  complementary fit = sum over categories of (their surplus x my need) + (my surplus x their need), where surplus /
  need are distances above / below a 0.5 category win rate.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

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

    @property
    def has_history(self) -> bool:
        return self.confidence in ("medium", "high")

    def typical_adds(self) -> int:
        """Adds to expect from this manager in a week: recent rate, rounded."""
        return int(round(self.recent_adds_per_week))

    def to_dict(self) -> dict:
        out = asdict(self)
        out["has_history"] = self.has_history
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


def _tier(recent_apw: float, days_since: float | None) -> str:
    if days_since is None or days_since > float(C("intel.inactive_days")):
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


def profile_teams(
    transactions: Iterable[Transaction],
    teams: Mapping[str, str],
    now: datetime,
    season_start: datetime | None = None,
    builds: Mapping[str, Mapping[str, float]] | None = None,
) -> dict[str, ManagerProfile]:
    """Profiles for every team in `teams` (team_key -> name). `season_start` defaults to the first transaction."""
    txs = sorted((t for t in transactions if t.timestamp is not None), key=lambda t: t.timestamp)
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
    out = {}
    for key, name in teams.items():
        a, d = adds[key], drops[key]
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
            **build_summary(rates),
        )
        prof.tier = _tier(prof.recent_adds_per_week, since) if txs else "unknown"
        out[key] = prof
    return out


def claim_activity(profiles: Mapping[str, ManagerProfile]) -> dict[str, float]:
    """Per-team waiver-claim probability for teams with enough history (clamped); others use the default."""
    lo, hi = float(C("intel.activity_floor")), 1 - float(C("intel.activity_floor"))
    return {
        k: min(max(p.claim_activity, lo), hi)
        for k, p in profiles.items()
        if p.has_history and p.claim_activity is not None
    }


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
    if p.cat_win_rate:
        lines += [
            f"- Strong cats: {', '.join(p.strong_cats) or '-'}; weak: {', '.join(p.weak_cats) or '-'}; "
            f"likely punts: {', '.join(p.likely_punts) or '-'}",
        ]
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
