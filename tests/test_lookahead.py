"""team_roster (any-team view) and next_week (lookahead): pure helpers and the tool wiring, all offline."""

from datetime import date, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest

from fbm.gm import lookahead as la
from fbm.gm import services as svc
from fbm.league.model import FantasyTeam, LeagueState, RosterPlayer, SlotSpec
from fbm.store import db
from tests.test_yahoo_services import FakeClient

ME, THEM, OTHER = "t.1", "t.2", "t.3"


def _league(source: str = "demo") -> LeagueState:
    def team(key, name, manager, players):
        return FantasyTeam(key, name, manager, players)

    me = team(ME, "Glass Cleaners", "Quin", [RosterPlayer("a", "A Guard", "BOS", ("PG", "UTIL"), "PG")])
    them = team(
        THEM,
        "Bench Mob",
        "Jayson D",
        [
            RosterPlayer("b", "B Wing", "LAL", ("SF", "UTIL"), "UTIL"),
            RosterPlayer("c", "C Big", "BOS", ("C", "UTIL"), "BN"),
            RosterPlayer("d", "D Hurt", "MIA", ("PF", "UTIL"), "IL", status="O"),
        ],
    )
    other = team(OTHER, "Jaylen Fan Club", "Sam", [])
    slots = (SlotSpec("PG", 1, True), SlotSpec("UTIL", 2, True), SlotSpec("BN", 2, False), SlotSpec("IL", 1, False))
    return LeagueState("l", "L", 2027, ME, [me, them, other], slots=slots, source=source, week=1, opponent_key=THEM)


def test_match_team_key_name_manager_and_default():
    league = _league()
    assert la.match_team(league, "T.2").name == "Bench Mob"  # key, case-insensitive
    assert la.match_team(league, "glass cleaners").team_key == ME  # exact name
    assert la.match_team(league, "quin").team_key == ME  # manager
    assert la.match_team(league, "bench").team_key == THEM  # unique substring
    with pytest.raises(svc.GMError, match="matched 2"):
        la.match_team(league, "jay")  # Jayson D and Jaylen Fan Club
    with pytest.raises(svc.GMError, match="matched 0"):
        la.match_team(league, "nobody")


def _row(name, slot, games, g, status="", injury=None, today=False):
    return {
        "player": name,
        "slot": slot,
        "status": status,
        "games_left_this_week": games,
        "game_today": today,
        "g_by_cat": g,
        "injury": injury,
    }


def test_team_summary_excludes_il_and_flags_injuries():
    rows = [
        _row("A", "PG", 3, {"PTS": 2.0, "TO": -1.0, "BLK": 0.5, "REB": 0.1}, today=True),
        _row("B", "BN", 2, {"PTS": 1.0, "TO": -0.5, "BLK": -0.5, "REB": 0.2}, status="GTD"),
        _row("C", "IL", 4, {"PTS": 9.0}, status="O", injury={"status": "Out", "return_date": "2027-01-02"}),
    ]
    s = la.team_summary(rows)
    assert s["games_remaining_this_week"] == 5  # the IL player's 4 games don't count
    assert s["players_with_game_today"] == ["A"]
    assert s["category_strengths"]["g_by_cat"] == {"PTS": 3.0, "TO": -1.5, "BLK": 0.0, "REB": 0.3}
    assert s["category_strengths"]["strongest"] == ["PTS", "REB", "BLK"]
    assert s["category_strengths"]["weakest"] == ["TO", "BLK", "REB"]
    assert [(i["player"], i["out"]) for i in s["injured"]] == [("B", False), ("C", True)]
    assert s["out_count"] == 1 and s["injured"][1]["return_date"] == "2027-01-02"


