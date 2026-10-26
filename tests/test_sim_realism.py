"""Simulator realism fixes from the week-1 audit: projection anchoring, weekly form shock, back-to-back rest."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from fbm.analytics.bank import DrawBank
from fbm.analytics.rest import b2b_rest_probs, team_b2b_dates
from fbm.analytics.simulation import (
    COL,
    STATS,
    PlayerModel,
    TeamWeek,
    anchor_samples,
    build_player_models,
    form_multiplier,
    simulate_totals,
)
from fbm.constants import C
from fbm.store import db


def line(**kw) -> np.ndarray:
    row = np.zeros(len(STATS))
    for k, v in kw.items():
        row[COL[k]] = v
    return row


LINES = np.array(
    [line(pts=20, reb=8, fgm=8, fga=16, ftm=4, fta=5, tpm=0), line(pts=10, reb=4, fgm=4, fga=8, ftm=2, fta=2)]
)


def test_anchor_moves_means_to_projection_and_keeps_shape():
    target = pd.Series({"pts": 12.0, "reb": 6.0, "fgm": 5.4, "fga": 12.0})
    out = anchor_samples(LINES, target, 1.0, 0.5, 2.0)
    assert out[:, COL["pts"]].mean() == pytest.approx(12.0)
    assert out[:, COL["reb"]].mean() == pytest.approx(6.0)
    assert out[:, COL["fgm"]].sum() / out[:, COL["fga"]].sum() == pytest.approx(0.45)  # FG% follows projection
    assert out[0, COL["pts"]] / out[1, COL["pts"]] == pytest.approx(2.0)  # game-to-game shape kept
    assert out[:, COL["ftm"]].mean() == pytest.approx(LINES[:, COL["ftm"]].mean())  # no target -> unchanged
    half = anchor_samples(LINES, target, 0.5, 0.5, 2.0)
    assert half[:, COL["pts"]].mean() == pytest.approx(13.5)
    capped = anchor_samples(LINES, pd.Series({"pts": 100.0}), 1.0, 0.5, 2.0)
    assert capped[:, COL["pts"]].mean() == pytest.approx(30.0)
    assert anchor_samples(LINES, target, 0.0, 0.5, 2.0) is LINES


def test_form_multiplier_mean_one_with_requested_spread():
    m = form_multiplier(np.random.default_rng(0), 200_000, 0.09)
    assert m.mean() == pytest.approx(1.0, abs=0.002) and m.std() == pytest.approx(0.09, abs=0.003)
    assert (form_multiplier(np.random.default_rng(0), 5, 0.0) == 1.0).all()


def test_form_shock_widens_weekly_totals_and_keeps_percentages():
    flat = PlayerModel("a", np.array([line(pts=20, fgm=8, fga=16)]))
    shocked = PlayerModel("a", flat.samples, form_sd=0.2)
    week = TeamWeek("t", ["a"] * 4)
    base = simulate_totals(week, {"a": flat}, 5000, np.random.default_rng(1))
    wide = simulate_totals(week, {"a": shocked}, 5000, np.random.default_rng(1))
    assert base[:, COL["pts"]].std() == 0.0
    assert wide[:, COL["pts"]].std() == pytest.approx(80 * 0.2, rel=0.1)  # one factor shared by all 4 games
    assert np.allclose(wide[:, COL["fgm"]] / wide[:, COL["fga"]], 0.5)


def test_back_to_back_rest_only_on_b2b_dates():
    d1, d2, d3 = date(2026, 10, 24), date(2026, 10, 25), date(2026, 10, 27)
    m = PlayerModel("a", np.array([line(pts=10)]), rest_prob=0.25, b2b_dates=frozenset({d1, d2}))
    assert m.game_play_prob(d1) == pytest.approx(0.75) and m.game_play_prob(d3) == 1.0 and m.game_play_prob() == 1.0
    week = TeamWeek("t", ["a", "a", "a"], game_dates=[d1, d2, d3])
    tot = simulate_totals(week, {"a": m}, 20000, np.random.default_rng(2))
    assert tot[:, COL["pts"]].mean() == pytest.approx(10 * (0.75 + 0.75 + 1.0), rel=0.02)
    bank = DrawBank({"a": m}, n_sims=20000, seed=3)
    assert bank.total([("a", d1), ("a", d3)])[:, COL["pts"]].mean() == pytest.approx(17.5, rel=0.02)


def test_bank_form_shared_across_games_and_candidates():
    m = PlayerModel("a", np.array([line(pts=10)]), form_sd=0.2)
    bank = DrawBank({"a": m, "b": PlayerModel("b", np.array([line(pts=5)]))}, n_sims=4000, seed=5)
    g1, g2 = bank.game("a", date(2026, 10, 20)), bank.game("a", date(2026, 10, 22))
    assert np.allclose(g1, g2)  # deterministic lines -> identical games means one shared weekly factor
    assert np.allclose(bank.total([("a", date(2026, 10, 20))]), bank.total([("a", date(2026, 10, 20))]))
    assert (bank.game("b", date(2026, 10, 20))[:, COL["pts"]] == 5).all()  # form_sd 0 -> untouched


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def _game(conn, eid, d, home, away, season=2026, completed=1):
    conn.execute(
        "INSERT INTO games(event_id, season, season_type, start_utc, et_date, home, away, state, completed, neutral, "
        "time_valid, updated_at) VALUES (?, ?, 2, ?, ?, ?, ?, 'post', ?, 0, 1, '')",
        (eid, season, f"{d}T00:00:00Z", d, home, away, completed),
    )


def _line(conn, eid, pid, d, team, season=2026):
    conn.execute(
        "INSERT INTO box_lines(event_id, espn_id, et_date, season, team, starter, played, minutes, pts) "
        "VALUES (?, ?, ?, ?, ?, 1, 1, 30, 10)",
        (eid, pid, d, season, team),
    )


def test_rest_rates_from_history(conn):
    # PHI plays 10 back-to-backs (Nov 1-2, 4-5, ...): "rester" sits leg 2 of every one, "iron" plays all.
    for i in range(10):
        d1, d2 = f"2025-11-{3 * i + 1:02d}", f"2025-11-{3 * i + 2:02d}"
        _game(conn, f"a{i}", d1, "PHI", "BOS")
        _game(conn, f"b{i}", d2, "PHI", "NY")
        for pid in ("rester", "iron"):
            _line(conn, f"a{i}", pid, d1, "PHI")
        _line(conn, f"b{i}", "iron", d2, "PHI")
    conn.commit()
    rates = b2b_rest_probs(conn, [2026])
    base, prior = float(C("sim.b2b_rest_baseline")), float(C("sim.b2b_rest_prior_pairs"))
    assert rates["rester"] == pytest.approx((10 - 10 * base) / (10 + prior) / 2)
    assert "iron" not in rates
    assert date(2025, 11, 1) in team_b2b_dates(conn, 2026)["PHI"]
    assert date(2025, 11, 3) not in team_b2b_dates(conn, 2026).get("BOS", frozenset())


def test_build_player_models_anchors_and_sets_rest(conn):
    for i in range(10):
        d = f"2025-12-{i + 1:02d}"
        _game(conn, f"g{i}", d, "PHI", "BOS")
        _line(conn, f"g{i}", "p", d, "PHI")
    conn.commit()
    proj = pd.DataFrame({"pts": [15.0], "fgm": [0.0], "fga": [0.0]}, index=["p"])
    models = build_player_models(
        conn, ["p"], [2026], proj, rest_probs={"p": 0.2}, b2b_dates={"p": frozenset({date(2025, 12, 1)})}
    )
    m = models["p"]
    assert m.samples[:, COL["pts"]].mean() == pytest.approx(15.0) and not m.synthetic
    assert m.form_sd == pytest.approx(float(C("sim.form_sd"))) and m.rest_prob == 0.2
    assert m.game_play_prob(date(2025, 12, 1)) == pytest.approx(0.8)
