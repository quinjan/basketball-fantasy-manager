"""The shared decision rule: pick one option from a logged decision given the policy settings.

Contract shared by the engine (generates + scores options, then MUST call `choose` to pick its recommendation), the
replay grader (grades every logged option on real outcomes) and the policy tuner (re-runs `choose` over past decisions
with candidate settings to see which option each setting would have picked).

Decision dict (as stored by fbm.gm.decisions): {kind, situation, options, ...}

situation keys used here (engine must log them):
    odds: {cat: P(win)+0.5*P(tie)}   calibrated weekly odds (HT totals + FBM spread) for my matchup
    punts: [cat, ...]                approved strategy punts
    weekday: 0..6                    ET weekday of the decision (0 = Monday)
    adds_left: int | None            adds left this week
    expected_days_out: float | None  il decisions: the injured player's expected days out

Option dict keys used here:
    id, label                        required (fbm.gm.decisions validates)
    view: "ROS"|"BLEND14"|...        the HT projection view the option was generated with; options without `view`
                                     are view-independent. Only options of the active view (or no view) are eligible.
    cat_scores: {cat: float}         per-category score of the option (lineup: summed per-category G of the day's
                                     starters; stream/claim/trade/drop/il: change in P(win) per category vs standing
                                     pat from the comparator; category_weights: expected categories by category)
    ros_cost: float                  ROS G value given up (drops); default 0
    adds: int                        adds the option uses; default 0
    stand_pat: bool                  the do-nothing option (streams, claims, trades, drops)
    action: "hold"|"drop"|...        il options
    requires: {policy_key: value}    option eligible only when the setting has this value (e.g. pct-risk lineups)
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

# Which HT-view setting governs each decision kind.
KIND_VIEW_PARAM = {
    "lineup": "policy.view.lineup",
    "stream_plan": "policy.view.stream",
    "waiver_claim": "policy.view.claim",
    "chase_concede": "policy.view.weekly_odds",
    "category_weights": "policy.view.ros",
    "il": "policy.view.ros",
    "trade": "policy.view.ros",
    "drop": "policy.view.ros",
}
ACQUIRE_KINDS = ("stream_plan", "waiver_claim")
THURSDAY = 3


def week_weights(odds: Mapping[str, float], params: Mapping[str, Any], punts: Iterable[str] = ()) -> dict[str, float]:
    """Per-category weight in [0, 1]: 1 when contested (|p - 0.5| = 0), falling linearly to the floor at
    |p - 0.5| = contested_band and staying there (won or lost categories). Punts get 0."""
    band = float(params["policy.contested_band"])
    floor = float(params["policy.weight_floor"])
    punted = set(punts)
    out: dict[str, float] = {}
    for cat, p in odds.items():
        if cat in punted:
            out[cat] = 0.0
            continue
        closeness = max(0.0, 1.0 - abs(float(p) - 0.5) / band) if band > 0 else 0.0
        out[cat] = floor + (1.0 - floor) * closeness
    return out


def classify(odds: Mapping[str, float], params: Mapping[str, Any], punts: Iterable[str] = ()) -> dict[str, str]:
    """chase | hold | lock | concede | punt per category for the week."""
    punted = set(punts)
    lo, hi = float(params["policy.concede_below"]), float(params["policy.lock_above"])
    band = float(params["policy.contested_band"])
    out = {}
    for cat, p in odds.items():
        p = float(p)
        if cat in punted:
            out[cat] = "punt"
        elif p < lo:
            out[cat] = "concede"
        elif p > hi:
            out[cat] = "lock"
        elif abs(p - 0.5) <= band:
            out[cat] = "chase"
        else:
            out[cat] = "hold"
    return out


def option_value(option: Mapping[str, Any], weights: Mapping[str, float], params: Mapping[str, Any]) -> float:
    scores = option.get("cat_scores") or {}
    value = sum(float(weights.get(cat, 1.0)) * float(v) for cat, v in scores.items())
    return value - float(params["policy.ros_penalty"]) * float(option.get("ros_cost") or 0.0)


def eligible(decision: Mapping[str, Any], params: Mapping[str, Any]) -> list[dict[str, Any]]:
    kind = decision["kind"]
    view = params.get(KIND_VIEW_PARAM.get(kind, ""), None)
    out = []
    for o in decision["options"]:
        if o.get("view") and view and o["view"] != view:
            continue
        if any(params.get(k) != v for k, v in (o.get("requires") or {}).items()):
            continue
        out.append(o)
    return out


def _within_add_reserve(option: Mapping[str, Any], situation: Mapping[str, Any], params: Mapping[str, Any]) -> bool:
    adds = int(option.get("adds") or 0)
    left = situation.get("adds_left")
    if not adds or left is None or int(situation.get("weekday", THURSDAY)) >= THURSDAY:
        return True
    return adds <= int(left) - int(params["policy.add_reserve"])


def choose(decision: Mapping[str, Any], params: Mapping[str, Any]) -> str:
    """The option id FBM recommends for this decision under `params` (full policy values dict)."""
    situation = decision.get("situation") or {}
    options = eligible(decision, params)
    if not options:
        raise ValueError(f"Decision {decision.get('id')} has no option eligible under these settings.")
    weights = week_weights(situation.get("odds") or {}, params, situation.get("punts") or ())
    kind = decision["kind"]

    if kind == "il":
        days = situation.get("expected_days_out")
        hold = next((o for o in options if o.get("action") == "hold"), None)
        if hold is not None and days is not None and float(days) <= float(params["policy.il_hold_days"]):
            return str(hold["id"])

    if kind in ACQUIRE_KINDS:
        options = [o for o in options if o.get("stand_pat") or _within_add_reserve(o, situation, params)]

    best = max(options, key=lambda o: option_value(o, weights, params))  # first wins ties (logged order)
    if kind in (*ACQUIRE_KINDS, "trade", "drop"):
        stand = next((o for o in options if o.get("stand_pat")), None)
        if stand is not None and best is not stand:
            gain = option_value(best, weights, params) - option_value(stand, weights, params)
            if gain < float(params["policy.stream_min_gain"]):
                return str(stand["id"])
    return str(best["id"])