def test_next_week_bounds_prefers_yahoo_schedule():
    assert la.next_week_bounds(date(2026, 11, 1)) == (date(2026, 11, 2), date(2026, 11, 8))
    assert la.next_week_bounds(date(2026, 11, 1), date(2026, 11, 2), date(2026, 11, 15)) == (
        date(2026, 11, 2),
        date(2026, 11, 15),
    )  # a two-week stretch (e.g. around the All-Star break)
    assert la.next_week_bounds(date(2026, 11, 1), date(2026, 10, 26), date(2026, 11, 1)) == (
        date(2026, 11, 2),
        date(2026, 11, 8),
    )  # a stale/overlapping Yahoo window is ignored


def test_games_by_team_and_best_streams():
    playing = {date(2026, 11, 2): {"BOS", "LAL"}, date(2026, 11, 3): {"BOS", "MIA"}}
    counts = la.games_by_team(playing)
    assert counts == {"BOS": 2, "LAL": 1, "MIA": 1}
    pool = [
        {"player": "X", "id": "x", "nba": "BOS", "value": 1.0},
        {"player": "Y", "id": "y", "nba": "LAL", "value": 1.4},
        {"player": "Z", "id": "z", "nba": "NYK", "value": 3.0},  # no games next week
    ]
    best = la.best_streams(pool, counts, top=2)
    assert [(p["player"], p["games_next_week"], p["next_week_value"]) for p in best] == [("X", 2, 0.57), ("Y", 1, 0.4)]


def test_team_roster_defaults_to_opponent_and_wraps_view(monkeypatch):
    league = _league()
    seen = {}

    def fake_view(team, day, punts):
        seen.update(team=team.team_key, day=day, punts=punts)
        return {
            "team": team.name,
            "date_et": "2026-11-02",
            "punts_applied": [],
            "players": [_row("B Wing", "UTIL", 2, {"PTS": 1.0}), _row("D Hurt", "IL", 3, {"PTS": 4.0}, status="O")],
        }

    monkeypatch.setattr(svc, "active_league", lambda: league)
    monkeypatch.setattr(svc, "_roster_view", fake_view)
    res = svc.team_roster()
    assert seen == {"team": THEM, "day": None, "punts": None}
    assert res["team"] == "Bench Mob" and res["is_this_week_opponent"] and not res["is_mine"]
    assert res["summary"]["games_remaining_this_week"] == 2 and res["summary"]["out_count"] == 1
    mine = svc.team_roster("quin", "2026-11-02", "FT%")
    assert mine["is_mine"] and not mine["is_this_week_opponent"]
    assert seen == {"team": ME, "day": "2026-11-02", "punts": "FT%"}


def _set_window(monkeypatch, week_end: date):
    window = (week_end - timedelta(days=2), week_end - timedelta(days=6), week_end)
    monkeypatch.setattr(svc, "_week_window", lambda day=None: window)


def _patch_lookahead(monkeypatch, league):
    conn = db.connect(":memory:")
    for i, (d, home, away) in enumerate(
        [
            ("2026-11-02", "BOS", "LAL"),
            ("2026-11-03", "BOS", "MIA"),
            ("2026-11-04", "LAL", "MIA"),
            ("2026-10-30", "BOS", "NYK"),  # before next week: ignored
        ]
    ):
        conn.execute(
            "INSERT INTO games VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (str(i), 2027, 2, d + "T00:00Z", d, home, away, "pre", 0, 0, 1, 0, d),
        )
    monkeypatch.setattr(svc, "active_league", lambda: league)
    monkeypatch.setattr(svc, "_conn", lambda: conn)
    _set_window(monkeypatch, date(2026, 11, 1))
    monkeypatch.setattr(svc, "ctx", lambda: SimpleNamespace(return_dates=lambda conn=None: {}))
    monkeypatch.setattr(svc, "_strategy_punts", lambda punts: ())
    monkeypatch.setattr(svc, "_value", lambda c, punts: pd.Series({"a": 1.0, "b": 2.0, "c": 0.5, "d": 3.0}))
    monkeypatch.setattr(
        svc,
        "rankings",
        lambda **kw: {
            "players": [
                {"player": "FA Boston", "id": "f1", "nba": "BOS", "value": 2.0, "status": ""},
                {"player": "FA Laker", "id": "f2", "nba": "LAL", "value": 2.0, "status": ""},
            ]
        },
    )

    def fake_matchup(lg, me, opp, start, n):
        cat = SimpleNamespace(label="PTS", kind="count")
        o = SimpleNamespace(category=cat, mean_a=100.0, mean_b=90.0, p_win=0.7, p_tie=0.05, p_loss=0.25)
        odds = SimpleNamespace(categories=[o], expected_score=5.5, p_majority=0.6, swing=lambda: [o])
        mw, ow = SimpleNamespace(player_games=["a"] * 2), SimpleNamespace(player_games=["b"] * 3)
        return odds, mw, ow, date(2026, 11, 2), date(2026, 11, 8)

    monkeypatch.setattr(svc, "_matchup", fake_matchup)


