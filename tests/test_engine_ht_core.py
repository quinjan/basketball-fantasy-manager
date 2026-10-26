"""HT-mode engine: context from Hashtag projections, official odds, the suggestion gate, decision logging, import."""

import sys
import types
from datetime import date

import pytest

from fbm.analytics import ht_odds
from fbm.analytics import players as player_tables
from fbm.analytics.context import build_context
from fbm.gm import choosers, decisions, engine_ht, policy
from fbm.store import db

Z = ("z_fg_pct", "z_ft_pct", "z_tpm", "z_pts", "z_reb", "z_ast", "z_stl", "z_blk", "z_tov")


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def _ht_row(conn, view, pid, pts, rank, as_of="2026-11-02"):
    stats = dict(minutes=30.0, fgm=7.0, fga=15.0, ftm=3.0, fta=4.0, tpm=2.0, pts=pts, reb=6.0, ast=4.0, stl=1.0,
                 blk=0.5, tov=2.0)  # fmt: skip
    cols = ["as_of", "view", "ht_id", "espn_id", "name", "team", "positions", "rank", "gp", *stats, *Z, "total",
            "fetched_at"]  # fmt: skip
    vals = [as_of, view, f"h{pid}", pid, f"Player {pid}", "DEN", "PG,SG", rank, 70.0, *stats.values()]
    vals += [0.1 * rank] * len(Z) + [1.0, "2026-11-02T10:00:00"]
    conn.execute(f"INSERT INTO hashtag_projections({', '.join(cols)}) VALUES ({','.join('?' * len(cols))})", vals)


def _seed(conn, n=30):
    for i in range(n):
        _ht_row(conn, "ROS", f"p{i}", 10.0 + i, i + 1)
        _ht_row(conn, "STREAM7", f"p{i}", 20.0 + i, i + 1)
    conn.commit()


def test_hashtag_projection_table_shape(conn):
    _seed(conn, 3)
    t = player_tables.hashtag_projection_table(conn, "STREAM7", 2027)
    assert t.loc["p1", "pts"] == 21.0 and t.loc["p1", "position"] == "PG"
    assert t.loc["p1", "tpa"] > t.loc["p1", "tpm"]
    assert {"gp_current", "gp_prior", "ht_z_pts", "minutes_trend"} <= set(t.columns)
    assert t.attrs["view"] == "STREAM7"


def test_build_context_hashtag_mode_uses_ht_numbers(conn):
    _seed(conn)
    c = build_context(conn, date(2026, 11, 2), hashtag=True)
    assert c.hashtag and c.ht_as_of == "2026-11-02"
    assert c.projections.loc["p5", "pts"] == 15.0  # ROS view
    assert c.view_projections("STREAM7").loc["p5", "pts"] == 25.0
    assert set(c.view_g("STREAM7").columns) >= {"g_pts", "g_tov"}
    assert len(c.values) == 30 and c.pool is not None and c.statuses() == {}


def test_build_context_hashtag_mode_requires_import(conn):
    with pytest.raises(RuntimeError, match="Hashtag"):
        build_context(conn, date(2026, 11, 2), hashtag=True)


def test_odds_from_totals_normal_and_turnovers():
    means = {"pts": (500.0, 480.0), "tov": (60.0, 50.0), "fg_pct": (0.47, 0.47)}
    sd = {"pts": 20.0, "tov": 10.0, "fg_pct": 0.02}
    p = ht_odds.odds_from_totals(means, sd, 1.0, ["pts", "tov", "fg_pct"])
    assert p["pts"] == pytest.approx(0.8413, abs=1e-3)
    assert p["tov"] == pytest.approx(0.1587, abs=1e-3)  # more turnovers is worse
    assert p["fg_pct"] == pytest.approx(0.5)
    wider = ht_odds.odds_from_totals(means, sd, 2.0, ["pts"])
    assert 0.5 < wider["pts"] < p["pts"]
    s = ht_odds.summary({"a": 1.0, "b": 1.0, "c": 0.0})
    assert s["expected"] == 2.0 and s["p_majority"] == 1.0


def test_shift_opponent_reprices_official_odds():
    means = {"pts": (500.0, 480.0), "tov": (60.0, 60.0)}
    shifted = ht_odds.shift_opponent(means, {"pts": 20.0, "tov": 10.0})
    assert shifted == {"pts": (500.0, 500.0), "tov": (60.0, 70.0)}
    assert ht_odds.shift_opponent(means, {}) == means
    p = ht_odds.odds_from_totals(shifted, {"pts": 20.0, "tov": 10.0}, 1.0, ["pts", "tov"])
    assert p["pts"] == pytest.approx(0.5)
    assert p["tov"] == pytest.approx(0.8413, abs=1e-3)  # their extra turnovers help me


def test_payload_keeps_unscaled_sd():
    pl = ht_odds.payload({"pts": 0.6}, {"pts": (1.0, 0.5)}, {"pts": 3.0}, 1.3, "STREAM7")
    assert pl["sd"] == {"pts": 3.0} and pl["spread_scale"] == 1.3 and pl["method"] == "ht_totals+sim_spread"
    assert pl["categories"]["pts"] == {"p_win": 0.6, "p_tie": 0.0}


