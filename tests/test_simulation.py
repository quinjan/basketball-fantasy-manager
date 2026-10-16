from datetime import date

import numpy as np
import pytest

from fbm.analytics.categories import NINE_CAT
from fbm.analytics.simulation import (
    COL,
    STATS,
    PlayerModel,
    TeamWeek,
    compare,
    play_probability,
    simulate_matchup,
    simulate_totals,
)
from fbm.analytics.team_profile import league_profile
from fbm.analytics.week import date_range, fantasy_week, generic_week, plan_team_week
from fbm.league.demo import load_league, save_league
from fbm.league.model import FantasyTeam, LeagueState, RosterPlayer, SlotSpec


def line(**kw) -> np.ndarray:
    row = np.zeros(len(STATS))
    for k, v in kw.items():
        row[COL[k]] = v
    return row


STAR = PlayerModel(
    "star", np.array([line(pts=30, reb=10, ast=8, stl=2, blk=1, tov=3, tpm=3, fgm=11, fga=20, ftm=5, fta=6)])
)
SCRUB = PlayerModel(
    "scrub", np.array([line(pts=5, reb=2, ast=1, stl=0, blk=0, tov=1, tpm=1, fgm=2, fga=6, ftm=0, fta=0)])
)


def test_deterministic_models_give_certain_outcomes():
    odds = simulate_matchup(
        TeamWeek("a", ["star"] * 3),
        TeamWeek("b", ["scrub"] * 3),
        {"star": STAR, "scrub": SCRUB},
        NINE_CAT,
        n_sims=200,
        seed=1,
    )
    by = {o.category.key: o for o in odds.categories}
    assert by["pts"].p_win == 1.0 and by["tov"].p_loss == 1.0  # more turnovers loses TO
    assert by["fg_pct"].p_win == 1.0  # 55% vs 33%
    assert by["ft_pct"].p_win == 1.0  # scrub has 0 FTA -> 0%
    assert odds.expected_score == pytest.approx(8.0)
    assert odds.p_majority == 1.0 and odds.p_majority_loss == 0.0


def test_banked_stats_and_play_probability():
    out = PlayerModel("out", STAR.samples, play_prob=0.0)
    totals = simulate_totals(TeamWeek("a", ["out"], banked={"pts": 50.0}), {"out": out}, 10, np.random.default_rng(0))
    assert (totals[:, COL["pts"]] == 50).all()
    assert play_probability("GTD") == 0.6 and play_probability("O") == 0.0 and play_probability(None) == 1.0


def test_ties_counted():
    a = np.array([line(stl=2, fgm=1, fga=2)] * 5)
    odds = compare(a, a.copy(), NINE_CAT)
    by = {o.category.key: o for o in odds.categories}
    assert by["stl"].p_tie == 1.0 and odds.expected_score == pytest.approx(4.5)


def test_week_helpers_and_planning():
    assert fantasy_week(date(2026, 10, 21)) == (date(2026, 10, 19), date(2026, 10, 25))
    days = date_range(date(2026, 10, 20), date(2026, 10, 22))
    team = FantasyTeam(
        "t",
        "T",
        players=[
            RosterPlayer("a", "A", "BOS", ("PG", "G", "UTIL"), "PG"),
            RosterPlayer("b", "B", "LAL", ("PG", "G", "UTIL"), "BN"),
        ],
    )
    playing = {days[0]: {"BOS", "LAL"}, days[1]: {"BOS"}, days[2]: set()}
    week, plans = plan_team_week(team, days, playing, ["PG"], value={"a": 1.0, "b": 2.0})
    assert week.player_games == ["b", "a"]  # day 1: only one PG slot -> higher value; day 2: a plays
    assert [len(p.started) for p in plans] == [1, 1, 0]
    g = generic_week(team, n_starting=1)
    assert len(g.player_games) == 8 and g.play_scale["a"] == pytest.approx(0.5 * 3.5 / 4)


def _league() -> LeagueState:
    teams = [
        FantasyTeam("t1", "Stars", players=[RosterPlayer("star", "S", "BOS", ("PG", "UTIL"), "PG")]),
        FantasyTeam("t2", "Scrubs", players=[RosterPlayer("scrub", "s", "LAL", ("PG", "UTIL"), "PG")]),
    ]
    return LeagueState("L", "Test", 2027, "t1", teams, slots=(SlotSpec("PG", 1, True), SlotSpec("BN", 1, False)))


def test_league_profile_ranks():
    profiles = {p.team_key: p for p in league_profile(_league(), {"star": STAR, "scrub": SCRUB}, n_sims=200)}
    assert profiles["t1"].cat_score["pts"] == 1.0 and profiles["t1"].cat_rank["pts"] == 1
    assert profiles["t2"].cat_score["tov"] == 1.0
    assert profiles["t1"].expected_score == pytest.approx(8.0)


def test_demo_save_load_roundtrip(tmp_path):
    league = _league()
    path = tmp_path / "demo.json"
    save_league(league, path)
    loaded = load_league(path)
    assert loaded.my_team.players[0].eligible == ("PG", "UTIL")
    assert [c.key for c in loaded.categories] == [c.key for c in NINE_CAT]
    assert loaded.starting_slots() == ["PG"]
