"""Box-score tools: game log, fantasy-week breakdown, and current-vs-prior season role split."""

from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

from fbm.analytics import boxscores as box
from fbm.analytics.valuation import ValuationConfig, value_players
from fbm.gm import boxscore_tools
from fbm.gm import services as svc
from fbm.league.model import FantasyTeam, RosterPlayer
from fbm.store import db

A, B = "100", "200"


def add_game(conn, event_id, et_date, home, away, season=2027, season_type=2):
    conn.execute(
        "INSERT INTO games(event_id, season, season_type, start_utc, et_date, home, away, state, completed, neutral, "
        "time_valid, box_synced, updated_at) VALUES (?,?,?,?,?,?,?,?,1,0,1,1,'')",
        (event_id, season, season_type, et_date + "T23:00Z", et_date, home, away, "post"),
    )


def add_line(conn, event_id, pid, et_date, team, season=2027, starter=1, played=1, **stats):
    s = {"minutes": 30, "pts": 20, "reb": 5, "ast": 5, "stl": 1, "blk": 1, "tov": 2, "tpm": 2}
    s |= {"fgm": 8, "fga": 16, "ftm": 2, "fta": 2} | stats
    conn.execute(
        "INSERT INTO box_lines(event_id, espn_id, et_date, season, team, starter, played, dnp_reason, minutes, pts, "
        "fgm, fga, tpm, tpa, ftm, fta, reb, ast, tov, stl, blk) VALUES (?,?,?,?,?,?,?,'',?,?,?,?,?,0,?,?,?,?,?,?,?)",
        (event_id, pid, et_date, season, team, starter, played, s["minutes"], s["pts"], s["fgm"], s["fga"], s["tpm"],
         s["ftm"], s["fta"], s["reb"], s["ast"], s["tov"], s["stl"], s["blk"]),
    )  # fmt: skip


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    add_game(c, "g1", "2026-04-10", "BOS", "NYK", season=2026)
    add_game(c, "g2", "2026-10-20", "NYK", "BOS")
    add_game(c, "g3", "2026-10-21", "BOS", "MIA")
    add_game(c, "g4", "2026-10-22", "MIA", "BOS", season_type=1)  # preseason-type row
    add_line(c, "g1", A, "2026-04-10", "BOS", season=2026, minutes=36, starter=1, fga=20, fta=4, tov=3)
    add_line(c, "g2", A, "2026-10-20", "BOS", minutes=24, starter=0, fga=10, fta=2, tov=1)
    add_line(c, "g3", A, "2026-10-21", "BOS", minutes=28, starter=0, fga=12, fta=2, tov=2)
    add_line(
        c, "g2", B, "2026-10-20", "NYK", pts=4, fgm=1, fga=9, ftm=0, fta=0, tov=5, reb=1, ast=0, stl=0, blk=0, tpm=0
    )
    add_line(c, "g3", B, "2026-10-21", "BOS", starter=1, played=0, minutes=0)  # DNP
    return c


def rows_for(conn, pid, **kw):
    return box.recent_lines(conn, pid, kw.pop("last_n", 10), kw.pop("season", None))


def test_recent_lines_newest_first_with_opponent_and_home(conn):
    lines = rows_for(conn, A)
    rows = box.game_log(lines)
    assert [r["date_et"] for r in rows] == ["2026-10-21", "2026-10-20", "2026-04-10"]
    assert rows[0]["opp"] == "MIA" and rows[0]["home"] is True  # BOS hosted MIA
    assert rows[1]["opp"] == "NYK" and rows[1]["home"] is False
    assert rows[2]["season"] == 2026 and rows[2]["started"] is True and rows[1]["started"] is False
    assert rows[0]["fg"] == "8/12" and rows[0]["ft"] == "2/2" and rows[0]["3pm"] == 2
    assert box.recent_lines(conn, A, 2)[1]["et_date"] == "2026-10-20"
    assert len(rows_for(conn, A, season=2027)) == 2
    assert rows_for(conn, B)[0]["et_date"] == "2026-10-20"  # DNP line excluded


