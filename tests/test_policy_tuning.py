import sys
import types

import pytest

from fbm.gm import decisions, policy, policy_tuning
from fbm.store import db


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def _lineup(conn, week, cats7=4.0, cats14=6.0):
    opts = [
        {"id": "s7", "label": "STREAM7 lineup", "view": "STREAM7", "cat_scores": {"reb": 1.0}},
        {"id": "s14", "label": "STREAM14 lineup", "view": "STREAM14", "cat_scores": {"reb": 1.0}},
    ]
    did = decisions.log(
        conn,
        kind="lineup",
        trigger="daily-gm",
        et_date=f"2026-11-{week + 1:02d}",
        week=week,
        situation={"odds": {"reb": 0.5}, "punts": []},
        options=opts,
        recommended="s7",
    )
    decisions.save_result(
        conn,
        did,
        {"options": {"s7": {"cats_won": cats7}, "s14": {"cats_won": cats14}}, "recommended": "s7", "complete": True},
    )
    return did


def test_evaluate_view_replays_chosen_option(conn):
    ids = [_lineup(conn, w) for w in range(1, 6)]
    rows = policy_tuning.evaluate(conn, "policy.view.lineup", ["STREAM7", "STREAM14"])
    cur, alt = rows
    assert cur["current"] and cur["cats_won_delta"] == 0 and cur["regret"] == 10.0 and cur["changed"] == []
    assert alt["n_decisions"] == 5 and alt["n_weeks"] == 5
    assert alt["cats_won_delta"] == 10.0 and alt["regret"] == 0.0 and alt["changed"] == ids


def test_suggest_needs_min_weeks(conn):
    for w in range(1, 4):  # 3 weeks < policy.min_weeks (4)
        _lineup(conn, w)
    assert not [s for s in policy_tuning.suggest(conn) if s["key"] == "policy.view.lineup"]
    _lineup(conn, 4)
    sugg = [s for s in policy_tuning.suggest(conn) if s["key"] == "policy.view.lineup"]
    assert sugg and sugg[0]["value"] == "STREAM14" and sugg[0]["improvement"] == 8.0
    payload = policy_tuning.draft_proposal_payload(sugg[0])
    assert payload["kind"] == "policy" and payload["payload"]["changes"] == {"policy.view.lineup": "STREAM14"}
    assert policy.get(conn, "policy.view.lineup") == "STREAM7"  # never applied


def test_candidates_respect_max_step_and_bounds():
    c = policy_tuning.candidates_for("policy.contested_band", 0.25)
    assert c and all(abs(v - 0.25) <= 0.05 + 1e-9 for v in c)
    assert policy_tuning.candidates_for("policy.contested_band", 0.45) == [0.4, 0.425]
    assert policy_tuning.candidates_for("policy.add_reserve", 0) == [1]
    assert "STREAM7" not in policy_tuning.candidates_for("policy.view.lineup", "STREAM7")


def test_concede_threshold_cost(conn):
    for w in range(1, 6):
        did = decisions.log(
            conn,
            kind="chase_concede",
            trigger="weekly-plan",
            et_date=f"2026-11-{w + 1:02d}",
            week=w,
            situation={"odds": {"reb": 0.22, "ast": 0.5}, "punts": []},
            options=[{"id": "plan", "label": "plan", "cat_scores": {"reb": 1.0}}],
            recommended="plan",
        )
        decisions.save_result(
            conn,
            did,
            {"options": {"plan": {"cats_won": 5.0, "cat_results": {"reb": 1, "ast": 0}}}, "recommended": "plan"},
        )
    rows = policy_tuning.evaluate(conn, "policy.concede_below", [0.25, 0.2])
    assert rows[0]["classify_cost"] == 5.0 and rows[1]["classify_cost_delta"] == -5.0
    sugg = [s for s in policy_tuning.suggest(conn) if s["key"] == "policy.concede_below"]
    assert sugg and sugg[0]["value"] <= 0.22


def test_spread_scale_uses_odds_reliability(conn, monkeypatch):
    fake = types.ModuleType("fbm.gm.odds_reliability")
    fake.report = lambda conn, scale=None, view=None: {"brier": abs(scale - 1.25), "n_weeks": 6, "n_forecasts": 54}
    monkeypatch.setitem(sys.modules, "fbm.gm.odds_reliability", fake)
    import fbm.gm as gm_pkg

    monkeypatch.setattr(gm_pkg, "odds_reliability", fake, raising=False)
    rows = policy_tuning.evaluate(conn, "policy.odds_spread_scale", [1.0, 1.25])
    assert rows[1]["brier"] == 0.0
    sugg = [s for s in policy_tuning.suggest(conn) if s["key"] == "policy.odds_spread_scale"]
    assert sugg[0]["value"] == 1.25 and sugg[0]["metric"] == "brier_reduction"
