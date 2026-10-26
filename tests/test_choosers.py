"""The shared decision rule (fbm.gm.choosers)."""

from __future__ import annotations

from fbm.gm import choosers, policy

P = {k: p.default for k, p in policy.REGISTRY.items()}


def _d(kind, options, **situation):
    return {"id": 1, "kind": kind, "situation": situation, "options": options}


def test_week_weights_and_classify():
    odds = {"reb": 0.5, "tpm": 0.06, "fg_pct": 0.91, "pts": 0.62, "tov": 0.4}
    w = choosers.week_weights(odds, P, punts=["tov"])
    assert w["reb"] == 1.0 and w["tov"] == 0.0
    assert w["tpm"] == P["policy.weight_floor"]
    c = choosers.classify(odds, P, punts=["tov"])
    assert c == {"reb": "chase", "tpm": "concede", "fg_pct": "lock", "pts": "chase", "tov": "punt"}


def test_view_and_requires_filter():
    d = _d(
        "lineup",
        [
            {"id": "s7", "label": "a", "view": "STREAM7", "cat_scores": {"reb": 1}},
            {"id": "b14", "label": "b", "view": "BLEND14", "cat_scores": {"reb": 5}},
            {
                "id": "risk",
                "label": "c",
                "view": "STREAM7",
                "cat_scores": {"reb": 3},
                "requires": {"policy.pct_risk.enabled": True},
            },
        ],
        odds={"reb": 0.5},
    )
    assert choosers.choose(d, P) == "s7"
    assert choosers.choose(d, {**P, "policy.view.lineup": "BLEND14"}) == "b14"
    assert choosers.choose(d, {**P, "policy.pct_risk.enabled": True}) == "risk"


def test_stream_min_gain_and_add_reserve():
    opts = [
        {"id": "pat", "label": "stand pat", "stand_pat": True, "cat_scores": {}},
        {"id": "one", "label": "1 add", "adds": 1, "cat_scores": {"reb": 0.04}},
        {"id": "two", "label": "2 adds", "adds": 2, "cat_scores": {"reb": 0.30}},
    ]
    mon = _d("stream_plan", opts, odds={"reb": 0.5}, weekday=0, adds_left=2)
    assert choosers.choose(mon, P) == "pat"  # 'two' breaks the reserve; 'one' gains < 0.05
    thu = _d("stream_plan", opts, odds={"reb": 0.5}, weekday=3, adds_left=2)
    assert choosers.choose(thu, P) == "two"
    assert choosers.choose(mon, {**P, "policy.add_reserve": 0}) == "two"


def test_ros_penalty_and_il_hold():
    opts = [
        {"id": "pat", "label": "keep", "stand_pat": True, "cat_scores": {}},
        {"id": "drop", "label": "drop star", "cat_scores": {"reb": 0.4}, "ros_cost": 1.0},
    ]
    assert choosers.choose(_d("drop", opts, odds={"reb": 0.5}), P) == "pat"
    il = [
        {"id": "hold", "label": "hold", "action": "hold", "cat_scores": {}},
        {"id": "cut", "label": "drop", "action": "drop", "cat_scores": {"reb": 0.5}},
    ]
    assert choosers.choose(_d("il", il, odds={"reb": 0.5}, expected_days_out=10), P) == "hold"
    assert choosers.choose(_d("il", il, odds={"reb": 0.5}, expected_days_out=30), P) == "cut"
