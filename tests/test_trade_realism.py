"""Partner-realism gate: find_trades and create_proposal(kind="trade") share one screen.

Fixtures mirror the numbers of real offers (P-21 Mitchell+Spencer for Murphy: partner ranking value -3.48; P-37
Embiid for George+Watson: about +39% HT value premium our way; P-27 Zion for Sengun: win-win) on synthetic players."""

from types import SimpleNamespace

import pandas as pd
import pytest

from fbm.analytics import trades as trades_mod
from fbm.analytics.trades import (
    PartnerBuild,
    TradeEval,
    TradeSide,
    evaluate_trade,
    find_trades,
    ht_value_premium,
    realism_of,
    trade_realism,
)
from fbm.gm import memory
from fbm.gm import proposals as props
from fbm.gm import services as svc
from fbm.hashtag import store as ht_store
from fbm.store import db
from tests.test_find_trades_ht import ht_mode  # noqa: F401 - fixture
from tests.test_streaming_trades import trade_league

MAX_LOSS, MAX_PREMIUM, MIN_CATS = 1.0, 0.15, 0.0  # the constants' defaults
REAL_EVALUATE = svc._evaluate_trade_ev


def rules_by_name(r):
    return {x.rule: x for x in r.rules}


# ---------------------------------------------------------------- the pure screen


def test_p21_equivalent_fails_on_ranking_value():
    r = trade_realism(-3.48, -0.214, None)
    assert not r.ok and [x.rule for x in r.failures] == ["their_ranking_value", "their_expected_cats"]
    ranking = rules_by_name(r)["their_ranking_value"]
    assert ranking.value == -3.48 and ranking.threshold == -MAX_LOSS
    assert "-3.48" in r.refusal() and "1.00" in r.refusal() and "trade.max_their_value_loss" in r.refusal()


def test_p37_equivalent_fails_on_ht_premium_with_sign_toward_us():
    # P-37: Embiid sent, George + Watson received; HT's market says about +39% our way
    premium = ht_value_premium(sent=10.0, received=13.9)
    assert premium == pytest.approx(0.39)
    r = trade_realism(-0.4, 0.116, premium)  # by their build the partner gains +0.116 cats: only the premium fails
    assert [x.rule for x in r.failures] == ["ht_value_premium"]
    assert "+39%" in r.refusal() and "15%" in r.refusal()
    # the other direction (we overpay) is never a realism problem
    assert trade_realism(0.0, 0.2, ht_value_premium(sent=13.9, received=10.0)).ok


def test_p27_equivalent_passes():
    r = trade_realism(-0.15, 0.179, ht_value_premium(sent=10.0, received=10.5))
    assert r.ok and r.failures == ()
    assert r.as_dict()["ok"] and r.as_dict()["failing"] == []


@pytest.mark.parametrize(
    ("kwargs", "failing"),
    [
        ({"them_value_delta": -1.0}, []),  # exactly the limit passes
        ({"them_value_delta": -1.01}, ["their_ranking_value"]),
        ({"ht_premium": 0.15}, []),
        ({"ht_premium": 0.16}, ["ht_value_premium"]),
        ({"them_cats_delta": 0.0}, ["their_expected_cats"]),  # no gain by their own build
        ({"them_cats_delta": 0.001}, []),
        ({"them_cats_delta": -0.3}, ["their_expected_cats"]),
    ],
)
def test_each_rule_and_its_boundary(kwargs, failing):
    base = {"them_value_delta": 0.0, "them_cats_delta": 0.1, "ht_premium": 0.0}
    base.update(kwargs)
    r = trade_realism(base["them_value_delta"], base["them_cats_delta"], base["ht_premium"])
    assert [x.rule for x in r.failures] == failing


def test_ht_rule_skipped_without_an_ht_read_and_thresholds_overridable():
    r = trade_realism(0.0, 0.1, None, ht_note="demo league")
    ht = rules_by_name(r)["ht_value_premium"]
    assert r.ok and ht.value is None and not ht.checked and "demo league" in ht.note
    assert trade_realism(-2.0, 0.1, max_their_value_loss=3.0).ok
    assert not trade_realism(0.0, 0.1, 0.3, max_ht_premium=0.2).ok
    assert trade_realism(0.0, -0.01, min_their_cats_delta=-0.05).ok
    with pytest.raises(ValueError):
        trade_realism(0.0, 0.1, rules=("nope",))


def test_ht_value_premium_edges():
    assert ht_value_premium(0.0, 0.0) is None
    assert ht_value_premium(0.0, 2.0) == trades_mod.HT_PREMIUM_CAP  # sending nothing for something: capped, JSON-safe
    assert ht_value_premium(10.0, 200.0) == trades_mod.HT_PREMIUM_CAP


