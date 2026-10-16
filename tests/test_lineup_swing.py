import pandas as pd

from fbm.analytics.categories import Category
from fbm.analytics.lineup import optimize_day
from fbm.analytics.lineup_swing import category_weights, swing_categories, swing_values
from fbm.analytics.simulation import CategoryOdds, MatchupOdds
from fbm.league.model import RosterPlayer


def cat(key):
    return Category(key, key.upper(), "count", key)


def odds(ps):
    cats = tuple(CategoryOdds(cat(k), pw, 0.0, 1 - pw, 0.0, 0.0) for k, pw in ps.items())
    return MatchupOdds(cats, 0.0, 0.0, ())


ODDS = odds({"pts": 0.95, "reb": 0.5, "ast": 0.05, "blk": 0.5})
VALUES = pd.DataFrame(
    {"g_pts": [3.0, 0.0, 0.5], "g_reb": [0.0, 1.0, 0.5], "g_ast": [0.0, 0.0, 0.0], "g_blk": [0.0, 0.5, 0.0]},
    index=["scorer", "big", "mid"],
)


def test_weights_order():
    w = category_weights(ODDS, punts=("blk",), band=0.25, floor=0.2)
    assert w["reb"] == 1.0
    assert w["pts"] == w["ast"] == 0.2
    assert w["blk"] == 0.0
    assert swing_categories(w, 0.2) == ["reb"]
    assert category_weights(None) == {}


def test_fallback_to_ros_without_odds():
    assert swing_values(VALUES, {}, {"scorer": 3.0}) == {"scorer": 3.0}


def test_tight_slot_swing_choice_differs_and_games_never_drop():
    players = [
        RosterPlayer("scorer", "scorer", "A", ("PG", "UTIL")),
        RosterPlayer("big", "big", "B", ("C", "UTIL")),
        RosterPlayer("mid", "mid", "C", ("SF", "UTIL")),
        RosterPlayer("idle", "idle", "D", ("PF", "UTIL"), "UTIL"),
    ]
    slots = ["UTIL"]
    playing = {"scorer", "big", "mid"}
    ros = VALUES.sum(axis=1).to_dict()  # scorer 3.0 > big 1.5 > mid 1.0
    plain = optimize_day(players, slots, playing, ros)
    w = category_weights(ODDS, band=0.25, floor=0.2)
    swing = optimize_day(players, slots, playing, swing_values(VALUES, w, ros))
    assert plain.assignments["scorer"] == "UTIL"
    assert swing.assignments["big"] == "UTIL"  # REB/BLK are contested; PTS is already locked
    assert swing.starters_with_games >= plain.starters_with_games == 1
