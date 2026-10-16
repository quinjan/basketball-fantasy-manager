from datetime import UTC, date, datetime

import pytest

from fbm.analytics.categories import NINE_CAT
from fbm.analytics.simulation import CategoryOdds, MatchupOdds
from fbm.analytics.valuation import PoolStats
from fbm.constants import C
from fbm.gm import outcomes
from fbm.gm import proposals as props
from fbm.store import db

POOL = PoolStats(
    mean={c.key: 0.0 for c in NINE_CAT},
    sd={
        "fg_pct": 0.5,
        "ft_pct": 0.3,
        "tpm": 1.0,
        "pts": 6.0,
        "reb": 3.0,
        "ast": 2.0,
        "stl": 0.5,
        "blk": 0.5,
        "tov": 1.0,
    },
    tau2={},
    pool_pct={"fg_pct": 0.47, "ft_pct": 0.78},
    mean_att={"fg_pct": 12.0, "ft_pct": 4.0},
    pool_ids=[],
)
STAT_COLS = ("minutes", "pts", "fgm", "fga", "tpm", "tpa", "ftm", "fta", "reb", "ast", "tov", "stl", "blk")


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def add_game(conn, event, day, completed=1, synced=1):
    conn.execute(
        "INSERT INTO games(event_id, season, season_type, start_utc, et_date, home, away, state, completed, "
        "neutral, time_valid, box_synced, updated_at) VALUES (?,2027,2,'x',?,'H','A','post',?,0,1,?,'x')",
        (event, day, completed, synced),
    )


def add_line(conn, event, pid, day, **stats):
    vals = {s: stats.get(s, 0) for s in STAT_COLS}
    conn.execute(
        f"INSERT INTO box_lines(event_id, espn_id, et_date, season, team, starter, played, dnp_reason, "
        f"{', '.join(STAT_COLS)}) VALUES (?,?,?,2027,'H',1,1,'',{','.join('?' * len(STAT_COLS))})",
        (event, pid, day, *[vals[s] for s in STAT_COLS]),
    )


def odds(p_win: float) -> MatchupOdds:
    cats = tuple(CategoryOdds(c, p_win, 0.0, 1 - p_win, 0, 0) for c in NINE_CAT)
    return MatchupOdds(cats, 9 * p_win, p_win, tuple([0.0] * 10))


def test_prediction_graded_with_realized_box_scores(conn):
    add_game(conn, "g1", "2026-10-20")
    add_line(
        conn, "g1", "me1", "2026-10-20", pts=30, reb=10, ast=8, stl=2, blk=1, tov=2, tpm=3, fgm=10, fga=18, ftm=7, fta=8
    )
    add_line(
        conn, "g1", "op1", "2026-10-20", pts=10, reb=4, ast=2, stl=0, blk=0, tov=4, tpm=1, fgm=4, fga=12, ftm=1, fta=2
    )
    pid = outcomes.record_prediction(
        conn,
        odds(0.8),
        (date(2026, 10, 20), date(2026, 10, 20)),
        "t1",
        "t2",
        "demo",
        [("me1", date(2026, 10, 20))],
        [("op1", date(2026, 10, 20))],
    )
    assert outcomes.grade_predictions(conn, NINE_CAT, date(2026, 10, 20)) == []  # window not over yet
    (graded,) = outcomes.grade_predictions(conn, NINE_CAT, date(2026, 10, 21))
    assert graded["id"] == pid and graded["actual_cats"] == 9.0
    assert graded["brier_mean"] == pytest.approx(0.04)
    cal = outcomes.calibration(conn)
    assert cal["predictions"] == 1 and cal["brier"] == pytest.approx(0.04)
    assert cal["skill_vs_coinflip"] == pytest.approx(1 - 0.04 / 0.25)
    assert cal["reliability"] == [{"range": "0.8-1.0", "n": 9, "mean_forecast": 0.8, "observed": 1.0}]


def test_prediction_waits_for_unsynced_games(conn):
    add_game(conn, "g1", "2026-10-20", synced=0)
    outcomes.record_prediction(conn, odds(0.5), (date(2026, 10, 20), date(2026, 10, 20)), "t1", "t2", "demo", [], [])
    assert outcomes.grade_predictions(conn, NINE_CAT, date(2026, 10, 22)) == []


def test_contribution_scaling(conn):
    add_game(conn, "g1", "2026-10-20")
    add_line(conn, "g1", "p", "2026-10-20", pts=12, tov=2, fgm=6, fga=10, ftm=0, fta=0)
    c = outcomes.contribution(conn, "p", date(2026, 10, 20), date(2026, 10, 26), POOL, NINE_CAT)
    scale = 35**0.5
    assert c["games"] == 1
    assert c["std"]["PTS"] == pytest.approx(12 / 6 / scale, abs=1e-3)
    assert c["std"]["TO"] == pytest.approx(-2 / 1 / scale, abs=1e-3)
    assert c["std"]["FG%"] == pytest.approx((6 - 0.47 * 10) / 12 / 0.5 / scale, abs=1e-3)


def test_grade_executed_add_drop(conn):
    p = props.create(conn, "add_drop", "Add X", "s", {"add": "x", "drop": "y"}, "because", {"delta": 1})
    props.record_review(conn, p.ref, "AGREE", "ok", "gpt-5.6-sol")
    props.record_owner_decision(conn, p.ref, True, "yes")
    executed = datetime(2026, 10, 20, 16, 0, tzinfo=UTC).isoformat()  # 12:00 ET Oct 20
    conn.execute("UPDATE proposals SET status='executed', executed_at=? WHERE id=?", (executed, p.id))
    for i, day in enumerate(("2026-10-20", "2026-10-22", "2026-10-24")):
        add_game(conn, f"g{i}", day)
        add_line(conn, f"g{i}", "x", day, pts=22, reb=8, ast=4, stl=1, blk=1, tov=2, tpm=2, fgm=8, fga=15, ftm=4, fta=5)
    add_line(conn, "g0", "y", "2026-10-20", pts=4, reb=1, tov=1, fgm=2, fga=7)
    graded = outcomes.grade_decisions(
        conn, POOL, NINE_CAT, date(2026, 10, 26), lookback_days=10_000, names={"x": "X Player", "y": "Y Player"}
    )
    (g,) = graded
    assert g["ref"] == p.ref and g["executed_et"] == "2026-10-20" and g["window_et"] == "2026-10-20..2026-10-25"
    assert g["acquired"][0]["games"] == 3 and g["gave_up"][0]["games"] == 1
    assert g["delta"] > C("outcomes.good_threshold") and g["grade"] == "good"
    assert g["acquired"][0]["player"] == "X Player"


def test_grade_labels():
    assert outcomes._grade(0.1, 1, 2) == "too early"
    assert outcomes._grade(-0.9, 6, 7) == "bad"
    assert outcomes._grade(0.2, 6, 7) == "neutral"