def test_gate_blocks_suggestions_when_unhealthy(conn, monkeypatch):
    monkeypatch.setattr(engine_ht, "enabled", lambda: True)
    payload = engine_ht.gate(conn)
    assert payload["error_code"] == "HT_UNHEALTHY" and payload["reasons"]

    @engine_ht.gated
    def suggest():
        return {"ok": True}

    monkeypatch.setattr(db, "connect", lambda *a, **k: conn)
    assert suggest()["error_code"] == "HT_UNHEALTHY"
    monkeypatch.setattr(engine_ht, "enabled", lambda: False)
    assert suggest() == {"ok": True}


def test_decide_logs_options_and_choosers_pick(conn):
    sit = engine_ht.situation(
        odds={"pts": 0.5, "reb": 0.9}, punts=[], day=date(2026, 11, 2), adds_left=3, week=3, days_left=6,
        games_left={"me": 20, "opp": 18}, banked={}, opponent_key="t2", view_used="STREAM7", ht_as_of="2026-11-02",
        team_key="t1", starting_slots=["PG", "UTIL"],
    )  # fmt: skip
    options = [
        {"id": "view:STREAM7", "label": "a", "view": "STREAM7", "spec": {"date": "2026-11-02", "starters": ["x"]},
         "cat_scores": {"pts": 1.0, "reb": 0.0}},
        {"id": "view:ROS", "label": "b", "view": "ROS", "spec": {"date": "2026-11-02", "starters": ["y"]},
         "cat_scores": {"pts": 9.0, "reb": 0.0}},
        {"id": "ros", "label": "c", "spec": {"date": "2026-11-02", "starters": ["z"]},
         "cat_scores": {"pts": 0.0, "reb": 2.0}},
    ]  # fmt: skip
    rec, did = engine_ht.decide(
        conn, kind="lineup", trigger="lineup-guard", et_date="2026-11-02", situation=sit, options=options, week=3
    )
    row = decisions.get(conn, did)
    assert rec == choosers.choose(row, policy.all_values(conn)) == row["recommended"]
    assert rec != "view:ROS"  # other views are logged but not eligible under the lineup view
    assert row["trigger"] == "lineup-guard" and row["situation"]["team_key"] == "t1"
    assert [o["id"] for o in row["options"]] == ["view:STREAM7", "view:ROS", "ros"]


def test_record_ht_prediction(conn):
    pl = ht_odds.payload({"pts": 0.6}, {"pts": (1.0, 0.5)}, {"pts": 3.0}, 1.0, "STREAM7")
    pid = engine_ht.record_ht_prediction(conn, pl, (date(2026, 11, 2), date(2026, 11, 8)), "t1", "t2")
    row = conn.execute("SELECT source, payload FROM predictions WHERE id = ?", (pid,)).fetchone()
    assert row["source"] == "ht_spread" and '"means"' in row["payload"]


def test_refresh_runs_hashtag_importer(conn, monkeypatch):
    from fbm.gm import services as svc

    calls = {}
    fake = types.ModuleType("fbm.hashtag.importer")

    def run_import(c, *, league=None, weights=None, **kw):
        calls["import"] = weights
        return {"run_id": 1, "pages": {}, "healthy": True}

    def run_next_man_up(c, ids, **kw):
        calls["nmu"] = ids
        return len(ids)

    fake.run_import, fake.run_next_man_up = run_import, run_next_man_up
    monkeypatch.setitem(sys.modules, "fbm.hashtag.importer", fake)
    import fbm.hashtag

    monkeypatch.setattr(fbm.hashtag, "importer", fake, raising=False)
    monkeypatch.setattr(svc, "_conn", lambda: conn)
    monkeypatch.setattr(svc, "_default_opponent", lambda league: None)
    monkeypatch.setattr(svc, "_week_odds", lambda *a, **k: ({"pts": 0.5}, "ht_spread"))
    monkeypatch.setattr(svc, "_category_multipliers", lambda odds=None: {"combined": {"pts": 1.25}})
    import fbm.analytics.signals as sig

    monkeypatch.setattr(sig, "ht_absent_key_players", lambda c: [{"espn_id": "p9"}], raising=False)
    out = svc._ht_import(types.SimpleNamespace())
    assert calls["import"] == {"pts": 1.25} and out["healthy"] is True
    assert calls["nmu"] == ["p9"] and out["next_man_up"] == 1


def test_simulate_matchup_ht_gate_and_record_guard(monkeypatch):
    from types import SimpleNamespace

    from fbm.gm import services as svc

    league = SimpleNamespace(opponent_key="t2")
    monkeypatch.setattr(svc, "active_league", lambda: league)
    monkeypatch.setattr(svc, "_team", lambda lg, q: SimpleNamespace(team_key=q or "t2"))
    monkeypatch.setattr(svc, "ctx", lambda: SimpleNamespace(hashtag=True))
    monkeypatch.setattr(svc._ht, "gate", lambda conn=None: {"error_code": "HT_UNHEALTHY"})
    assert svc.simulate_matchup_tool(start="2026-11-02")["error_code"] == "HT_UNHEALTHY"
    monkeypatch.setattr(svc._ht, "gate", lambda conn=None: None)
    with pytest.raises(svc.GMError, match="start"):
        svc.simulate_matchup_tool(start="2026-11-02")
    with pytest.raises(svc.GMError, match="scheduled opponent"):
        svc.simulate_matchup_tool(opponent="t9", record=True)
