"""Policy tuner: which policy settings would have made better decisions, judged on replayed real outcomes.

For one setting and a list of candidate values, `evaluate` re-runs the shared decision rule (`choosers.choose`) over
every graded decision the setting affects, with the candidate in place of the current value (all other settings as
now), and sums the replayed outcome (`result.options[id].cats_won`, from fbm.gm.replay) of the option that would
have been picked. Chase/concede thresholds are also checked against the categories' final results, and
`policy.odds_spread_scale` is judged by the odds' Brier score (fbm.gm.odds_reliability).

`suggest` lists changes that beat the current value with enough graded weeks (`policy.min_weeks`) and within the
setting's max_step. It NEVER applies anything: a suggestion becomes a `policy` proposal that goes through the
reviewer and the owner's explicit approval (AGENTS.md hard rule 2).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping
from datetime import date
from typing import Any

from fbm.constants import C
from fbm.gm import choosers, decisions, policy

ALL_KINDS = decisions.KINDS
THRESHOLD_KEYS = ("policy.concede_below", "policy.lock_above", "policy.contested_band")
SPREAD_KEY = "policy.odds_spread_scale"
# Settings the replay cannot judge yet (no replay hook): never suggested automatically.
UNTUNED = ("policy.season_weights", "policy.opponent_adds")
EPS = 1e-9


def affected_kinds(key: str) -> tuple[str, ...]:
    """Decision kinds whose choice depends on this setting."""
    if key.startswith("policy.view."):
        return tuple(k for k, v in choosers.KIND_VIEW_PARAM.items() if v == key)
    return {
        "policy.contested_band": ALL_KINDS,
        "policy.weight_floor": ALL_KINDS,
        "policy.ros_penalty": ALL_KINDS,
        "policy.stream_min_gain": (*choosers.ACQUIRE_KINDS, "trade", "drop"),
        "policy.add_reserve": choosers.ACQUIRE_KINDS,
        "policy.il_hold_days": ("il",),
        "policy.pct_risk.enabled": ("lineup",),
        "policy.pct_risk.max_impact": ("lineup",),
        "policy.concede_below": ("chase_concede",),
        "policy.lock_above": ("chase_concede",),
    }.get(key, ())


def _week_of(d: Mapping[str, Any]) -> Any:
    if d.get("week") is not None:
        return ("w", int(d["week"]))
    y, w, _ = date.fromisoformat(str(d["et_date"])[:10]).isocalendar()
    return ("iso", y, w)


def _cats(result: Mapping[str, Any], option_id: str) -> float | None:
    opt = (result.get("options") or {}).get(str(option_id))
    if not opt or opt.get("cats_won") is None:
        return None
    return float(opt["cats_won"])


def _final_cat_results(d: Mapping[str, Any]) -> dict[str, float]:
    """Final category results of the decision's week: {cat: 1 | 0.5 | 0}."""
    r = d.get("result") or {}
    if r.get("cat_results"):
        return {k: float(v) for k, v in r["cat_results"].items()}
    for oid in (r.get("chosen"), r.get("recommended"), d.get("chosen"), d.get("recommended")):
        opt = (r.get("options") or {}).get(str(oid)) if oid else None
        if opt and opt.get("cat_results"):
            return {k: float(v) for k, v in opt["cat_results"].items()}
    return {}


def classify_cost(d: Mapping[str, Any], params: Mapping[str, Any]) -> float:
    """Categories mis-called by chase/concede under `params`: conceded but won (1, tie 0.5) + locked but lost."""
    sit = d.get("situation") or {}
    finals = _final_cat_results(d)
    status = choosers.classify(sit.get("odds") or {}, params, sit.get("punts") or ())
    cost = 0.0
    for cat, st in status.items():
        if cat not in finals:
            continue
        if st == "concede":
            cost += finals[cat]
        elif st == "lock":
            cost += 1.0 - finals[cat]
    return cost


def _graded(conn: sqlite3.Connection, kinds: Iterable[str]) -> list[dict[str, Any]]:
    kinds = set(kinds)
    return [d for d in decisions.query(conn, graded=True, limit=100000) if d["kind"] in kinds and d.get("result")]


def _safe_choose(d: Mapping[str, Any], params: Mapping[str, Any]) -> str | None:
    try:
        return choosers.choose(d, params)
    except (ValueError, KeyError, TypeError):
        return None


def _evaluate_spread(conn: sqlite3.Connection, candidates: list) -> list[dict[str, Any]]:
    from fbm.gm import odds_reliability  # built by ws-decisions

    out = []
    for c in candidates:
        value = policy.validate(SPREAD_KEY, c)
        rep = odds_reliability.report(conn, scale=value) or {}
        n_weeks = rep.get("n_weeks", rep.get("weeks", rep.get("n", 0)))
        if isinstance(n_weeks, list):
            n_weeks = len(n_weeks)
        out.append(
            {
                "value": value,
                "brier": rep.get("brier"),
                "n_weeks": int(n_weeks or 0),
                "n_decisions": int(rep.get("n_forecasts", rep.get("n", 0)) or 0),
                "report": {k: v for k, v in rep.items() if k in ("brier", "expected_vs_actual", "buckets")},
            }
        )
    return out


