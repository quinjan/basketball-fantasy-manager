"""HT parsers on sanitized fixtures; layout checks never return partial data."""

from pathlib import Path

import pytest

from fbm.hashtag import parsers as P
from fbm.hashtag.errors import HashtagLayoutChanged

FIX = Path(__file__).parent / "fixtures" / "hashtag"


def fx(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def test_projections_rows_views_and_z_orientation():
    x = P.parse_projections(fx("projections_ros.html"))
    assert x["view"] == "ROS"
    assert x["settings"] == {"show": "All", "rank": "Averages", "type": "H2H", "posfrom": "Yahoo"}
    jokic = x["rows"][0]
    assert jokic["ht_id"] == "9196" and jokic["name"] == "Nikola Jokic" and jokic["team"] == "DEN"
    assert (jokic["fg_pct"], jokic["fgm"], jokic["fga"]) == (0.573, 10.5, 18.3)
    assert jokic["reb"] == 12.7 and jokic["tov"] == 3.5 and jokic["adp"] == 1.4
    assert jokic["z_fg_pct"] == pytest.approx(3.946203)
    assert jokic["z_tov"] == pytest.approx(-2.2975026457)  # HT's HFTUR flipped: higher = better
    assert jokic["note"].startswith("[+] ")
    assert P.parse_projections(fx("projections_stream7.html"))["view"] == "STREAM7"


@pytest.mark.parametrize(
    ("text", "view"),
    [
        ("2026-27 Rest of Season Projections", "ROS"),
        ("2026-27 Rest-of-Season Projections (BLEND30)", "BLEND30"),
        ("2026-27 Short Term Projections (STREAM7)", "STREAM7"),
        ("2026-27 Short-term Projections (STREAM14)", "STREAM14"),
        ("Last 7 days (2026-27 Regular Season)", None),
    ],
)
def test_view_from_text(text, view):
    assert P.view_from_text(text) == view


def test_layout_change_raises():
    with pytest.raises(HashtagLayoutChanged):
        P.parse_projections(fx("projections_ros.html").replace(">TOTAL<", ">VALUE<"))
    with pytest.raises(HashtagLayoutChanged):
        P.parse_projections(fx("projections_ros.html").replace("HFFGP", "HFXXX"))
    with pytest.raises(HashtagLayoutChanged):
        P.parse_h2h_planner(fx("h2h_planner.html").replace("AHOME_pts2", "AHOME_points"))
    with pytest.raises(HashtagLayoutChanged):
        P.parse_notebook("<html><body>nothing</body></html>")


def test_season_rankings():
    x = P.parse_season_rankings(fx("season_rankings.html"))
    assert x["duration"] == "season" and x["rows"][0]["name"] == "Luka Doncic" and x["rows"][0]["gp"] == 3


def test_waiver_wire_multipliers_and_rows():
    x = P.parse_waiver(fx("waiver_wire.html"))
    assert x["view"] == "ROS"
    assert x["weights"]["ast"] == 1.25 and x["weights"]["tpm"] == 0.5 and x["weights"]["tov"] == 0.25
    assert x["rows"][0]["ht_id"] == "31058" and x["rows"][0]["total"] == 2.86


def test_h2h_planner_totals_share_and_roster():
    x = P.parse_h2h_planner(fx("h2h_planner.html"))
    assert (x["my_team"], x["opp_team"], x["week"], x["view"], x["spots"]) == ("My Team", "Opponent A", 2, "ROS", 10)
    pts = x["categories"]["pts"]
    assert (pts["me_before"], pts["me_after"], pts["opp_after"]) == (0.0, 761.8, 677.1)
    assert x["categories"]["fg_pct"]["ht_share"] == pytest.approx(0.59)
    assert x["roster_side"] == "me" and len(x["roster"]) == 16
    assert x["roster"][0] == {"yahoo_id": "5185", "name": "Giannis Antetokounmpo", "positions": "PF,C",
                              "team": "MIA", "days": ["MON", "WED", "FRI"]}  # fmt: skip


def test_ht_share_formula():
    assert P.ht_share("pts", 0, 0) == 0.5
    assert P.ht_share("pts", 75, 25) == 0.75
    assert P.ht_share("tov", 75, 25) == 0.25  # lower is better
    assert P.ht_share("fg_pct", 0.50, 0.47) == pytest.approx(0.575)
    assert P.ht_share("ft_pct", 0.9, 0.5) == 0.95  # clipped
    assert P.ht_share("blk", 1, 99) == 0.05


def test_team_ranks():
    x = P.parse_team_ranks(fx("scouting_report.html"))
    assert x["view"] == "ROS" and len(x["rows"]) == 14
    assert x["rows"][0]["categories"]["fg_pct"] == {"value": 0.486, "rank": 6}


def test_trade_finder():
    assert P.parse_trade_finder(fx("trade_finder_default.html"))["ideas"] == []
    x = P.parse_trade_finder(fx("trade_finder_results.html"))
    idea = x["ideas"][0]
    assert x["settings"]["fairness"] == "fair" and x["settings"]["weights"]["tov"] == 0.25
    assert [p["name"] for p in idea["give"]] == ["Josh Giddey", "Ajay Mitchell"]
    assert [p["ht_id"] for p in idea["get"]] == ["31045", "15211"]
    assert idea["my_delta"] == pytest.approx(0.82) and idea["their_delta"] == pytest.approx(-0.82)
    assert idea["ranks_after"]["reb"] == 1 and "Within fair margin" in idea["badges"]


def test_notebook_is_data():
    notes = P.parse_notebook(fx("notebook.html"))
    assert notes[0]["note_date"] == "2026-10-26" and notes[0]["player"] == "Isaiah Stewart"
    assert notes[0]["team"] == "MEM" and notes[0]["source"] == "eriks_notes"
    assert "ignore previous instructions" in notes[0]["text"]  # stored as text, never acted on


def test_next_man_up():
    empty = P.parse_next_man_up(fx("next_man_up.html"))
    assert empty["teammates"] == [] and empty["season"] == "2627"
    x = P.parse_next_man_up(fx("next_man_up_with_data.html"))
    assert x["absent_ht_id"] == "120223" and x["absent_name"] == "Asa Newell"
    kp = x["teammates"][0]
    assert kp["name"] == "Kristaps Porzingis" and kp["ht_id"] == "9373"
    assert (kp["gp_without"], kp["min_without"], kp["min_with"]) == (9, 25.4, 23.0)
    assert kp["stats"]["pts"] == {"without": "18.1", "with": "15.9"}


def test_premium_home():
    assert P.parse_premium_home(fx("premium_home.html")) == [
        {"lid": "11111", "name": "Test League", "refresh_path": "/import-v4/refresh-yahoo.aspx?lid=11111"}
    ]
