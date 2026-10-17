"""Early signals: players whose fantasy value is about to move before the market (Yahoo season stats, rankings,
ownership) shows it.

`direction` is where we expect his value to go relative to what the market sees now:

- buy_low (up) / sell_high (down): season-to-date value far below / above FBM's rest-of-season projection. Reason tags
  compare the season line with what we expected before the season (ESPN preseason projection, else last season):
  minutes and usage (stabilise fast, so a change is probably real) vs FG%/FT%/3P% (stabilise slowly, so a swing is
  probably luck) and a key teammate being out (temporary). Confidence is high when the gap is luck-driven and low
  when the role itself changed.
- role_change (up/down): the fastest box-score evidence — a new starter, a lost starting job, or a minutes spike/drop
  over the last 3 games — days before a minutes trend or a season average moves.
- opportunity (up): a key player is ruled out; teammates who historically gained minutes without him (with/without
  split from box scores; positional estimate when there is no history).
- espn_revision (up/down): ESPN's projection for the player moved by a meaningful value over the last week.

Each signal carries what grading needs: `evidence.metric` ("value" = per-game G-score total, or "minutes"),
`evidence.reference` (what the market sees / the old level) and, for buy/sell, `evidence.predicted` (our projection).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import pandas as pd

from fbm.analytics import players as player_tables
from fbm.analytics.valuation import PoolStats, ValuationConfig, score_table
from fbm.constants import C

OUT_STATUSES = {"OUT", "O", "OFS", "INJ", "SUSP", "SUSPENSION"}
CONF_RANK = {"high": 0, "medium": 1, "low": 2}
POSITION_GROUP = {"PG": "G", "SG": "G", "G": "G", "SF": "F", "PF": "F", "F": "F", "GF": "F", "C": "C", "FC": "C"}


@dataclass
class Signal:
    kind: str
    direction: str  # "up" | "down"
    espn_id: str
    name: str
    team: str
    strength: float
    confidence: str  # high | medium | low
    reasons: list[str]
    note: str
    evidence: dict[str, Any] = field(default_factory=dict)
    key_extra: str = ""

    @property
    def key(self) -> str:
        return ":".join(x for x in (self.kind, self.espn_id, self.direction, self.key_extra) if x)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "direction": self.direction,
            "id": self.espn_id,
            "player": self.name,
            "nba": self.team,
            "strength": round(self.strength, 2),
            "confidence": self.confidence,
            "reasons": self.reasons,
            "note": self.note,
            "evidence": self.evidence,
        }


def total_value(table: pd.DataFrame, pool: PoolStats, cfg: ValuationConfig) -> pd.Series:
    """Per-game G-score total of each row against the projection pool (all league categories)."""
    if table.empty:
        return pd.Series(dtype=float)
    return score_table(table, pool, cfg)["g_total"].astype(float)


def _r(x: Any, nd: int = 2) -> float | None:
    return None if x is None or pd.isna(x) else round(float(x), nd)


def _pct(made: float, att: float) -> float | None:
    return made / att if att and att > 0 else None


# ------------------------------------------------------------------ buy-low / sell-high


def explain(actual: pd.Series, expected: pd.Series | None, teammates_out: Sequence[str], status: str) -> tuple:
    """Reason tags (and numbers) for a season line vs what we expected before the season."""
    reasons: list[str] = []
    ev: dict[str, Any] = {}
    if int(actual.get("gp", 0)) < int(C("signals.small_sample_games")):
        reasons.append("small_sample")
    if status.upper() in OUT_STATUSES:
        reasons.append("injured_now")
    if teammates_out:
        reasons.append("teammate_out")
        ev["teammates_out"] = list(teammates_out)
    if expected is None:
        reasons.append("no_preseason_expectation")
        return reasons, ev
    a_min, x_min = float(actual["minutes"]), float(expected["minutes"])
    ev["minutes"] = {"season": _r(a_min, 1), "expected": _r(x_min, 1)}
    change = float(C("projection.trend_min_change"))
    if a_min - x_min >= change:
        reasons.append("minutes_up")
    elif x_min - a_min >= change:
        reasons.append("minutes_down")

    def usage(r: pd.Series) -> float | None:
        m = float(r["minutes"])
        return (float(r["fga"]) + 0.44 * float(r["fta"])) / m if m > 0 else None

    ua, ux = usage(actual), usage(expected)
    if ua is not None and ux:
        rel = ua / ux - 1
        ev["usage_change"] = _r(rel, 3)
        if rel >= float(C("signals.usage_change")):
            reasons.append("usage_up")
        elif rel <= -float(C("signals.usage_change")):
            reasons.append("usage_down")
    for tag, made, att, min_att, key in (
        ("fg", "fgm", "fga", 3.0, "signals.fg_pct_change"),
        ("ft", "ftm", "fta", 1.5, "signals.ft_pct_change"),
        ("three", "tpm", "tpa", 2.0, "signals.tp_pct_change"),
    ):
        pa = _pct(float(actual[made]), float(actual[att]))
        px = _pct(float(expected[made]), float(expected[att]))
        if pa is None or px is None or float(actual[att]) < min_att:
            continue
        ev[f"{tag}_pct"] = {"season": _r(pa, 3), "expected": _r(px, 3)}
        if pa - px >= float(C(key)):
            reasons.append(f"{tag}_hot")
        elif px - pa >= float(C(key)):
            reasons.append(f"{tag}_cold")
    return reasons, ev


def _buy_sell_confidence(kind: str, reasons: list[str]) -> str:
    if "small_sample" in reasons:
        return "low"
    if kind == "buy_low":
        role_against = "minutes_down" in reasons or "usage_down" in reasons
        luck = any(r.endswith("_cold") for r in reasons)
    else:
        role_against = ("minutes_up" in reasons or "usage_up" in reasons) and "teammate_out" not in reasons
        luck = any(r.endswith("_hot") for r in reasons) or "teammate_out" in reasons
    if role_against:
        return "low"
    return "high" if luck else "medium"


def _buy_sell_note(kind: str, reasons: list[str]) -> str:
    parts = []
    if kind == "buy_low":
        parts.append("producing below FBM's rest-of-season projection")
        if any(r.endswith("_cold") for r in reasons):
            parts.append("cold shooting should normalise")
        if "minutes_down" in reasons or "usage_down" in reasons:
            parts.append("but his role shrank — only a buy if the minutes come back")
        if "injured_now" in reasons:
            parts.append("injury discount: check the return timeline")
    else:
        parts.append("producing above FBM's rest-of-season projection")
        if any(r.endswith("_hot") for r in reasons):
            parts.append("hot shooting is likely to cool")
        if "teammate_out" in reasons:
            parts.append("his role may shrink when injured teammates return")
        elif "minutes_up" in reasons or "usage_up" in reasons:
            parts.append("bigger role may stick — verify the cause in the news before selling")
    if "small_sample" in reasons:
        parts.append("small sample")
    return "; ".join(parts)


def buy_sell(
    actual: pd.DataFrame,
    ros: pd.DataFrame,
    expected: pd.DataFrame,
    pool: PoolStats,
    cfg: ValuationConfig,
    teammates_out: Mapping[str, Sequence[tuple[str, str]]] | None = None,
    statuses: Mapping[str, str] | None = None,
) -> list[Signal]:
    """Season-to-date value vs rest-of-season projection value (G points per game)."""
    teammates_out = teammates_out or {}
    statuses = statuses or {}
    act = actual[(actual["gp"] >= int(C("signals.min_games"))) & (actual["minutes"] > 0)]
    ids = act.index.intersection(ros.index)
    if not len(ids):
        return []
    v_act = total_value(act.loc[ids], pool, cfg)
    v_ros = total_value(ros.loc[ids], pool, cfg)
    limit = int(cfg.pool_size * float(C("signals.relevance_rank_mult")))
    relevant = (v_act.rank(ascending=False) <= limit) | (v_ros.rank(ascending=False) <= limit)
    gap = v_act - v_ros
    min_gap = float(C("signals.buy_sell_min_gap"))
    out = []
    for pid in gap.index[relevant & (gap.abs() >= min_gap)]:
        kind = "sell_high" if gap[pid] > 0 else "buy_low"
        row = act.loc[pid]
        team = str(row.get("team") or ros.loc[pid].get("team") or "")
        mates = [name for (oid, name) in teammates_out.get(team, ()) if oid != pid]
        exp = expected.loc[pid] if pid in expected.index else None
        reasons, ev = explain(row, exp, mates, statuses.get(pid, ""))
        out.append(
            Signal(
                kind=kind,
                direction="down" if kind == "sell_high" else "up",
                espn_id=str(pid),
                name=str(row.get("name") or ros.loc[pid].get("name")),
                team=team,
                strength=abs(float(gap[pid])),
                confidence=_buy_sell_confidence(kind, reasons),
                reasons=reasons,
                note=_buy_sell_note(kind, reasons),
                evidence={
                    "metric": "value",
                    "reference": _r(v_act[pid]),
                    "predicted": _r(v_ros[pid]),
                    "season_value": _r(v_act[pid]),
                    "ros_value": _r(v_ros[pid]),
                    "gap": _r(gap[pid]),
                    "gp": int(row["gp"]),
                    "expected_source": None if exp is None else str(exp.get("prior_source", "")),
                    **ev,
                },
            )
        )
    return out


# ------------------------------------------------------------------ role changes (box-score starts / minutes)


def role_changes(
    lines: pd.DataFrame,
    today: date,
    teammates_out: Mapping[str, Sequence[tuple[str, str]]] | None = None,
) -> list[Signal]:
    """lines: this season's played box lines (espn_id, name, team, et_date, starter, minutes)."""
    if lines.empty:
        return []
    teammates_out = teammates_out or {}
    lookback = int(C("signals.role_lookback_games"))
    rate = float(C("signals.role_starter_rate"))
    min_minutes = float(C("signals.role_min_minutes"))
    spike = float(C("signals.minutes_spike"))
    stale = (today - timedelta(days=4)).isoformat()
    out = []
    for pid, g in lines.sort_values("et_date", ascending=False).groupby("espn_id", sort=False):
        if g["et_date"].iloc[0] < stale:
            continue
        starts = g["starter"].astype(int).tolist()
        mins = g["minutes"].astype(float).tolist()
        name, team = str(g["name"].iloc[0]), str(g["team"].iloc[0])
        mates = [n for (oid, n) in teammates_out.get(team, ()) if oid != pid]
        streak_kind = starts[0]
        k = next((i for i, s in enumerate(starts) if s != streak_kind), len(starts))
        prev_s, prev_m = starts[k : k + lookback], mins[k : k + lookback]
        reasons = ["teammate_out"] if mates else []
        ev_base = {"metric": "minutes", "teammates_out": mates} if mates else {"metric": "minutes"}
        signal = None
        if len(prev_s) >= 3:
            prev_rate = sum(prev_s) / len(prev_s)
            prev_min = sum(prev_m) / len(prev_m)
            streak_min = sum(mins[:k]) / k
            conf = "high" if k >= 3 else "medium" if k == 2 else "low"
            if streak_kind == 1 and prev_rate <= rate and streak_min >= min_minutes:
                signal = Signal(
                    "role_change",
                    "up",
                    str(pid),
                    name,
                    team,
                    streak_min - prev_min,
                    conf,
                    ["new_starter", *reasons],
                    f"started the last {k} game(s) after starting {prev_rate:.0%} of the previous {len(prev_s)}; "
                    f"{streak_min:.1f} min vs {prev_min:.1f}",
                    {**ev_base, "reference": round(prev_min, 1), "streak_games": k, "prev_starter_rate": prev_rate},
                )
            elif streak_kind == 0 and prev_rate >= 1 - rate and streak_min <= prev_min - spike / 2:
                signal = Signal(
                    "role_change",
                    "down",
                    str(pid),
                    name,
                    team,
                    prev_min - streak_min,
                    conf,
                    ["lost_starting_job", *reasons],
                    f"came off the bench the last {k} game(s) after starting {prev_rate:.0%} of the previous "
                    f"{len(prev_s)}; {streak_min:.1f} min vs {prev_min:.1f}",
                    {**ev_base, "reference": round(prev_min, 1), "streak_games": k, "prev_starter_rate": prev_rate},
                )
        if signal is None and len(mins) >= 3 + 3:
            last3 = sum(mins[:3]) / 3
            base = mins[3 : 3 + lookback]
            base_min = sum(base) / len(base)
            if last3 - base_min >= spike and last3 >= min_minutes:
                signal = Signal(
                    "role_change",
                    "up",
                    str(pid),
                    name,
                    team,
                    last3 - base_min,
                    "medium",
                    ["minutes_spike", *reasons],
                    f"{last3:.1f} min over the last 3 games vs {base_min:.1f} before",
                    {**ev_base, "reference": round(base_min, 1)},
                )
            elif base_min - last3 >= spike and base_min >= 20:
                signal = Signal(
                    "role_change",
                    "down",
                    str(pid),
                    name,
                    team,
                    base_min - last3,
                    "medium",
                    ["minutes_drop", *reasons],
                    f"{last3:.1f} min over the last 3 games vs {base_min:.1f} before",
                    {**ev_base, "reference": round(base_min, 1)},
                )
        if signal is not None:
            out.append(signal)
    return out