def evaluate(conn: sqlite3.Connection, key: str, candidates: list) -> list[dict[str, Any]]:
    """Per candidate value: n_decisions, n_weeks, cats_won, cats_won_delta (vs current), regret (vs best option),
    changed (decision ids whose choice changes); threshold keys also classify_cost(_delta)."""
    policy.param(key)
    if key == SPREAD_KEY:
        return _evaluate_spread(conn, candidates)
    current = policy.all_values(conn)
    graded = _graded(conn, affected_kinds(key))
    base: dict[int, tuple[str, float]] = {}
    for d in graded:
        cid = _safe_choose(d, current)
        cats = _cats(d["result"], cid) if cid else None
        if cats is not None:
            base[d["id"]] = (cid, cats)
    out = []
    for cand in candidates:
        value = policy.validate(key, cand)
        params = {**current, key: value}
        n, weeks, total, delta, regret, changed = 0, set(), 0.0, 0.0, 0.0, []
        cost = cost_delta = 0.0
        for d in graded:
            if d["kind"] == "chase_concede" and key in THRESHOLD_KEYS:
                c_new, c_cur = classify_cost(d, params), classify_cost(d, current)
                cost += c_new
                cost_delta += c_new - c_cur
                weeks.add(_week_of(d))
            if d["id"] not in base:
                continue
            oid = _safe_choose(d, params)
            cats = _cats(d["result"], oid) if oid else None
            if cats is None:
                continue
            best = max(
                (float(o["cats_won"]) for o in d["result"]["options"].values() if o.get("cats_won") is not None),
                default=cats,
            )
            n += 1
            weeks.add(_week_of(d))
            total += cats
            delta += cats - base[d["id"]][1]
            regret += best - cats
            if oid != base[d["id"]][0]:
                changed.append(d["id"])
        row = {
            "value": value,
            "current": value == current[key],
            "n_decisions": n,
            "n_weeks": len(weeks),
            "cats_won": round(total, 4),
            "cats_won_delta": round(delta, 4),
            "regret": round(regret, 4),
            "changed": changed,
        }
        if key in THRESHOLD_KEYS:
            row["classify_cost"] = round(cost, 4)
            row["classify_cost_delta"] = round(cost_delta, 4)
        out.append(row)
    return out


def candidates_for(key: str, current: Any) -> list:
    """Values one proposal may move to: within bounds and max_step of the current value."""
    p = policy.param(key)
    if p.kind == "choice":
        return [c for c in (p.choices or ()) if c != current]
    if p.kind == "bool":
        return [not current]
    if p.kind not in ("number", "int") or p.max_step is None:
        return []
    lo, hi = p.bounds or (float("-inf"), float("inf"))
    if p.kind == "int":
        steps = [s for s in range(-int(p.max_step), int(p.max_step) + 1) if s]
        vals = [int(current) + s for s in steps]
    else:
        ms = float(p.max_step)
        vals = [round(float(current) + s, 6) for s in (-ms, -ms / 2, ms / 2, ms)]
    return [v for v in vals if lo - EPS <= v <= hi + EPS and policy.step_problem(key, current, v) is None]


def _improvement(key: str, row: Mapping[str, Any], cur_row: Mapping[str, Any]) -> float:
    if key == SPREAD_KEY:
        if row.get("brier") is None or cur_row.get("brier") is None:
            return 0.0
        return float(cur_row["brier"]) - float(row["brier"])
    return float(row["cats_won_delta"]) - float(row.get("classify_cost_delta", 0.0))


def suggest(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Candidate changes beating the current value on >= policy.min_weeks graded weeks, each within max_step.
    Read-only: never applies anything (a change needs a reviewed, owner-approved policy proposal)."""
    min_weeks = int(C("policy.min_weeks"))
    current = policy.all_values(conn)
    out = []
    for key in policy.REGISTRY:
        if key in UNTUNED:
            continue
        cands = candidates_for(key, current[key])
        if not cands:
            continue
        try:
            rows = evaluate(conn, key, [current[key], *cands])
        except ImportError:
            continue
        cur_row, alt = rows[0], rows[1:]
        best, best_gain = None, 0.0
        for r in alt:
            if r["n_weeks"] < min_weeks:
                continue
            gain = _improvement(key, r, cur_row)
            if gain > best_gain + EPS:
                best, best_gain = r, gain
        if best is None:
            continue
        out.append(
            {
                "key": key,
                "current": current[key],
                "value": best["value"],
                "improvement": round(best_gain, 4),
                "metric": "brier_reduction" if key == SPREAD_KEY else "replayed_cats_won",
                "n_weeks": best["n_weeks"],
                "n_decisions": best["n_decisions"],
                "evidence": {
                    "key": key,
                    "current_value": current[key],
                    "proposed_value": best["value"],
                    "min_weeks": min_weeks,
                    "current": cur_row,
                    "candidates": alt,
                },
            }
        )
    return sorted(out, key=lambda s: -s["improvement"])


def draft_proposal_payload(suggestion: Mapping[str, Any]) -> dict[str, Any]:
    """Arguments for create_proposal(kind="policy", ...) from one suggestion (still needs review + owner approval)."""
    key, cur, new = suggestion["key"], suggestion["current"], suggestion["value"]
    metric = suggestion.get("metric", "replayed_cats_won")
    return {
        "kind": "policy",
        "title": f"Policy: {key} {cur} -> {new}",
        "summary": (
            f"Change {key} from {cur!r} to {new!r}: replaying {suggestion.get('n_decisions', 0)} graded decisions over "
            f"{suggestion.get('n_weeks', 0)} weeks improves {metric} by {suggestion.get('improvement')}."
        ),
        "payload": {"changes": {key: new}, "evidence": suggestion.get("evidence") or {}},
        "rationale": f"policy_tuning.suggest: {metric} +{suggestion.get('improvement')} vs current (replay).",
        "evidence": suggestion.get("evidence") or {},
    }
