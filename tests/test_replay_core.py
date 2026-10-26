from datetime import date

import pytest

from fbm.analytics.categories import NINE_CAT
from fbm.gm import decisions as dlog
from fbm.gm import odds_reliability, outcomes, replay
from fbm.store import db
from fbm.yahoo import lineups

from .test_outcomes import POOL, add_game, add_line, odds

WEEK = ("2026-10-19", "2026-10-25")
DAY = "2026-10-20"
TODAY = date(2026, 10, 27)
ME = {"pts": 500, "reb": 200, "ast": 100, "stl": 30, "blk": 20, "tov": 60, "tpm": 50, "fgm": 190, "fga": 400,
      "ftm": 80, "fta": 100}  # fmt: skip
OPP = {"pts": 505, "reb": 210, "ast": 90, "stl": 30, "blk": 25, "tov": 50, "tpm": 40, "fgm": 185, "fga": 400,
       "ftm": 70, "fta": 100}  # fmt: skip


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def winners(me, opp):
    res, _ = replay.cat_results(me, opp, NINE_CAT)
    return res


def setup_week(conn, final=True):
    lineups.store_final(
        conn,
        week=1,
        team_key="me",
        opponent_key="op",
        week_start=WEEK[0],
        week_end=WEEK[1],
        me=ME,
        opp=OPP,
        stat_winners=winners(ME, OPP),
        status="postevent" if final else "midevent",
    )
    add_game(conn, "g1", DAY)
    for d in ("2026-10-19", DAY, "2026-10-21", "2026-10-22", "2026-10-23", "2026-10-24", "2026-10-25"):
        lineups.store_lineup(conn, "me", d, [("p.a", "a", "PG"), ("p.b", "b", "SG"), ("p.x", "x", "BN")])
    add_line(conn, "g1", "a", DAY, pts=10, fgm=4, fga=10)
    add_line(conn, "g1", "b", DAY, pts=2, reb=1, fgm=1, fga=5)
    add_line(conn, "g1", "x", DAY, pts=20, reb=12, ast=5, blk=6, fgm=8, fga=10)


def test_lineup_replay_and_regret(conn):
    setup_week(conn)
    opts = [
        {"id": "rec", "label": "rec", "spec": {"date": DAY, "starters": ["a", "b"]}},
        {"id": "alt", "label": "alt", "spec": {"date": DAY, "starters": ["a", "x"]}},
    ]
    did = dlog.log(
        conn, kind="lineup", trigger="daily-gm", et_date=DAY, situation={"team_key": "me"}, options=opts,
        recommended="rec",
    )  # fmt: skip
    d = dlog.get(conn, did)
    rec = replay.replay_option(conn, d, opts[0], today=TODAY)
    alt = replay.replay_option(conn, d, opts[1], today=TODAY)
    assert rec["complete"] and rec["cat_results"] == winners(ME, OPP)
    assert alt["cats_won"] > rec["cats_won"]  # x's rebounds/blocks flip REB and BLK
    assert alt["cat_results"]["reb"] == 1.0 and alt["cat_results"]["blk"] == 1.0
    out = replay.grade_pending(conn, TODAY)
    assert out[0]["chosen"] == "rec" and out[0]["best"] == "alt" and out[0]["regret_cats"] > 0
    saved = dlog.get(conn, did)
    assert saved["chosen_source"] == "yahoo_lineup" and saved["result"]["complete"]


def test_incomplete_until_final(conn):
    setup_week(conn, final=False)
    opt = {"id": "rec", "label": "r", "spec": {"date": DAY, "starters": ["a", "b"]}}
    did = dlog.log(conn, kind="lineup", trigger="daily-gm", et_date=DAY, situation={"team_key": "me"}, options=[opt])
    r = replay.replay_option(conn, dlog.get(conn, did), opt, today=TODAY)
    assert r["complete"] is False and "cats_won" in r
    assert replay.grade_pending(conn, TODAY)[0]["complete"] is False
    assert dlog.get(conn, did)["graded_at"] is None


def test_stream_undo_chosen_and_ros(conn):
    setup_week(conn)
    stand = {"id": "stand", "label": "s", "stand_pat": True, "spec": {"moves": []}}
    add = {"id": "add", "label": "add y", "spec": {"moves": [{"add": "y", "drop": "b", "date": DAY}]}}
    add_line(conn, "g1", "y", DAY, pts=30, stl=5, fgm=10, fga=15)
    did = dlog.log(
        conn, kind="stream_plan", trigger="daily-gm", et_date=DAY, situation={"team_key": "me"},
        options=[stand, add], recommended="stand",
    )  # fmt: skip
    d = dlog.get(conn, did)
    r_add = replay.replay_option(conn, d, add, pool=POOL, today=TODAY)
    r_stand = replay.replay_option(conn, d, stand, pool=POOL, today=TODAY)
    assert r_add["cat_results"]["pts"] == 1.0 and r_stand["cat_results"]["pts"] == 0.0
    assert r_add["ros_value"] > 0 and r_stand["ros_value"] == 0.0
    # if the add actually happened, standing pat is replayed by undoing it
    dlog.set_chosen(conn, did, "add", "owner")
    d = dlog.get(conn, did)
    assert (
        replay.replay_option(conn, d, stand, pool=POOL, today=TODAY)["cat_margins"]["pts"] < r_add["cat_margins"]["pts"]
    )