def _candidate_ev(pid, them_value, them_cats):
    ev = make_ev(["a1"], [pid], them_value, them_cats)
    ev.me.expected_after = 0.5  # a clear gain for us
    return ev


def test_find_trades_uses_the_shared_ranking_screen(monkeypatch):
    evs = {
        "ok": _candidate_ev("ok", -0.5, 0.1),
        "limit": _candidate_ev("limit", -1.0, 0.1),  # exactly at the limit still passes
        "too_costly": _candidate_ev("too_costly", -1.5, 0.1),
        "cats_dip": _candidate_ev("cats_dip", 0.0, -0.03),  # find_trades tolerates -0.05; the proposal gate does not
        "cats_bad": _candidate_ev("cats_bad", 0.0, -0.2),
    }
    cands = [trades_mod.TradeCandidate("t2", ("a1",), (pid,), 1.0, 0.0) for pid in evs]
    monkeypatch.setattr(trades_mod, "screen_trades", lambda *a, **k: cands)
    monkeypatch.setattr(trades_mod, "evaluate_trade", lambda sim, my, partner, give, get, *rest: evs[get[0]])
    calls = []
    real = trades_mod.trade_realism

    def spy(*a, **k):
        calls.append(k.get("rules"))
        return real(*a, **k)

    monkeypatch.setattr(trades_mod, "trade_realism", spy)
    results = find_trades(trade_league(), "t1", ["f1"])
    assert {r.me.gets[0] for r in results} == {"ok", "limit", "cats_dip"}
    assert calls and all(c == ("their_ranking_value",) for c in calls)
    wide = find_trades(trade_league(), "t1", ["f1"], max_their_value_loss=2.0)
    assert {r.me.gets[0] for r in wide} == {"ok", "limit", "too_costly", "cats_dip"}
    assert realism_of(evs["cats_dip"]).failures[0].rule == "their_expected_cats"  # ...but the gate would refuse it


# ---------------------------------------------------------------- create_proposal(kind="trade")


@pytest.fixture
def gm(monkeypatch):
    """services wired to an in-memory DB, the synthetic league and a stubbed engine evaluation (set `env.ev`)."""
    sim = trade_league()
    conn = db.connect(":memory:")
    journal = []
    env = SimpleNamespace(conn=conn, journal=journal, ev=None, hashtag=True, sim=sim)
    env.ros = pd.DataFrame({"total": {"a1": 10.0, "a2": 3.0, "b1": 10.5, "b2": 3.4, "c1": 8.0, "c2": 4.0}})

    monkeypatch.setattr(svc, "_conn", lambda: conn)
    monkeypatch.setattr(svc, "active_league", lambda: sim.league)
    monkeypatch.setattr(
        svc, "ctx", lambda: SimpleNamespace(hashtag=env.hashtag, projections=pd.DataFrame(columns=["name"]))
    )
    monkeypatch.setattr(svc._ht, "gate", lambda conn=None: None)
    monkeypatch.setattr(svc, "_evaluate_trade_ev", lambda league, team, give, get, n=1500: env.ev)
    monkeypatch.setattr(memory, "journal_append", lambda kind, title, body="": journal.append((kind, title, body)))
    monkeypatch.setattr(ht_store, "projections", lambda c, view: env.ros)
    yield env
    conn.close()


def make_ev(give, get, them_value, them_cats, partner="t2"):
    """A TradeEval whose partner side shows `them_value` (ranking, G above replacement) and `them_cats` (exp cats by
    their build)."""
    me = TradeSide("t1", "Me", list(give), list(get))
    them = TradeSide(
        partner, "Them", list(get), list(give), cats_before={"pts": 0.5}, cats_after={"pts": 0.5 + them_cats},
        value_delta=them_value,
    )  # fmt: skip
    return TradeEval(me, them, "test verdict")


def propose(**kw):
    payload = kw.pop("payload", {"partner": "t2", "give": ["a1"], "get": ["b1"]})
    return svc.create_proposal("trade", "Trade", "s", payload, "numbers from tools", **kw)


def stored(env):
    return env.conn.execute("SELECT COUNT(*) FROM proposals").fetchone()[0]


def test_p21_equivalent_is_refused_with_the_ranking_number(gm):
    gm.ev = make_ev(["a1", "a2"], ["b1"], them_value=-3.48, them_cats=-0.214)
    with pytest.raises(svc.GMError) as err:
        propose(payload={"partner": "t2", "give": ["a1", "a2"], "get": ["b1"]})
    msg = str(err.value)
    assert "-3.48" in msg and "1.00" in msg and "trade.max_their_value_loss" in msg
    assert "-0.214" in msg and "Rebalance" in msg and "override_reason" in msg
    assert stored(gm) == 0 and gm.journal == []