# ------------------------------------------------------------------ next man up (with/without splits)


def _split(team_lines: pd.DataFrame, xid: str, yid: str) -> tuple[pd.DataFrame, pd.DataFrame] | None:
    """Teammate Y's games with and without X (from X's first game for the team on)."""
    x_lines = team_lines[team_lines["espn_id"] == xid]
    if x_lines.empty:
        return None
    window = team_lines[team_lines["et_date"] >= x_lines["et_date"].min()]
    y = window[window["espn_id"] == yid]
    with_x = y["event_id"].isin(set(x_lines["event_id"]))
    return y[with_x], y[~with_x]


def opportunities(
    injured: Sequence[Mapping[str, Any]],
    lines: pd.DataFrame,
    ros: pd.DataFrame,
    season: int | None = None,
    statuses: Mapping[str, str] | None = None,
) -> list[Signal]:
    """Next man up. injured: key players ruled out ({espn_id, name, team, position, minutes, return_date}).
    lines: played box lines with event_id, espn_id, team, et_date, season, minutes, fga.

    Each teammate's minutes with vs without the injured player come from this season's games when there are enough
    without-games (high/medium confidence), else from all stored seasons (last season's split: rosters and roles
    changed, so at most medium). A beneficiary must gain `signals.opportunity_min_delta` minutes and reach
    `signals.opportunity_min_minutes` without him. With no usable split, the top projected-minutes teammate at the
    same position group is a low-confidence positional estimate. One signal per beneficiary (all injured teammates
    listed in the evidence)."""
    min_delta = float(C("signals.opportunity_min_delta"))
    min_without = int(C("signals.opportunity_min_without"))
    min_minutes = float(C("signals.opportunity_min_minutes"))
    best: dict[str, Signal] = {}
    injured_by_player: dict[str, list[str]] = {}
    injured_ids_by_player: dict[str, list[str]] = {}
    statuses = statuses or {}
    unavailable = {str(i["espn_id"]) for i in injured} | {
        pid for pid, st in statuses.items() if str(st).upper() in OUT_STATUSES
    }
    for inj in injured:
        xid, team = str(inj["espn_id"]), str(inj["team"])
        roster = ros[(ros["team"] == team) & (ros["minutes"] > 0) & ~ros.index.isin(list(unavailable))]
        if roster.empty:
            continue
        team_lines = lines[lines["team"] == team]
        current = team_lines[team_lines["season"] == season] if season is not None else team_lines.iloc[0:0]
        hurt = {"id": xid, "name": inj["name"], "return_date": inj.get("return_date")}
        found: list[Signal] = []
        for yid in roster.index:
            split, label = _split(current, xid, yid), "this_season"
            if split is None or len(split[1]) < min_without or len(split[0]) < 3:
                split, label = _split(team_lines, xid, yid), "all_seasons"
            if split is None or len(split[1]) < min_without or len(split[0]) < 3:
                continue
            w_, wo = split
            m_with, m_without = float(w_["minutes"].mean()), float(wo["minutes"].mean())
            if m_without - m_with < min_delta or m_without < min_minutes:
                continue
            if label == "this_season":
                conf = "high" if len(wo) >= 8 else "medium"
            else:
                conf = "medium" if len(wo) >= 8 else "low"
            name = str(roster.loc[yid, "name"])
            found.append(
                Signal(
                    "opportunity",
                    "up",
                    str(yid),
                    name,
                    team,
                    m_without - m_with,
                    conf,
                    ["teammate_out", "with_without_split" if label == "this_season" else "last_season_split"],
                    f"{inj['name']} is out; {name} has averaged {m_without:.1f} min without him ({len(wo)} games"
                    f"{'' if label == 'this_season' else ', incl. last season'}) vs {m_with:.1f} with him",
                    {
                        "metric": "minutes",
                        "reference": round(m_with, 1),
                        "minutes_without": round(m_without, 1),
                        "games_without": len(wo),
                        "split": label,
                        "fga_delta": round(float(wo["fga"].mean() - w_["fga"].mean()), 1),
                        "injured": hurt,
                    },
                )
            )
        if not found:
            group = POSITION_GROUP.get(str(inj.get("position") or "").upper())
            same = roster[roster["position"].map(POSITION_GROUP.get) == group] if group else roster
            same = same[(same["minutes"] >= 12) & (same["minutes"] < float(C("signals.key_player_minutes")))]
            for yid, r in same.sort_values("minutes", ascending=False).head(1).iterrows():
                found.append(
                    Signal(
                        "opportunity",
                        "up",
                        str(yid),
                        str(r["name"]),
                        team,
                        float(inj.get("minutes") or 0) / 4,
                        "low",
                        ["teammate_out", "positional_estimate"],
                        f"{inj['name']} is out; no with/without history, {r['name']} is the next "
                        f"{group or 'rotation'} player by projected minutes ({float(r['minutes']):.1f})",
                        {"metric": "minutes", "reference": round(float(r["minutes"]), 1), "injured": hurt},
                    )
                )
        for s in found:
            injured_by_player.setdefault(s.espn_id, []).append(str(inj["name"]))
            injured_ids_by_player.setdefault(s.espn_id, []).append(xid)
            cur = best.get(s.espn_id)
            rank = (-CONF_RANK[s.confidence], s.strength)
            if cur is None or rank > (-CONF_RANK[cur.confidence], cur.strength):
                best[s.espn_id] = s
    out = []
    for pid, s in best.items():
        names = injured_by_player[pid]
        s.evidence["injured_teammates"] = names
        s.key_extra = "+".join(sorted(injured_ids_by_player[pid]))
        if len(names) > 1:
            s.note += f" (also out: {', '.join(n for n in names if n != s.evidence['injured']['name'])})"
        out.append(s)
    return out


