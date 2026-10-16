from datetime import date

import numpy as np
import pandas as pd
import pytest

from fbm.analytics.bank import DrawBank
from fbm.analytics.categories import NINE_CAT
from fbm.analytics.simulation import COL, STATS, PlayerModel
from fbm.analytics.streaming import InvalidPlan, Move, StreamContext, plan_streams, roster_by_day
from fbm.analytics.trades import LeagueSim, evaluate_trade, find_trades, need_weights, screen_trades, verdict_for
from fbm.league.model import FantasyTeam, LeagueState, RosterPlayer, SlotSpec


def line(**kw) -> np.ndarray:
    row = np.zeros(len(STATS))
    for k, v in kw.items():
        row[COL[k]] = v
    return row


GOOD = line(pts=20, reb=8, ast=5, stl=1, blk=1, tov=2, tpm=2, fgm=8, fga=16, ftm=2, fta=3)
MEH = line(pts=8, reb=3, ast=2, stl=0, blk=0, tov=1, tpm=1, fgm=3, fga=8, ftm=1, fta=2)
D1, D2, D3 = date(2026, 10, 20), date(2026, 10, 21), date(2026, 10, 22)
WEIGHTS = {c.key: 1.0 for c in NINE_CAT}


def rp(pid, team, selected="BN"):
    return RosterPlayer(pid, pid, team, ("PG", "UTIL"), selected)


def stream_ctx(n=200):
    models = {"A": PlayerModel("A", GOOD[None]), "B": PlayerModel("B", MEH[None]), "C": PlayerModel("C", GOOD[None])}
    bank = DrawBank(models, n_sims=n)
    pool = {"A": rp("A", "AAA", "PG"), "B": rp("B", "BBB", "UTIL"), "C": rp("C", "CCC")}
    playing = {D1: {"AAA", "BBB"}, D2: {"CCC"}, D3: {"CCC", "AAA"}}
    opp = np.tile(GOOD * 4, (n, 1))
    return StreamContext(
        base=[pool["A"], pool["B"]],
        pool=pool,
        days=[D1, D2, D3],
        playing=playing,
        starting_slots=["PG", "UTIL"],
        capacity=2,
        value={"A": 2.0, "B": -1.0, "C": 1.0},
        bank=bank,
        opp_totals=opp,
        categories=NINE_CAT,
        weights=WEIGHTS,
    )


def test_streamer_added_after_dropped_players_last_game():
    ctx = stream_ctx()
    plan = plan_streams(ctx, free_agents=["C"], droppable=["B"], adds_available=2)
    assert [pm.move for pm in plan.moves] == [Move(D2, "C", "B")]
    pm = plan.moves[0]
    assert pm.add_games == 2 and pm.drop_games_lost == 0 and pm.ros_cost == 0.0 and pm.gain > 0
    assert (plan.starts_before, plan.starts_after) == (3, 5)
    assert plan.final_score > plan.baseline_score


def test_no_adds_means_no_moves_and_full_roster_requires_drop():
    ctx = stream_ctx()
    assert plan_streams(ctx, ["C"], ["B"], adds_available=0).moves == []
    with pytest.raises(InvalidPlan):
        roster_by_day(ctx, [Move(D2, "C", None)])
    assert roster_by_day(ctx, [Move(D2, "C", "B")])[D3] == ("A", "C")


def test_draw_bank_common_random_numbers():
    bank = DrawBank({"X": PlayerModel("X", np.array([GOOD, MEH]))}, n_sims=500, seed=3)
    a = bank.game("X", D1)
    assert a is bank.game("X", D1)
    assert np.array_equal(a, DrawBank(bank.models, n_sims=500, seed=3).game("X", D1))
    assert not np.array_equal(a, bank.game("X", D2))
    assert bank.generic_week("X", 4).shape == (500, len(STATS))


CATS = [c.key for c in NINE_CAT]


