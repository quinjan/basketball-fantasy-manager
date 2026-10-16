from fbm.analytics.lineup import il_moves, optimize_day
from fbm.league.model import RosterPlayer, eligibility_from_espn_position

SLOTS = ["PG", "SG", "G", "SF", "PF", "F", "C", "C", "UTIL", "UTIL"]


def p(pid, pos, selected="BN", status="", eligible=None):
    return RosterPlayer(pid, pid, "TM", eligible or eligibility_from_espn_position(pos), selected, status)


def test_eligibility_mapping():
    assert eligibility_from_espn_position("G") == ("PG", "SG", "G", "UTIL")
    assert eligibility_from_espn_position("C") == ("C", "UTIL")


def test_maximizes_games_started_using_flexible_slots():
    players = [
        p("pg1", "PG", "PG"),
        p("pg2", "PG", "BN"),
        p("c1", "C", "C"),
        p("c2", "C", "C"),
        p("c3", "C", "BN"),
        p("f1", "F", "F"),
    ]
    playing = {"pg1", "pg2", "c1", "c2", "c3", "f1"}
    res = optimize_day(players, SLOTS, playing, value={})
    assert res.starters_with_games == 6
    assert res.benched_with_games == ()
    assert res.assignments["pg1"] == "PG"  # stability: unchanged when possible
    assert res.assignments["c3"] == "UTIL" and res.assignments["pg2"] in ("G", "SG", "UTIL")


def test_value_breaks_ties_when_too_many_players():
    players = [p(f"c{i}", "C", "BN") for i in range(5)]
    value = {f"c{i}": float(i) for i in range(5)}
    res = optimize_day(players, SLOTS, {f"c{i}" for i in range(5)}, value)
    assert res.benched_with_games == ("c0",)
    assert res.starters_with_games == 4


def test_out_players_and_no_game_players_yield_slots():
    players = [p("c1", "C", "C", status="O"), p("c2", "C", "BN"), p("c3", "C", "BN")]
    res = optimize_day(players, ["C"], playing={"c1", "c2"}, value={"c3": 50.0})
    assert res.assignments == {"c1": "BN", "c2": "C", "c3": "BN"}
    assert res.changes == {"c1": ("C", "BN"), "c2": ("BN", "C")}


def test_locked_players_keep_slot():
    players = [p("pg1", "PG", "PG"), p("pg2", "PG", "BN")]
    res = optimize_day(players, ["PG"], playing={"pg1", "pg2"}, value={"pg2": 10.0}, locked=["pg1"])
    assert res.assignments == {"pg1": "PG", "pg2": "BN"}


def test_il_moves():
    hurt = p("h", "PG", "BN", status="INJ", eligible=("PG", "G", "UTIL", "IL"))
    healed = p("x", "C", "IL", eligible=("C", "UTIL"))
    moves = il_moves([hurt, healed], {"IL": 1, "IL+": 1})
    assert [(m.player_id, m.to_slot, m.blocked) for m in moves] == [("h", "IL", False), ("x", "BN", False)]


def test_il_activation_blocked_when_roster_full_unless_stash_frees_a_spot():
    healthy = [p(f"a{i}", "G", "BN") for i in range(3)]
    healed = p("x", "C", "IL", eligible=("C", "UTIL"))
    moves = il_moves([*healthy, healed], {"IL": 1}, capacity=3)
    assert [(m.player_id, m.blocked) for m in moves] == [("x", True)]
    hurt = p("h", "PG", "BN", status="O", eligible=("PG", "UTIL", "IL", "IL+"))
    moves = il_moves([*healthy[:2], hurt, healed], {"IL": 1, "IL+": 1}, capacity=3)
    assert [(m.player_id, m.to_slot, m.blocked) for m in moves] == [("h", "IL+", False), ("x", "BN", False)]
    assert il_moves([*healthy[:2], hurt], {"IL": 1}, capacity=3, locked=["h"]) == []


def test_started_teams_and_il_eligibility():
    from datetime import UTC, datetime

    from fbm.analytics.lineup import started_teams
    from fbm.league.model import with_il_eligibility

    now = datetime(2026, 10, 21, 0, 0, tzinfo=UTC)
    games = [
        {"start_utc": "2026-10-20T23:00:00+00:00", "state": "pre", "home": "BOS", "away": "NY"},
        {"start_utc": "2026-10-21T02:00:00+00:00", "state": "pre", "home": "LAL", "away": "GS"},
        {"start_utc": "2026-10-21T03:00:00+00:00", "state": "in", "home": "POR", "away": "PHX"},
    ]
    assert started_teams(games, now) == {"BOS", "NY", "POR", "PHX"}
    assert with_il_eligibility(("C", "UTIL"), "OUT") == ("C", "UTIL", "IL", "IL+")
    assert with_il_eligibility(("C", "UTIL", "IL", "IL+"), "GTD") == ("C", "UTIL")
