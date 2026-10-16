from datetime import date

import pandas as pd
import pytest

from fbm.analytics.availability import availability_share, load_availability, ros_adjust
from fbm.analytics.simulation import build_player_models
from fbm.store import db


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def test_healthy_and_season_out():
    assert availability_share("", 60).share == 1.0
    assert availability_share("OFS", 60).share == 0.0
    a = availability_share("O", 60, None, "He is expected to miss the 2026-27 season after a torn ACL.")
    assert a.share == 0.0 and "season" in a.reason
    assert availability_share("O", 60, None, "Out for the season (knee).").share == 0.0


def test_return_date_and_defaults():
    assert availability_share("O", 60, 15).share == pytest.approx(0.75)
    assert availability_share("O", 80, None).share == pytest.approx(1 - 8 / 80)
    assert availability_share("GTD", 80, 0).share == pytest.approx(1 - 1 / 80)
    assert availability_share("SUSP", 80).share == pytest.approx(1 - 3 / 80)
    assert availability_share("O", 5, 50).share == 0.0
    assert availability_share("O", 0, None).share == 1.0


def test_ros_adjust_moves_toward_replacement_only_above_it():
    values = pd.DataFrame(
        {"g_pts": [3.0, 1.0, 0.0, -1.0], "g_reb": [1.0, 1.0, 0.5, 0.0]}, index=["star", "mid", "repl", "scrub"]
    )
    values["g_total"] = values.sum(axis=1)
    out = ros_adjust(values, {"star": 0.5, "scrub": 0.0}, pool_size=3)
    assert out.at["star", "g_pts"] == pytest.approx(1.5) and out.at["star", "g_reb"] == pytest.approx(0.75)
    assert out.at["star", "g_total"] == pytest.approx(2.25)
    assert out.at["mid", "g_total"] == pytest.approx(2.0)
    assert out.at["scrub", "g_total"] == pytest.approx(-1.0)  # below replacement: unchanged
    assert ros_adjust(values, {"star": 0.0}, 3).at["star", "g_total"] == pytest.approx(0.5)


def test_load_availability_counts_team_games_before_return(conn):
    for i in range(10):
        d = date(2026, 10, 20 + i).isoformat()
        conn.execute(
            "INSERT INTO games(event_id, season, season_type, start_utc, et_date, home, away, state, completed,"
            " neutral, time_valid, updated_at) VALUES (?,2027,2,?,?,?,?,'pre',0,0,1,'x')",
            (f"g{i}", d, d, "AAA", "BBB" if i % 2 else "CCC"),
        )
    rows = [
        ("p1", "Out", "O", "2026-10-24", "Ankle sprain."),
        ("p2", "Out", "OFS", "2027-07-01", ""),
        ("p3", "Day-To-Day", "GTD", "2026-10-16", "Rest."),
    ]
    for pid, st, fs, rd, com in rows:
        conn.execute(
            "INSERT INTO injuries_current(espn_id, status, fantasy_status, return_date, short_comment, fetched_at) "
            "VALUES (?,?,?,?,?,'x')",
            (pid, st, fs, rd, com),
        )
    av = load_availability(conn, {"p1": "AAA", "p2": "BBB", "p3": "BBB"}, date(2026, 10, 16))
    assert av.at["p1", "availability_ros"] == pytest.approx(0.6)  # misses 4 of 10
    assert av.at["p2", "availability_ros"] == 0.0
    assert av.at["p3", "availability_ros"] == pytest.approx(0.8)  # 1 of 5 BBB games


def test_play_probs_override_status(conn):
    models = build_player_models(conn, ["x", "y"], [2027], statuses={"x": "O", "y": "O"}, play_probs={"x": 0.7})
    assert models["x"].play_prob == pytest.approx(0.7)
    assert models["y"].play_prob == 0.0