def test_p37_equivalent_is_refused_on_the_ht_premium(gm):
    gm.ros.loc["b1", "total"], gm.ros.loc["b2", "total"] = 9.0, 4.9  # sent 10.0 (a1), received 13.9: +39% our way
    gm.ev = make_ev(["a1"], ["b1", "b2"], them_value=-0.4, them_cats=0.116)
    with pytest.raises(svc.GMError) as err:
        propose(payload={"partner": "t2", "give": ["a1"], "get": ["b1", "b2"]})
    msg = str(err.value)
    assert "+39%" in msg and "15%" in msg and "trade.max_ht_value_premium" in msg
    assert "ranking value" not in msg and "expected cats" not in msg  # only the failing rule is named
    assert stored(gm) == 0


def test_p27_equivalent_passes_and_records_the_screen(gm):
    gm.ev = make_ev(["a1"], ["b1"], them_value=-0.15, them_cats=0.179)
    out = propose(evidence={"note": "kept"}, override_reason="not needed")
    real = out["evidence"]["trade_realism"]
    assert real["ok"] and real["failing"] == [] and "override_reason" not in real
    assert out["evidence"]["note"] == "kept" and "realism_override" not in out
    assert {r["rule"] for r in real["rules"]} == {"their_ranking_value", "ht_value_premium", "their_expected_cats"}
    ht = next(r for r in real["rules"] if r["rule"] == "ht_value_premium")
    assert ht["value"] == pytest.approx(0.05) and "sent 10.00, received 10.50" in ht["note"]
    assert [e for e in gm.journal if "override" in e[1]] == []


@pytest.mark.parametrize(
    ("ev_kwargs", "ros_b1", "rule"),
    [
        ({"them_value": -1.5, "them_cats": 0.2}, 10.0, "their_ranking_value"),
        ({"them_value": 0.0, "them_cats": 0.2}, 12.0, "ht_value_premium"),
        ({"them_value": 0.0, "them_cats": -0.05}, 10.0, "their_expected_cats"),
        ({"them_value": 0.0, "them_cats": 0.0}, 10.0, "their_expected_cats"),
    ],
)
def test_each_rule_refuses_on_its_own(gm, ev_kwargs, ros_b1, rule):
    gm.ros.loc["b1", "total"] = ros_b1
    gm.ev = make_ev(["a1"], ["b1"], **ev_kwargs)
    with pytest.raises(svc.GMError) as err:
        propose()
    phrases = {
        "their_ranking_value": "ranking value",
        "ht_value_premium": "Hashtag ROS value premium",
        "their_expected_cats": "expected cats",
    }
    msg = err.value.args[0]
    assert phrases[rule] in msg
    assert not [p for r, p in phrases.items() if r != rule and p in msg]  # names only the failing rule


def test_caller_supplied_numbers_are_ignored(gm):
    gm.ev = make_ev(["a1"], ["b1"], them_value=-3.0, them_cats=0.2)
    fake = {"trade_realism": {"ok": True, "rules": []}, "them": {"ranking_value_delta": 0.5}}
    with pytest.raises(svc.GMError, match="-3.00"):
        propose(evidence=fake)
    gm.ev = make_ev(["a1"], ["b1"], them_value=0.0, them_cats=0.2)
    out = propose(evidence=fake)  # a passing trade's record comes from the engine, not from the caller
    assert out["evidence"]["trade_realism"]["rules"] and out["evidence"]["trade_realism"]["ok"]


def test_override_is_recorded_journaled_and_visible(gm):
    gm.ev = make_ev(["a1", "a2"], ["b1"], them_value=-3.48, them_cats=-0.214)
    payload = {"partner": "t2", "give": ["a1", "a2"], "get": ["b1"]}
    out = propose(payload=payload, override_reason="  Partner asked for a2 in chat  ")
    assert stored(gm) == 1
    real = out["evidence"]["trade_realism"]
    assert not real["ok"] and real["override_reason"] == "Partner asked for a2 in chat"
    assert real["failing"] == ["their_ranking_value", "their_expected_cats"]
    assert out["realism_override"]["override_reason"] == "Partner asked for a2 in chat"
    assert {r["rule"] for r in out["realism_override"]["failing"]} == set(real["failing"])

    kinds = [(k, t) for k, t, _ in gm.journal]
    assert ("decision", f"{out['ref']} trade realism override") in kinds
    body = next(b for k, t, b in gm.journal if t.endswith("trade realism override"))
    assert "Partner asked for a2 in chat" in body and "-3.48" in body

    got = svc.get_proposal(out["ref"])  # get_proposal: evidence, top-level flag and the audit event
    assert got["realism_override"]["override_reason"] == "Partner asked for a2 in chat"
    assert got["evidence"]["trade_realism"]["rules"][0]["value"] == -3.48
    assert any(e["event"] == "realism_override" and "Partner asked" in e["detail"] for e in got["events"])

    listed = svc.list_proposals("open")["proposals"]  # the approvals summary reads list_proposals
    assert listed[0]["realism_override"]["failing"][0]["rule"] == "their_ranking_value"
    assert props.get(gm.conn, out["ref"]).realism_override["override_reason"] == "Partner asked for a2 in chat"


