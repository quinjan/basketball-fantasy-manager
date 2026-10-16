from datetime import date

import numpy as np
import pandas as pd
import pytest

from fbm import constants
from fbm.analytics import players as player_tables
from fbm.analytics.playoffs import (
    FantasyWeek,
    fantasy_weeks,
    playoff_schedule,
    playoff_value,
    playoff_weeks,
    season_progress,
)
from fbm.analytics.waivers import Competitor, claim_threshold, decide, interested_teams, p_claimed
from fbm.league.demo import default_waiver_priority, load_league, rotate_waiver_priority, save_league
from fbm.league.model import FantasyTeam, LeagueState, RosterPlayer, SlotSpec
from fbm.store import db


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


# ------------------------------------------------------------------ constants


def test_registry_entries_are_documented():
    kinds = {"measured", "research", "rule", "heuristic"}
    for c in constants.REGISTRY.values():
        assert c.description and c.source and c.unit and c.kind in kinds, c.key
    assert constants.REGISTRY["week.avg_games_per_week"].kind == "measured"


def test_overrides_apply_and_unknown_keys_reported(tmp_path, monkeypatch):
    path = tmp_path / "constants.toml"
    path.write_text('"stream.min_gain" = 0.2\n[waiver]\nthreshold_top = 2\n"bogus.key" = 1\n', encoding="utf-8")
    monkeypatch.setattr(constants, "OVERRIDES_PATH", path)
    monkeypatch.setattr(constants, "_cache", {"mtime": None, "values": {}})
    assert constants.C("stream.min_gain") == 0.2
    assert constants.C("waiver.threshold_top") == 2.0 and isinstance(constants.C("waiver.threshold_top"), float)
    assert constants.unknown_overrides() == ["waiver.bogus.key"]
    row = next(r for r in constants.table() if r["key"] == "stream.min_gain")
    assert row["overridden"] and row["default"] == 0.05
    with pytest.raises(KeyError):
        constants.C("nope")


# ------------------------------------------------------------------ projections: minutes trend

STATS = ("minutes", "pts", "reb", "ast", "stl", "blk", "tov", "tpm", "tpa", "fgm", "fga", "ftm", "fta")


def _avg(conn, season, pid, minutes, pts, gp):
    vals = dict.fromkeys(STATS, 1.0) | {"minutes": minutes, "pts": pts, "fga": 10.0, "fgm": 5.0}
    conn.execute(
        f"INSERT INTO season_averages(season, espn_id, name, team, position, gp, {', '.join(STATS)}, updated_at) "
        f"VALUES (?,?,?,?,?,?,{','.join('?' * len(STATS))},'x')",
        (season, pid, pid, "TM", "G", gp, *[vals[s] for s in STATS]),
    )


def _lines(conn, pid, minutes_list):
    for i, m in enumerate(minutes_list):
        conn.execute(
            "INSERT INTO box_lines(event_id, espn_id, et_date, season, team, starter, played, minutes) "
            "VALUES (?,?,?,2027,'TM',1,1,?)",
            (f"e{pid}{i}", pid, f"2026-11-{i + 1:02d}", m),
        )


def test_minutes_trend_scales_counting_stats(conn):
    _avg(conn, 2026, "riser", 20.0, 10.0, 70)
    _avg(conn, 2027, "riser", 22.0, 11.0, 10)
    _lines(conn, "riser", [20, 20, 20, 20, 20, 30, 30, 30, 30, 30])  # last 10: avg 25 vs season 22
    _avg(conn, 2026, "steady", 30.0, 15.0, 70)
    _avg(conn, 2027, "steady", 30.0, 15.0, 10)
    _lines(conn, "steady", [30] * 10)
    proj = player_tables.projection_table(conn, 2027, 2026)
    r, s = proj.loc["riser"], proj.loc["steady"]
    assert r["role"] == "up" and r["minutes_trend"] == pytest.approx(3.0)
    w = 0.6  # trend_max_weight with a full 10-game window
    blended_min = 10 / 25 * 22 + 15 / 25 * 20
    expected_min = (1 - w) * blended_min + w * 25
    assert r["minutes"] == pytest.approx(expected_min)
    assert r["pts"] == pytest.approx((10 / 25 * 11 + 15 / 25 * 10) * expected_min / blended_min)
    assert r["fgm"] / r["fga"] == pytest.approx(0.5)  # percentages unchanged
    assert s["role"] == "" and s["minutes"] == pytest.approx(30.0)


def test_projection_without_current_box_scores_has_no_trend(conn):
    _avg(conn, 2026, "p", 25.0, 12.0, 60)
    proj = player_tables.projection_table(conn, 2027, 2026)
    assert proj.loc["p", "role"] == "" and np.isnan(proj.loc["p", "minutes_trend"])


