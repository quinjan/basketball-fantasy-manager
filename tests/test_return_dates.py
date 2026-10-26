"""Out players with an ESPN return date: they miss games before it and play from it (week sims and lineup planning)."""

from dataclasses import replace
from datetime import date

import numpy as np
import pandas as pd
import pytest

from fbm.analytics.bank import DrawBank
from fbm.analytics.categories import NINE_CAT
from fbm.analytics.il_activation import phase_games
from fbm.analytics.lineup import as_of
from fbm.analytics.simulation import COL, STATS, PlayerModel, TeamWeek, build_player_models, simulate_totals
from fbm.analytics.streaming import StreamContext, evaluate
from fbm.analytics.week import plan_team_week
from fbm.constants import C
from fbm.league.model import FantasyTeam, RosterPlayer
from fbm.store import db

D1, D2, D3 = date(2026, 10, 26), date(2026, 10, 27), date(2026, 10, 28)
PLAYING = {D1: {"PHI", "BOS"}, D2: {"PHI", "BOS"}, D3: {"PHI", "BOS"}}


def line(**kw) -> np.ndarray:
    row = np.zeros(len(STATS))
    for k, v in kw.items():
        row[COL[k]] = v
    return row


def rp(pid, team, status="", selected="BN"):
    return RosterPlayer(pid, pid, team, ("C", "UTIL"), selected, status)


def test_play_prob_switches_on_return_date():
    m = PlayerModel("a", line(pts=10)[None], play_prob=0.0, return_date=D2, return_play_prob=0.6)
    assert m.game_play_prob(D1) == 0.0
    assert m.game_play_prob(D2) == pytest.approx(0.6) and m.game_play_prob(D3) == pytest.approx(0.6)
    assert m.game_play_prob() == 0.0 and m.game_play_prob(("generic", 0)) == 0.0  # no date: today's status
    rested = replace(m, rest_prob=0.5, b2b_dates=frozenset({D3}))
    assert rested.game_play_prob(D3) == pytest.approx(0.3)
    week = TeamWeek("t", ["a", "a", "a"], game_dates=[D1, D2, D3])
    tot = simulate_totals(week, {"a": m}, 20000, np.random.default_rng(4))
    assert tot[:, COL["pts"]].mean() == pytest.approx(10 * (0 + 0.6 + 0.6), rel=0.03)
    assert DrawBank({"a": m}, n_sims=4000).game("a", D1).sum() == 0


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def test_build_models_applies_return_date_only_to_out_players(conn):
    statuses = {"out": "O", "healthy": "", "dtd": "DTD", "susp": "SUSP"}
    back = {"out": D2, "healthy": D2, "dtd": D2}
    models = build_player_models(conn, list(statuses), [2026], pd.DataFrame(), statuses, return_dates=back)
    assert models["out"].return_date == D2
    assert models["out"].return_play_prob == pytest.approx(float(C("sim.play_prob.gtd")))
    assert models["out"].game_play_prob(D1) == 0.0 and models["out"].game_play_prob(D2) > 0
    assert models["healthy"].return_date is None and models["dtd"].return_date is None
    assert models["susp"].return_date is None and models["susp"].game_play_prob(D3) == 0.0  # no return date
    season = build_player_models(conn, ["out"], [2026], None, statuses, play_probs={"out": 0.4}, return_dates=back)
    assert season["out"].return_date is None and season["out"].game_play_prob(D1) == pytest.approx(0.4)


def test_as_of_clears_status_from_return_day_without_mutating():
    players = [rp("out", "PHI", "O"), rp("inj", "PHI", "INJ"), rp("ok", "BOS")]
    back = {"out": D2}
    assert [p.status for p in as_of(players, D1, back)] == ["O", "INJ", ""]
    assert [p.status for p in as_of(players, D2, back)] == ["", "INJ", ""]
    assert players[0].status == "O" and as_of(players, D2, None) == players


def test_week_plan_starts_out_player_from_return_date():
    team = FantasyTeam("t", "T", players=[rp("embiid", "PHI", "O"), rp("bench", "BOS")])
    value = {"embiid": 3.0, "bench": -1.0}
    week, _ = plan_team_week(team, [D1, D2, D3], PLAYING, ["C"], value)
    assert set(week.player_games) == {"bench"}  # Out all week without a return date
    week, plans = plan_team_week(team, [D1, D2, D3], PLAYING, ["C"], value, {"embiid": D3})
    assert list(zip(week.player_games, week.game_dates, strict=True)) == [("bench", D1), ("bench", D2), ("embiid", D3)]
    assert plans[2].started == ("embiid",)
    assert team.players[0].status == "O"


def test_streaming_and_il_phases_honor_return_dates():
    star, role = line(pts=30, fgm=10, fga=20), line(pts=10, fgm=4, fga=8)
    models = {
        "out": PlayerModel("out", star[None], play_prob=0.0, return_date=D2, return_play_prob=1.0),
        "ok": PlayerModel("ok", role[None]),
    }
    pool = {"out": rp("out", "PHI", "O", "C"), "ok": rp("ok", "BOS")}
    base = dict(
        base=list(pool.values()),
        pool=pool,
        days=[D1, D2, D3],
        playing=PLAYING,
        starting_slots=["C"],
        capacity=2,
        value={"out": 3.0, "ok": 1.0},
        bank=DrawBank(models, n_sims=200),
        opp_totals=np.tile(role * 3, (200, 1)),
        categories=NINE_CAT,
        weights={c.key: 1.0 for c in NINE_CAT},
    )

    def pts(odds):
        return next(o.mean_a for o in odds.categories if o.category.label == "PTS")

    without, _ = evaluate(StreamContext(**base), [])
    with_return, _ = evaluate(StreamContext(**base, return_dates={"out": D2}), [])
    assert pts(without) == pytest.approx(30.0)  # "ok" starts all 3 days
    assert pts(with_return) == pytest.approx(10.0 + 30.0 + 30.0)  # the star takes the slot from his return
    phases = [(date.min, list(pool.values()))]
    args = ([D1, D2, D3], PLAYING, ["C"], {"out": 3.0, "ok": 1.0})
    assert phase_games(phases, *args) == [("ok", D1), ("ok", D2), ("ok", D3)]
    assert phase_games(phases, *args, None, {"out": D2}) == [("ok", D1), ("out", D2), ("out", D3)]