# ------------------------------------------------------------------ ESPN projection revisions


def espn_revisions(
    now: pd.DataFrame,
    then: pd.DataFrame,
    pool: PoolStats,
    cfg: ValuationConfig,
    market_value: pd.Series | None = None,
) -> list[Signal]:
    """ESPN's latest projection vs its projection `signals.espn_revision_days` ago, in per-game value."""
    if now.empty or then.empty:
        return []
    ids = now.index[now["minutes"] > 0].intersection(then.index[then["minutes"] > 0])
    changed = [pid for pid in ids if now.loc[pid, "snapshot_date"] != then.loc[pid, "snapshot_date"]]
    if not changed:
        return []
    v_now = total_value(now.loc[changed], pool, cfg)
    v_then = total_value(then.loc[changed], pool, cfg)
    limit = int(cfg.pool_size * float(C("signals.relevance_rank_mult")))
    diff = v_now - v_then
    relevant = (v_now.rank(ascending=False) <= limit) | (v_then.rank(ascending=False) <= limit)
    min_change = float(C("signals.espn_revision_min"))
    market = market_value if market_value is not None else pd.Series(dtype=float)
    out = []
    for pid in diff.index[relevant & (diff.abs() >= min_change)]:
        up = diff[pid] > 0
        ref = market.get(pid, v_then[pid])
        out.append(
            Signal(
                "espn_revision",
                "up" if up else "down",
                str(pid),
                str(now.loc[pid, "name"]),
                str(now.loc[pid, "team"]),
                abs(float(diff[pid])),
                "medium",
                ["espn_projection_raised" if up else "espn_projection_cut"],
                f"ESPN's projection value moved {diff[pid]:+.2f} since {then.loc[pid, 'snapshot_date']} "
                f"(minutes {float(then.loc[pid, 'minutes']):.1f} -> {float(now.loc[pid, 'minutes']):.1f})",
                {
                    "metric": "value",
                    "reference": _r(ref),
                    "espn_value_now": _r(v_now[pid]),
                    "espn_value_then": _r(v_then[pid]),
                    "since": str(then.loc[pid, "snapshot_date"]),
                },
            )
        )
    return out