def test_next_week_demo_league_without_schedule(monkeypatch):
    league = _league("demo")
    _patch_lookahead(monkeypatch, league)
    res = svc.next_week()
    assert (res["start_et"], res["end_et"], res["days"]) == ("2026-11-02", "2026-11-08", 7)
    assert res["opponent"] is None and "demo league" in res["opponent_note"]
    assert res["nba_games_by_team"] == {"LAL": 2, "MIA": 2, "BOS": 2}
    assert res["me"]["player_games"] == 2 and "simulation" not in res
    assert res["free_agents_most_games"]["players"][0]["games_next_week"] == 2
    # an explicit opponent still gets the full preview
    res = svc.next_week("bench")
    assert res["opponent"] == "Bench Mob" and res["opponent_source"] == "caller"
    opp = res["opponent_roster"]
    assert opp["player_games"] == 4  # B (LAL x2) + C (BOS x2); the IL player is excluded
    assert {p["player"]: p["games_next_week"] for p in opp["players"]} == {"B Wing": 2, "C Big": 2, "D Hurt": 2}
    assert res["simulation"]["window_matches_week"] and res["simulation"]["p_win_majority"] == 0.6
    assert res["simulation"]["games_started"] == {"me": 2, "opp": 3}


def test_next_week_uses_yahoo_schedule(monkeypatch):
    from tests.test_yahoo_league import SCOREBOARD_XML

    league = _league("yahoo")
    league.league_key = "466.l.1"
    league.my_team_key = "466.l.1.t.1"
    league.teams[0].team_key = "466.l.1.t.1"
    league.teams[1].team_key, league.teams[1].name = "466.l.1.t.2", "Bench Mob"
    _patch_lookahead(monkeypatch, league)
    client = FakeClient({"scoreboard": SCOREBOARD_XML})
    monkeypatch.setattr(svc, "_yahoo_client", lambda: client)
    _set_window(monkeypatch, date(2026, 10, 26))
    res = svc.next_week()
    assert "scoreboard;week=2" in client.paths[0]
    assert res["opponent"] == "Bench Mob" and res["opponent_source"] == "Yahoo schedule" and res["week"] == 2
    assert (res["start_et"], res["end_et"]) == ("2026-10-27", "2026-11-02")  # Yahoo's own bounds


def test_next_week_survives_yahoo_failure(monkeypatch):
    league = _league("yahoo")
    _patch_lookahead(monkeypatch, league)

    class Broken:
        def __enter__(self):
            raise RuntimeError("boom")

        def __exit__(self, *exc):
            return None

    monkeypatch.setattr(svc, "_yahoo_client", lambda: Broken())
    res = svc.next_week()
    assert res["opponent"] is None and "unavailable" in res["opponent_note"] and res["me"]["player_games"] == 2


def test_tools_registered():
    import anyio

    from fbm.mcp_server import server

    names = {t.name for t in anyio.run(server.list_tools)}
    assert {"team_roster", "next_week"} <= names