def test_blank_override_reason_does_not_override(gm):
    gm.ev = make_ev(["a1"], ["b1"], them_value=-3.0, them_cats=0.2)
    with pytest.raises(svc.GMError):
        propose(override_reason="   ")


def test_demo_mode_skips_the_ht_rule(gm):
    gm.hashtag = False
    gm.ros = pd.DataFrame(columns=["total"])
    gm.ev = make_ev(["a1"], ["b1"], them_value=0.0, them_cats=0.2)
    out = propose()
    ht = next(r for r in out["evidence"]["trade_realism"]["rules"] if r["rule"] == "ht_value_premium")
    assert ht["value"] is None and ht["ok"] and "skipped" in ht["note"]
    gm.ev = make_ev(["a1", "a2"], ["c1"], them_value=-2.0, them_cats=0.2)  # the other rules still apply in demo mode
    with pytest.raises(svc.GMError, match="-2.00"):
        propose(payload={"partner": "t3", "give": ["a1", "a2"], "get": ["c1"]})


def test_player_missing_from_ht_skips_the_ht_rule(gm):
    gm.ros = gm.ros.drop(index=["b1"])
    gm.ev = make_ev(["a1"], ["b1"], them_value=0.0, them_cats=0.2)
    out = propose()
    ht = next(r for r in out["evidence"]["trade_realism"]["rules"] if r["rule"] == "ht_value_premium")
    assert ht["value"] is None and "no HT ROS value for b1" in ht["note"]


def test_other_kinds_are_not_screened(gm):
    gm.ev = None  # would blow up if the trade screen ran
    out = svc.create_proposal("strategy", "Build", "s", {"punts": ["FT%"], "targets": []}, "punt_scenarios says so")
    assert "trade_realism" not in (out["evidence"] or {})


def test_payload_must_be_a_real_trade(gm):
    with pytest.raises(svc.GMError, match="at least one player"):
        propose(payload={"partner": "t2", "give": [], "get": ["b1"]})
    with pytest.raises(svc.GMError, match="Partner must be another team"):
        propose(payload={"partner": "t1", "give": ["a1"], "get": ["a2"]})
    with pytest.raises(svc.GMError):  # a player the partner does not own
        propose(payload={"partner": "t2", "give": ["a1"], "get": ["c1"]})


def test_real_engine_numbers_gate_the_payload(monkeypatch, gm):
    """No stub: the gate re-runs evaluate_trade on the payload's ids (lopsided: our scrub for their star)."""
    sim = gm.sim
    conn = gm.conn
    monkeypatch.setattr(svc, "_evaluate_trade_ev", REAL_EVALUATE)
    monkeypatch.setattr(svc, "_conn", lambda: conn)
    monkeypatch.setattr(svc, "active_league", lambda: sim.league)
    monkeypatch.setattr(svc, "ctx", lambda: SimpleNamespace(hashtag=False, projections=pd.DataFrame(columns=["name"])))
    monkeypatch.setattr(svc._ht, "gate", lambda conn=None: None)
    monkeypatch.setattr(svc, "_league_sim", lambda n=1500: (sim, ["f1"]))
    monkeypatch.setattr(svc, "_manager_profiles", lambda league, rates, labels=False: {})
    monkeypatch.setattr(svc, "_partner_build", lambda p: PartnerBuild())
    monkeypatch.setattr(svc, "_strategy_punts", lambda x: ())
    monkeypatch.setattr(memory, "journal_append", lambda *a, **k: None)
    ev = evaluate_trade(sim, "t1", "t3", ["a2"], ["c1"], ["f1"])
    assert ev.them.judged_value_delta < -MAX_LOSS
    with pytest.raises(svc.GMError) as err:
        propose(payload={"partner": "t3", "give": ["a2"], "get": ["c1"]})
    assert f"{ev.them.judged_value_delta:+.2f}" in str(err.value)


def test_evaluate_and_find_tools_show_the_screen(ht_mode):  # noqa: F811
    out = svc.find_trades_tool(max_give=1, max_get=1, limit=10)
    assert out["trades"]
    for t in out["trades"]:
        assert set(t["realism"]) == {"ok", "failing"} and isinstance(t["realism"]["ok"], bool)