def values_frame(rows: dict[str, dict[str, float]]) -> pd.DataFrame:
    df = pd.DataFrame({pid: {f"g_{k}": v.get(k, 0.0) for k in CATS} for pid, v in rows.items()}).T
    df["g_total"] = df[[f"g_{k}" for k in CATS]].sum(axis=1)
    return df


def trade_league():
    star, scrub = GOOD, MEH
    big = line(pts=12, reb=12, ast=1, stl=0.5, blk=3, tov=1.5, tpm=0, fgm=5, fga=8, ftm=2, fta=4)
    models = {
        "a1": PlayerModel("a1", star[None]),
        "a2": PlayerModel("a2", scrub[None]),
        "b1": PlayerModel("b1", big[None]),
        "b2": PlayerModel("b2", scrub[None]),
        "c1": PlayerModel("c1", star[None]),
        "c2": PlayerModel("c2", big[None]),
        "f1": PlayerModel("f1", scrub[None]),
    }
    values = values_frame(
        {
            "a1": {"pts": 1.5, "ast": 1.0, "tpm": 1.0},
            "a2": {"pts": -0.5},
            "b1": {"reb": 1.5, "blk": 2.0},
            "b2": {"pts": -0.6},
            "c1": {"pts": 1.5, "ast": 1.0},
            "c2": {"reb": 1.4, "blk": 1.8},
            "f1": {"pts": -0.4},
        }
    )
    teams = [
        FantasyTeam("t1", "Me", players=[rp("a1", "X", "PG"), rp("a2", "X", "UTIL")]),
        FantasyTeam("t2", "Bigs", players=[rp("b1", "X", "PG"), rp("b2", "X", "UTIL")]),
        FantasyTeam("t3", "Mixed", players=[rp("c1", "X", "PG"), rp("c2", "X", "UTIL")]),
    ]
    league = LeagueState("L", "Test", 2027, "t1", teams, slots=(SlotSpec("PG", 1, True), SlotSpec("UTIL", 1, True)))
    return LeagueSim(league, DrawBank(models, n_sims=300), values)


def test_evaluate_trade_rebalances_rosters():
    sim = trade_league()
    ev = evaluate_trade(sim, "t1", "t2", give=["a1", "a2"], get=["b1"], free_agents=["f1"])
    assert ev.me.fa_adds == ["f1"] and ev.me.drops == []
    assert ev.them.drops == ["b2"] and ev.them.fa_adds == []
    assert ev.me.expected_before == pytest.approx(sim.profile("t1")[1])
    assert ev.me.gives == ["a1", "a2"] and ev.them.gets == ["a1", "a2"]
    assert ev.verdict in {
        "not worth it for us",
        "win-win",
        "favors us (defensible)",
        "lopsided (unlikely to be accepted)",
    }


def test_lopsided_trade_detected_and_validation():
    sim = trade_league()
    ev = evaluate_trade(sim, "t1", "t3", give=["a2"], get=["c1"], free_agents=["f1"])
    assert ev.me.expected_delta > 0 > ev.them.expected_delta
    assert ev.verdict == "lopsided (unlikely to be accepted)"
    with pytest.raises(ValueError):
        evaluate_trade(sim, "t1", "t2", give=["b1"], get=["b2"], free_agents=[])


def test_screen_and_find_trades():
    sim = trade_league()
    cands = screen_trades(sim, "t1", (), ["f1"], max_give=1, max_get=1)
    assert all(c.screen_me > 0 for c in cands)
    results = find_trades(sim, "t1", ["f1"], max_give=1, max_get=1)
    assert all(r.me.expected_delta > 0.02 and r.them.expected_delta >= -0.05 for r in results)


def test_need_weights_and_verdicts():
    w = need_weights({"pts": 0.5, "reb": 0.95, "tov": 0.5}, punts=("tov",))
    assert w["pts"] == 1.0 and w["reb"] < 0.5 and w["tov"] == 0.0
    assert verdict_for(0.3, 0.1) == "win-win" and verdict_for(0.0, 0.5) == "not worth it for us"
