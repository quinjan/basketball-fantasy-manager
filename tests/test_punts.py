import numpy as np
import pandas as pd
import pytest

from fbm.analytics.categories import NINE_CAT
from fbm.analytics.punts import (
    Swap,
    apply_swaps,
    candidate_punts,
    evaluate_scenarios,
    lens_values,
    market_edges,
    odds_vs_league,
    plan_swaps,
)
from fbm.analytics.simulation import COL, STATS

KEYS = [c.key for c in NINE_CAT]


def test_candidate_punts_singles_and_weak_pairs():
    rates = dict(zip(KEYS, np.linspace(0.1, 0.9, len(KEYS)), strict=True))
    out = candidate_punts(rates, max_size=2, pair_pool=4)
    assert out[0] == ()
    assert out[1] == (KEYS[0],)  # weakest first
    assert len(out) == 1 + 9 + 6
    pairs = [p for p in out if len(p) == 2]
    assert all(set(p) <= set(KEYS[:4]) for p in pairs) and all(list(p) == sorted(p) for p in pairs)


def test_lens_values_drop_punted_columns():
    df = pd.DataFrame({f"g_{k}": [1.0, 2.0] for k in KEYS}, index=["a", "b"])
    assert lens_values(df, KEYS, ()).tolist() == [9.0, 18.0]
    assert lens_values(df, KEYS, ("tpm", "ft_pct")).tolist() == [7.0, 14.0]


def test_plan_swaps_lens_proposes_simulation_confirms():
    value = {"r1": 0.0, "r2": 1.0, "fa_top": 3.0, "fa_good": 2.0, "fa_noise": 0.1}
    helps = {"fa_good": 0.3, "fa_top": -0.2}  # the lens's favourite would hurt the real score

    def score(roster):
        return sum(helps.get(pid, 0.0) for pid in roster)

    swaps, conflicts = plan_swaps(["r1", "r2"], ["fa_top", "fa_good", "fa_noise"], value, 2, 0.25, score, 0.02)
    assert [(s.add, s.drop) for s in swaps] == [("fa_good", "r1")]
    assert swaps[0].sim_delta == pytest.approx(0.3)
    assert conflicts and (conflicts[0].add, conflicts[0].drop) == ("fa_top", "r1")
    assert conflicts[0].sim_delta == pytest.approx(-0.2)
    assert apply_swaps(["r1", "r2"], swaps) == ["r2", "fa_good"]


def test_plan_swaps_respects_min_gain_and_budget():
    value = {"r1": 1.0, "fa": 1.1}
    swaps, _ = plan_swaps(["r1"], ["fa"], value, 3, 0.25, lambda r: float("fa" in r), 0.02)
    assert swaps == []  # lens gain 0.1 < min_gain
    swaps, _ = plan_swaps(["r1"], ["fa"], value, 0, 0.0, lambda r: float("fa" in r), 0.02)
    assert swaps == []  # no budget


def test_market_edges():
    idx = [f"p{i}" for i in range(40)]
    neutral = pd.Series(np.arange(40, 0, -1, dtype=float), index=idx)  # p0 is the market's best
    lens = neutral.copy()
    lens["p30"] = 100.0  # the lens loves an undervalued player on another team
    lens["p1"] = -100.0  # and hates one of mine
    owners = {"p30": "t2", "p1": "me", "p2": "me"}
    sell, buy = market_edges(neutral, lens, owners, "me", top_pool=40)
    assert [e.player_id for e in buy] == ["p30"] and buy[0].lens_rank == 1
    assert [e.player_id for e in sell] == ["p1"] and sell[0].lens_rank == 40


def _line(**kw) -> np.ndarray:
    row = np.zeros(len(STATS))
    for k, v in kw.items():
        row[COL[k]] = v
    return row


def test_evaluate_scenarios_only_keeps_helpful_swaps():
    rng = np.random.default_rng(3)
    base = {
        "g1": _line(pts=20, ast=8, tpm=3, stl=1, reb=4, blk=0, tov=3, fgm=7, fga=16, ftm=4, fta=5),
        "g2": _line(pts=8, ast=2, tpm=1, stl=1, reb=2, blk=0, tov=1, fgm=3, fga=8, ftm=1, fta=2),
        "o1": _line(pts=15, ast=4, tpm=2, stl=1, reb=7, blk=1, tov=2, fgm=6, fga=12, ftm=2, fta=3),
        "o2": _line(pts=14, ast=5, tpm=2, stl=1, reb=6, blk=1, tov=2, fgm=5, fga=11, ftm=3, fta=4),
        "fa_big": _line(pts=10, ast=1, tpm=0, stl=1, reb=11, blk=2, tov=1, fgm=4, fga=6, ftm=1, fta=3),
        "fa_wing": _line(pts=12, ast=2, tpm=3, stl=1, reb=3, blk=0, tov=1, fgm=4, fga=10, ftm=2, fta=2),
    }
    draws = {pid: row + rng.normal(0, 1.5, (400, len(STATS))).clip(-row, None) for pid, row in base.items()}
    totals = {pid: np.abs(d) for pid, d in draws.items()}

    def team_totals(ids):
        return sum((totals[pid] for pid in ids), np.zeros((400, len(STATS))))

    values = pd.DataFrame(rng.normal(0, 1, (len(base), len(KEYS))), index=list(base), columns=[f"g_{k}" for k in KEYS])
    active = {"me": ["g1", "g2"], "t1": ["o1"], "t2": ["o2"]}
    others = {k: team_totals(v) for k, v in active.items() if k != "me"}
    results = evaluate_scenarios(
        [(), ("tpm",), ("ft_pct", "tpm")],
        values=values,
        category_keys=KEYS,
        categories=NINE_CAT,
        my_team_key="me",
        active=active,
        free_agents=["fa_big", "fa_wing"],
        team_totals=team_totals,
        others_totals=others,
        fa_moves=2,
        min_gain=0.0,
        reliable=0.6,
    )
    _, baseline, _ = odds_vs_league(team_totals(active["me"]), others, NINE_CAT)
    assert [r.punts for r in results] == [(), ("tpm",), ("ft_pct", "tpm")]
    for r in results:
        assert r.expected_cats >= baseline - 1e-9
        assert r.expected_cats >= baseline + 0.02 * len(r.swaps) - 1e-9
        assert all(isinstance(s, Swap) and s.sim_delta >= 0.02 for s in r.swaps)
        assert 1 <= r.value_rank <= 3
        assert set(r.cat_rates) == set(KEYS)
    assert results[0].sell == [] and results[0].buy == []  # no market edges without a punt