def test_game_log_labels_non_regular_games_and_scores_games(conn):
    add_line(conn, "g4", A, "2026-10-22", "BOS", season=2027)
    top = box.game_log(rows_for(conn, A, last_n=1))[0]
    assert top["type"] == "preseason"
    pool = pool_cfg()
    rows = box.game_log(rows_for(conn, A), *pool)
    assert all({"g_total", "z_total"} <= r.keys() for r in rows)
    big = box.game_scores([{**dict.fromkeys(box.BOX_COLS, 0.0), "pts": 50, "minutes": 40, "fgm": 20, "fga": 25}], *pool)
    bad = box.game_scores([{**dict.fromkeys(box.BOX_COLS, 0.0), "tov": 10, "minutes": 30, "fgm": 1, "fga": 12}], *pool)
    assert big[0]["g"] > 0 > bad[0]["g"]
    assert box.game_scores(rows_for(conn, A), None, None) is None


def pool_cfg():
    rng = list(range(12))
    spec = {  # stat -> (base, per-player step, modulus)
        "minutes": (20.0, 1.0, 0), "pts": (8.0, 2.0, 0), "reb": (3.0, 1.0, 5), "ast": (2.0, 1.0, 4),
        "stl": (0.5, 0.3, 3), "blk": (0.3, 0.3, 4), "tov": (1.0, 0.2, 0), "tpm": (0.5, 0.2, 0),
        "fgm": (3.0, 1.0, 0), "fga": (7.0, 1.8, 0), "ftm": (1.0, 0.3, 0), "fta": (1.4, 0.4, 0),
    }  # fmt: skip
    table = pd.DataFrame(
        {stat: [base + step * (i % mod if mod else i) for i in rng] for stat, (base, step, mod) in spec.items()},
        index=[str(i) for i in rng],
    )
    table["gp"], table["tpa"] = 50.0, 4.0
    cfg = ValuationConfig(pool_size=8, roster_size=4, min_gp=1, min_minutes=1.0)
    _, stats = value_players(table, cfg)
    return stats, cfg


def test_week_box_totals_shares_and_effect(conn):
    lines = box.week_lines(conn, [A, B], date(2026, 10, 19), date(2026, 10, 25))
    assert [r["et_date"] for r in lines[A]] == ["2026-10-20", "2026-10-21"]  # prior season + preseason-type excluded
    assert len(lines[B]) == 1  # DNP not counted
    players = [
        {"player": "Aaa", "id": A, "slot": "PG"},
        {"player": "Bbb", "id": B, "slot": "C"},
        {"player": "Ccc", "id": "9"},
    ]
    out = box.week_box(players, {**lines, "9": []}, *pool_cfg())
    by = {p["player"]: p for p in out["players"]}
    assert by["Aaa"]["games"] == 2 and by["Aaa"]["totals"]["pts"] == 40 and by["Aaa"]["totals"]["fg"] == "16/22"
    assert by["Ccc"] == {"player": "Ccc", "id": "9", "games": 0}
    assert out["team_total"]["totals"]["PTS"] == 44 and out["team_total"]["totals"]["FG%"]["att"] == 31
    assert by["Aaa"]["cats"]["PTS"]["share"] == round(40 / 44, 3)
    assert by["Aaa"]["cats"]["FG%"]["pct_effect_pp"] > 0 > by["Bbb"]["cats"]["FG%"]["pct_effect_pp"]
    assert out["players"][0]["player"] == "Aaa" and out["carried"] == ["Aaa"] and out["sank"] == ["Bbb"]
    assert box.compare_banked({"PTS": 44, "REB": 11}, {"pts": 40.0, "reb": 11.0}, box.NINE_CAT) == {
        "PTS": 4.0,
        "REB": 0.0,
    }


def test_week_box_without_pool_has_no_scores(conn):
    lines = box.week_lines(conn, [A], date(2026, 10, 19), date(2026, 10, 25))
    out = box.week_box([{"player": "Aaa", "id": A}], lines)
    assert "g_sum" not in out["players"][0] and out["carried"] == []


