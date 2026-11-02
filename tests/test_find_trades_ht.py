"""HT-mode find_trades: HT Trade Finder ideas + FBM's own search, merged and judged by FBM."""

from types import SimpleNamespace

import pandas as pd
import pytest

from fbm.analytics import trades as trades_mod
from fbm.analytics.trades import evaluate_trade
from fbm.gm import services as svc
from fbm.hashtag import store as ht_store
from tests.test_streaming_trades import trade_league


def _idea(partner, give, get, fairness, partner_key=None):
    def side(ids):
        return [{"ht_id": f"h{i}", "espn_id": i, "name": f"N{i}"} for i in ids]

    return {
        "partner_team_key": partner_key, "partner_name": partner, "give": side(give), "get": side(get),
        "my_delta": 0.4, "settings": {"fairness": fairness},
        "data": {"badges": ["Within fair margin"], "ranks_now": {"pts": 3}, "ranks_after": {"pts": 2}},
    }  # fmt: skip


@pytest.fixture
def ht_mode(monkeypatch):
    sim = trade_league()
    ideas = [
        _idea("Bigs", ["a1"], ["b1"], "fair"),  # partner key resolved from the name
        _idea("Bigs", ["a1"], ["b1"], "10", partner_key="t2"),  # same idea from the 10%-my-way run
        _idea("Mixed", [None], ["c2"], "fair"),  # player HT couldn't map to ESPN
    ]
    ros = pd.DataFrame({"total": {"a1": 2.0, "a2": -0.5, "b1": 3.0, "b2": -0.6, "c1": 2.0, "c2": 1.5}})
    monkeypatch.setattr(svc, "active_league", lambda: sim.league)
    monkeypatch.setattr(svc, "ctx", lambda: SimpleNamespace(hashtag=True, projections=pd.DataFrame(columns=["name"])))
    monkeypatch.setattr(svc._ht, "gate", lambda conn=None: None)
    monkeypatch.setattr(svc, "_trade_setup", lambda league, n: (sim, ["f1"], {}, {}))
    monkeypatch.setattr(svc, "_ht_trade_ideas", lambda league, part: (ideas, ["hashtag trade finder (test)"]))
    monkeypatch.setattr(svc, "_strategy_punts", lambda x: ())
    monkeypatch.setattr(svc, "_playoff_trade_info", lambda *a: {})
    monkeypatch.setattr(svc, "_conn", lambda: None)
    monkeypatch.setattr(ht_store, "projections", lambda conn, view: ros)

    def engine(sim_, my, fa, punts, partners, max_give, max_get, limit, partner_builds):
        return [evaluate_trade(sim_, my, "t2", ["a1"], ["b1"], fa), evaluate_trade(sim_, my, "t3", ["a2"], ["c2"], fa)]

    monkeypatch.setattr(trades_mod, "find_trades", engine)
    return sim


def test_merges_ht_ideas_and_engine_candidates(ht_mode):
    out = svc.find_trades_tool(max_give=1, max_get=1, limit=10)
    assert out["counts"] == {"ht_ideas": 3, "ht_ideas_skipped": 1, "fbm_candidates": 2, "consensus": 1}
    rows = out["ht_ideas"]
    assert len(rows) == 2
    matched = next(r for r in rows if r["partner"] == "Bigs")
    assert matched["fairness_settings"] == ["fair", "10"] and matched["also_found_by_fbm"] is True
    assert {"fbm_verdict", "fbm_my_expected_cats_delta"} <= set(matched)
    assert "skipped" in next(r for r in rows if r["partner"] == "Mixed")
    assert len(out["trades"]) == 2
    first, second = out["trades"]
    assert first["consensus"] and first["sources"] == ["hashtag", "fbm"] and first["ht_idea"]["badges"]
    assert second["sources"] == ["fbm"] and not second["consensus"] and "ht_idea" not in second
    assert first["ht_value"]["received_minus_sent"] == 1.0 and second["ht_value"]["received_minus_sent"] == 1.5


def test_ht_value_read_and_partner_key_fallback(ht_mode):
    ros = ht_store.projections(None, "ROS")
    c = SimpleNamespace(projections=pd.DataFrame(columns=["name"]))
    v = svc._ht_value_read(ros, c, ["a1"], ["b1", "zz"])
    assert v["received_minus_sent"] == 1.0 and v["pct_of_sent"] == 50.0 and v["missing_from_ht"] == ["zz"]
    throw_in = svc._ht_value_read(ros, c, ["a1"], ["b1", "b2"])  # b2 is below replacement: counts 0, not -0.6
    assert throw_in["received_minus_sent"] == 1.0
    league = ht_mode.league
    assert svc._ht_partner_key(league, {"partner_name": "bigs"}) == "t2"
    assert svc._ht_partner_key(league, {"partner_team_key": "t3", "partner_name": "x"}) == "t3"
    assert svc._ht_partner_key(league, {"partner_name": "Nobody"}) is None