def test_chase_concede_carries_cat_results(conn):
    setup_week(conn)
    opt = {"id": "plan", "label": "p", "spec": {"status": {"pts": "chase", "reb": "concede"}}}
    did = dlog.log(
        conn, kind="chase_concede", trigger="weekly-plan", et_date=DAY, situation={"team_key": "me"}, options=[opt]
    )
    res = replay.grade_decision(conn, dlog.get(conn, did), today=TODAY)
    assert res["cat_results"] == winners(ME, OPP)
    assert res["options"]["plan"]["status_calls"] == {"pts": False, "reb": True}


def ht_payload(p, view="STREAM7"):
    cats = {c.key: {"p_win": p, "p_tie": 0.0} for c in NINE_CAT}
    means = {c.key: {"me": 1.0, "opp": 0.0} for c in NINE_CAT}
    sd = {c.key: 1.0 for c in NINE_CAT}
    return {"categories": cats, "expected": 9 * p, "p_majority": p, "view": view, "means": means, "sd": sd}


def insert_pred(conn, source, payload, start="2026-10-21"):
    import json

    conn.execute(
        "INSERT INTO predictions(created_at, kind, window_start, window_end, team_key, opponent_key, source, payload) "
        "VALUES ('x','matchup',?,?,'me','op',?,?)",
        (start, WEEK[1], source, json.dumps(payload)),
    )


def test_odds_reliability_against_finals(conn):
    setup_week(conn)
    insert_pred(conn, "ht_spread", ht_payload(0.6))
    r = odds_reliability.report(conn)
    assert r["n_weeks"] == 1 and r["n_forecasts"] == 9 and r["brier"] is not None
    actual = sum(winners(ME, OPP).values())
    assert r["mean_actual_cats"] == actual
    # recomputing with another scale uses stored means/sd (me ahead in every cat incl. TO -> TO odds < 0.5)
    r2 = odds_reliability.report(conn, scale=1.0)
    assert r2["brier"] != r["brier"]
    assert odds_reliability.best_scale(conn, [0.5, 2.0])["best"] in (0.5, 2.0)
    assert odds_reliability.report(conn, view="ROS")["n_forecasts"] == 0


def test_midweek_forecast_graded_on_yahoo_final(conn):
    """Bug 2026-10-26: mid-week forecasts were graded on the remaining days' planned player-days only."""
    setup_week(conn)
    pid = outcomes.record_prediction(
        conn, odds(0.5), (date(2026, 10, 21), date(2026, 10, 25)), "me", "op", "yahoo", [("x", date(2026, 10, 20))], []
    )
    (g,) = outcomes.grade_predictions(conn, NINE_CAT, TODAY)
    assert g["id"] == pid and g["actual_source"] == "yahoo_final"
    assert g["actual_cats"] == sum(winners(ME, OPP).values())


def test_yahoo_prediction_waits_for_final(conn):
    setup_week(conn, final=False)
    outcomes.record_prediction(conn, odds(0.5), (date(2026, 10, 21), date(2026, 10, 25)), "me", "op", "yahoo", [], [])
    assert outcomes.grade_predictions(conn, NINE_CAT, TODAY) == []


def test_regrade_legacy(conn):
    import json

    setup_week(conn)
    insert_pred(conn, "yahoo", ht_payload(0.5))
    conn.execute("UPDATE predictions SET graded_at='x', result=?", (json.dumps({"actual_cats": 0, "brier_mean": 1}),))
    out = replay.regrade_legacy_predictions(conn)
    assert out["regraded"] == 1
    res = json.loads(conn.execute("SELECT result FROM predictions").fetchone()[0])
    assert res["legacy"]["actual_cats"] == 0 and res["actual_cats"] == sum(winners(ME, OPP).values())
    assert replay.regrade_legacy_predictions(conn)["regraded"] == 0


def test_weekly_retro_per_view(conn):
    setup_week(conn)
    opts = [
        {"id": "s7", "label": "s7", "view": "STREAM7", "spec": {"date": DAY, "starters": ["a", "b"]},
         "cat_scores": {"pts": 1}},
        {"id": "ros", "label": "ros", "view": "ROS", "spec": {"date": DAY, "starters": ["a", "x"]},
         "cat_scores": {"pts": 1}},
    ]  # fmt: skip
    dlog.log(conn, kind="lineup", trigger="daily-gm", et_date=DAY, situation={"team_key": "me"}, options=opts,
             recommended="s7")  # fmt: skip
    data = outcomes.weekly_retro_data(conn, POOL, NINE_CAT, date(2026, 10, 19), TODAY)
    pv = data["per_view"]["lineup"]
    assert pv["best_view"] == "ROS" and pv["views"]["ROS"]["cats_won"] > pv["views"]["STREAM7"]["cats_won"]
    assert data["regret_by_kind"]["lineup"]["graded"] == 1
    assert data["odds_reliability"]["n_forecasts"] == 0


def test_forecast_against_wrong_opponent_is_skipped(conn):
    import json

    setup_week(conn)
    outcomes.record_prediction(conn, odds(0.5), (date(2026, 10, 20), date(2026, 10, 25)), "me", "zz", "yahoo", [], [])
    assert outcomes.grade_predictions(conn, NINE_CAT, TODAY) == []
    res = json.loads(conn.execute("SELECT result FROM predictions").fetchone()[0])
    assert res["skipped"].startswith("forecast opponent")
    assert not outcomes.calibration(conn).get("category_forecasts")