def test_season_role_split_and_delta(conn):
    roles = box.season_roles(conn, [A, B, "none"], [2027, 2026])
    cur, prior = roles[A][2027], roles[A][2026]
    assert cur["gp"] == 2 and cur["mpg"] == 26.0 and cur["starts"] == 0 and cur["start_rate"] == 0.0
    assert cur["usage_per36"] == round((22 + 0.44 * 4 + 3) / 52 * 36, 1)
    assert prior["mpg"] == 36.0 and prior["starts"] == 1
    role = box.season_role(roles[A], 2027, 2026)
    assert role["delta"]["mpg"] == -10.0 and role["delta"]["start_rate"] == -1.0
    assert "small sample" in role["note"]
    assert box.compact_role(role) == {
        "gp": 2, "mpg": 26.0, "starts": 0, "usage_per36": cur["usage_per36"], "prior_mpg": 36.0,
        "d_mpg": -10.0, "d_usage": role["delta"]["usage_per36"],
    }  # fmt: skip
    nobody = box.season_role(roles["none"], 2027, 2026)
    assert nobody["current"] == {"gp": 0} and nobody["prior"] is None and nobody["delta"] == {}
    assert box.role_stats(0, 0, 0, 0, 0, 0) == {"gp": 0}


# ---------------------------------------------------------------- services


def fake_context():
    pool, cfg = pool_cfg()
    projections = pd.DataFrame({"name": ["Aaa Player", "Bbb Player"], "team": ["BOS", "NYK"]}, index=[A, B])
    return SimpleNamespace(
        pool=pool, cfg=cfg, projections=projections, season=2027, prior=2026, values=pd.DataFrame(index=[A, B])
    )


def test_game_log_service(conn, monkeypatch):
    monkeypatch.setattr(svc, "_conn", lambda: conn)
    monkeypatch.setattr(svc, "ctx", fake_context)
    out = boxscore_tools.game_log("Aaa Player", last_n=2)
    assert out["player"] == "Aaa Player" and out["nba"] == "BOS" and out["games_returned"] == 2
    assert out["seasons_included"] == [2027] and out["games"][0]["g_total"] is not None
    assert out["avg_returned"]["min"] == 26.0
    both = boxscore_tools.game_log(A, last_n=10)
    assert both["seasons_included"] == [2026, 2027]
    assert boxscore_tools.game_log("Aaa Player", season=2026)["games_returned"] == 1


def test_week_box_service(conn, monkeypatch):
    mine = FantasyTeam("t1", "Mine", players=[RosterPlayer(A, "Aaa Player", "BOS", ("PG",), "PG")])
    opp = FantasyTeam("t2", "Opp", players=[RosterPlayer(B, "Bbb Player", "NYK", ("C",), "C")])
    league = SimpleNamespace(
        my_team=mine, my_team_key="t1", teams=[mine, opp], opponent_key="t2", week=3, banked={"t1": {"pts": 40.0}}
    )
    monkeypatch.setattr(svc, "_conn", lambda: conn)
    monkeypatch.setattr(svc, "ctx", fake_context)
    monkeypatch.setattr(svc, "active_league", lambda: league)
    monkeypatch.setattr(svc, "_week_window", lambda: (date(2026, 10, 22), date(2026, 10, 19), date(2026, 10, 25)))
    monkeypatch.setattr(svc, "_default_opponent", lambda lg: opp)
    monkeypatch.setattr(svc, "_team", lambda lg, q: opp)
    monkeypatch.setattr(boxscore_tools, "nba_today", lambda: date(2026, 10, 22))
    out = boxscore_tools.week_box()
    assert out["window"] == ["2026-10-19", "2026-10-25"] and out["last_box_score_date"] == "2026-10-21"
    assert "current" in out["note"].lower()
    me, other = out["teams"]
    assert me["is_me"] and me["players"][0]["totals"]["pts"] == 40 and me["vs_yahoo_banked"] == {"PTS": 0.0}
    assert not other["is_me"] and other["players"][0]["games"] == 1 and "vs_yahoo_banked" not in other
    solo = boxscore_tools.week_box("Opp")
    assert [t["team"] for t in solo["teams"]] == ["Opp"]
    assert [t["team"] for t in boxscore_tools.week_box("me")["teams"]] == ["Mine"]


def test_season_roles_helper_in_services(conn, monkeypatch):
    monkeypatch.setattr(svc, "_conn", lambda: conn)
    out = svc._season_roles(fake_context(), [A])
    assert out[A]["current"]["gp"] == 2 and out[A]["prior"]["gp"] == 1


def test_tools_registered():
    import anyio

    from fbm.mcp_server import server

    async def names():
        return {t.name: t for t in await server.list_tools()}

    tools = anyio.run(names)
    assert {"game_log", "week_box"} <= tools.keys()
    assert set(tools["game_log"].input_schema["properties"]) == {"player", "last_n", "season"}