# ------------------------------------------------------------------ orchestration over the store


def key_players_out(
    conn: sqlite3.Connection, ros: pd.DataFrame
) -> tuple[list[dict[str, Any]], dict[str, list[tuple[str, str]]]]:
    """Injured-out players with starter-level projected minutes, and the same grouped by NBA team."""
    key_min = float(C("signals.key_player_minutes"))
    injured = []
    for r in conn.execute("SELECT espn_id, name, team, status, fantasy_status, return_date FROM injuries_current"):
        status = {(r["status"] or "").upper(), (r["fantasy_status"] or "").upper()}
        pid = r["espn_id"]
        if not status & OUT_STATUSES or pid not in ros.index:
            continue
        minutes = float(ros.loc[pid, "minutes"])
        if minutes < key_min:
            continue
        injured.append(
            {
                "espn_id": pid,
                "name": r["name"],
                "team": str(ros.loc[pid, "team"] or r["team"]),
                "position": str(ros.loc[pid, "position"] or ""),
                "minutes": minutes,
                "return_date": r["return_date"],
            }
        )
    by_team: dict[str, list[tuple[str, str]]] = {}
    for inj in injured:
        by_team.setdefault(inj["team"], []).append((inj["espn_id"], inj["name"]))
    return injured, by_team


def detect_all(
    conn: sqlite3.Connection,
    season: int,
    prior: int,
    projections: pd.DataFrame,
    pool: PoolStats,
    cfg: ValuationConfig,
    statuses: Mapping[str, str],
    today: date,
    kinds: Sequence[str] | None = None,
) -> list[Signal]:
    wanted = set(kinds or ("buy_low", "sell_high", "role_change", "opportunity", "espn_revision"))
    injured, out_by_team = key_players_out(conn, projections)
    actual = player_tables.season_table(conn, season)
    signals: list[Signal] = []
    if wanted & {"buy_low", "sell_high"} and not actual.empty:
        expected = player_tables.prior_table(conn, season, prior)
        found = buy_sell(actual, projections, expected, pool, cfg, out_by_team, statuses)
        signals += [s for s in found if s.kind in wanted]
    if wanted & {"role_change", "opportunity"}:
        lines = pd.read_sql_query(
            "SELECT b.event_id, b.espn_id, COALESCE(p.name, b.espn_id) AS name, b.team, b.et_date, b.season, "
            "b.starter, b.minutes, b.fga FROM box_lines b LEFT JOIN players p ON p.espn_id = b.espn_id "
            "WHERE b.season IN (?, ?) AND b.played = 1 AND b.minutes IS NOT NULL",
            conn,
            params=(season, prior),
        )
        if "role_change" in wanted:
            found = role_changes(lines[lines["season"] == season], today, out_by_team)
            rank = total_value(projections[projections["minutes"] > 0], pool, cfg).rank(ascending=False)
            up_limit = cfg.pool_size * float(C("signals.gem_rank_mult"))
            down_limit = cfg.pool_size * float(C("signals.relevance_rank_mult"))
            signals += [
                s
                for s in found
                if rank.get(s.espn_id, float("inf")) <= (up_limit if s.direction == "up" else down_limit)
            ]
        if "opportunity" in wanted and injured:
            signals += opportunities(injured, lines, projections, season, statuses)
    if "espn_revision" in wanted:
        now = player_tables.espn_snapshot_table(conn, season)
        days = int(C("signals.espn_revision_days"))
        then = player_tables.espn_snapshot_table(conn, season, on_or_before=(today - timedelta(days=days)).isoformat())
        market = None
        if not actual.empty:
            act = actual[actual["gp"] >= int(C("signals.min_games"))]
            market = total_value(act, pool, cfg) if not act.empty else None
        signals += espn_revisions(now, then, pool, cfg, market)
    return signals


OWNER_HINTS = {
    ("buy_low", "mine"): "hold: expect a bounce-back, don't sell low",
    ("buy_low", "other"): "trade target: buy before his stats recover (evaluate_trade / find_trades)",
    ("buy_low", "fa"): "pickup or stash candidate (evaluate_add_drop / waiver_decision)",
    ("sell_high", "mine"): "sell-high: shop him while his line is inflated (find_trades / evaluate_trade)",
    ("sell_high", "other"): "don't pay his current price",
    ("sell_high", "fa"): "short-term pickup only: expect regression",
    ("up", "mine"): "hold: his value is rising",
    ("up", "other"): "trade target before the market reacts",
    ("up", "fa"): "pickup candidate: act before other managers (evaluate_add_drop / waiver_decision)",
    ("down", "mine"): "watch: consider replacing or selling before the market notices",
    ("down", "other"): "avoid acquiring",
    ("down", "fa"): "ignore",
}


def action_hint(kind: str, direction: str, owner: str) -> str:
    return OWNER_HINTS.get((kind, owner)) or OWNER_HINTS.get((direction, owner), "")