# ------------------------------------------------------------------ playoffs


def _game(conn, event, day, home, away):
    conn.execute(
        "INSERT INTO games(event_id, season, season_type, start_utc, et_date, home, away, state, completed, "
        "neutral, time_valid, updated_at) VALUES (?,2027,2,'x',?,?,?,'pre',0,0,1,'x')",
        (event, day, home, away),
    )


def test_playoff_weeks_and_team_games(conn):
    days = ["2026-10-20", "2026-10-27", "2027-03-23", "2027-03-25", "2027-03-31", "2027-04-07", "2027-04-09"]
    for i, d in enumerate(days):
        _game(conn, f"g{i}", d, "BOS" if i % 2 == 0 else "LAL", "NY")
    weeks = fantasy_weeks(conn, 2027)
    assert weeks[0] == FantasyWeek(1, date(2026, 10, 19), date(2026, 10, 25))
    assert weeks[-1].start == date(2027, 4, 5)
    po = playoff_weeks(weeks)
    assert [w.start for w in po] == [date(2027, 3, 22), date(2027, 3, 29), date(2027, 4, 5)]
    custom = playoff_weeks(
        weeks, {"end_week": weeks[-1].number, "settings": {"playoff_start_week": weeks[-2].number, "uses_playoff": "1"}}
    )
    assert len(custom) == 2
    sched = playoff_schedule(conn, 2027)
    assert sched.team_games["NY"] == [2, 1, 2] and sched.games("BOS") == 3 and sched.games("XXX") == 0
    assert sched.average_total == pytest.approx((5 + 3 + 2) / 3)


def test_playoff_value_and_progress():
    assert playoff_value(2.0, 12, 10.0) == pytest.approx(2.4)
    assert season_progress(date(2026, 10, 20), date(2026, 10, 20), date(2027, 3, 22)) == 0.0
    assert season_progress(date(2027, 4, 1), date(2026, 10, 20), date(2027, 3, 22)) == 1.0


# ------------------------------------------------------------------ waivers


def test_claim_threshold_scales_with_priority():
    assert claim_threshold(1, 12) == pytest.approx(1.0)
    assert claim_threshold(12, 12) == pytest.approx(0.1)
    assert claim_threshold(1, 12) > claim_threshold(6, 12) > claim_threshold(12, 12)


def test_decide_actions():
    ahead = [Competitor("t1", "A", 1, 0.5), Competitor("t2", "B", 2, 0.3)]
    behind = [Competitor("t9", "Z", 9, 0.4), Competitor("t10", "Y", 10, 0.2)]
    assert decide(-0.2, True, 3, 12, []).action == "pass"
    assert decide(0.3, False, 1, 12, ahead).action == "add"
    assert decide(1.5, True, 1, 12, ahead).action == "claim"
    assert decide(0.3, True, 1, 12, []).action == "wait"
    assert decide(0.3, True, 3, 12, ahead).action == "pass"
    # teams behind us also take him if we wait
    d = decide(0.3, True, 3, 12, behind)
    assert d.action == "pass"
    assert d.p_claimed_before_me == 0.0
    assert d.p_claimed_by_other == pytest.approx(0.75)
    assert p_claimed(ahead, activity=0.5) == pytest.approx(0.75)


def test_interested_teams():
    vec = {"pts": 1.0, "reb": 0.0}
    prio = {"me": 4, "t1": 1, "t2": 2, "t5": 5}
    weakest = {"t1": {"pts": -1.0, "reb": 0.0}, "t2": {"pts": 2.0, "reb": 0.0}, "t5": {"pts": -1.0, "reb": 0.0}}
    comps = interested_teams(vec, 4, prio, weakest, {}, {"t1": "One"})
    assert [(c.team_key, c.name, c.gain) for c in comps] == [("t1", "One", 2.0), ("t5", "t5", 2.0)]


def test_waiver_priority_rotation_and_roundtrip(tmp_path):
    teams = [
        FantasyTeam(f"t{i}", f"T{i}", players=[RosterPlayer(f"p{i}", "P", "X", ("PG", "UTIL"))]) for i in (1, 2, 3)
    ]
    league = LeagueState("demo", "D", 2027, "t2", teams, slots=(SlotSpec("PG", 1, True),), source="demo")
    prio = default_waiver_priority(league)
    assert prio == {"t1": 3, "t2": 2, "t3": 1}
    assert rotate_waiver_priority(prio, "t3") == {"t1": 2, "t2": 1, "t3": 3}
    league.waiver_priority = rotate_waiver_priority(prio, "t2")
    path = tmp_path / "demo.json"
    save_league(league, path)
    assert load_league(path).waiver_priority == {"t1": 2, "t2": 3, "t3": 1}


def test_values_frame_sanity():
    df = pd.DataFrame({"a": [1]})
    assert not df.empty
