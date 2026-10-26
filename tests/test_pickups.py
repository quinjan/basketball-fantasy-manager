"""Ownership history (percent-owned trends, in-league adds/drops) and the league-wide pickup radar."""

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest

from fbm import constants
from fbm.analytics import radar
from fbm.analytics.signals import Signal
from fbm.gm import ownership, pickups
from fbm.gm import signals as signal_feed
from fbm.league.model import FantasyTeam, LeagueState, RosterPlayer
from fbm.store import db
from fbm.yahoo.league_reads import Transaction, TxPlayer

T0 = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(days=10)  # inside retention


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def _league(ownership_map, rostered=(), fetched_at=T0, on_waivers=None):
    mine = FantasyTeam("t1", "Mine", players=[RosterPlayer("w1", "Worst", "AAA", ("PG", "UTIL"), "PG")])
    other = FantasyTeam("t2", "Other", players=[RosterPlayer(p, p, "BBB", ("PG", "UTIL")) for p in rostered])
    return LeagueState(
        "lk",
        "L",
        2027,
        "t1",
        [mine, other],
        ownership={k: list(v) for k, v in ownership_map.items()},
        fetched_at=fetched_at.isoformat(),
        on_waivers=on_waivers or {},
    )


# ------------------------------------------------------------------ history & trends


def test_record_dedupes_unchanged_values_and_tracks_owner(conn):
    assert ownership.record(conn, _league({"a": (10.0, 0.0), "b": (5.0, 0.0)})) == 2
    # same values 1h later: skipped (inside record_gap_hours); same snapshot again: skipped
    assert ownership.record(conn, _league({"a": (10.0, 0.0), "b": (5.0, 0.0)}, fetched_at=T0 + timedelta(hours=1))) == 0
    assert ownership.record(conn, _league({"a": (10.0, 0.0)}, fetched_at=T0)) == 0
    # a changes; b is now rostered by t2 (row with no percent); unchanged-but-old values are re-recorded
    wrote = ownership.record(conn, _league({"a": (14.0, 0.0)}, rostered=["b"], fetched_at=T0 + timedelta(hours=8)))
    assert wrote == 2
    hist = ownership.load_history(conn)
    assert [o.pct for o in hist["a"]] == [10.0, 14.0]
    assert hist["b"][-1].pct is None and hist["b"][-1].owner == "t2"
    # never-tracked rostered players are not recorded
    assert "w1" not in hist


def test_trend_windows_and_partial_history(conn):
    for hours, pct in ((0, 10.0), (24, 12.0), (48, 15.0), (72, 21.0)):
        ownership.record(conn, _league({"a": (pct, 0.0)}, fetched_at=T0 + timedelta(hours=hours)))
    obs = ownership.load_history(conn)["a"]
    now = T0 + timedelta(hours=72)
    one = ownership.trend(obs, 1, now)
    three = ownership.trend(obs, 3, now)
    ten = ownership.trend(obs, 10, now)
    assert (one.current, one.delta, one.partial) == (21.0, 6.0, False)
    assert (three.delta, three.span_days, three.partial) == (11.0, 3.0, False)
    assert ten.delta == 11.0 and ten.partial  # only 3 days of history for a 10-day window


def test_trend_needs_min_span_and_freezes_when_rostered(conn):
    ownership.record(conn, _league({"a": (10.0, 0.0)}, fetched_at=T0))
    ownership.record(conn, _league({"a": (11.0, 0.0)}, fetched_at=T0 + timedelta(hours=2)))
    assert ownership.trend(ownership.load_history(conn)["a"], 1, T0 + timedelta(hours=2)).delta is None
    ownership.record(conn, _league({"a": (18.0, 0.0)}, fetched_at=T0 + timedelta(hours=24)))
    ownership.record(conn, _league({}, rostered=["a"], fetched_at=T0 + timedelta(hours=30)))
    t = ownership.trend(ownership.load_history(conn)["a"], 3, T0 + timedelta(hours=90))
    assert t.current == 18.0 and t.delta == 8.0 and t.owner == "t2"  # window ends the day he was taken


def test_annotate_prefers_history_and_falls_back_to_yahoo(conn):
    league = _league({"a": (15.0, 0.0), "b": (9.0, 2.0)}, fetched_at=T0 + timedelta(days=3))
    for days, pct in ((0, 10.0), (2, 11.0), (3, 15.0)):
        ownership.record(conn, _league({"a": (pct, 0.0)}, fetched_at=T0 + timedelta(days=days)))
    rows = [{"id": "a"}, {"id": "b"}, {"id": "zzz"}]
    ownership.annotate(conn, rows, league)
    a, b, z = rows
    assert (a["pct_owned"], a["pct_owned_delta"], a["pct_owned_delta_basis"]) == (15.0, 4.0, "history_1d")
    assert a["pct_owned_delta_trend"] == 5.0
    assert (b["pct_owned_delta"], b["pct_owned_delta_basis"], b["pct_owned_delta_trend"]) == (2.0, "yahoo_week", None)
    assert "pct_owned" not in z


def test_annotate_survives_a_missing_database():
    rows = [{"id": "a"}]
    ownership.annotate(None, rows, _league({"a": (7.0, 1.5)}))
    assert rows[0]["pct_owned"] == 7.0 and rows[0]["pct_owned_delta"] == 1.5


# ------------------------------------------------------------------ in-league adds / drops


def _tx(key, when, kind, players):
    return Transaction(key, kind, "successful", when, tuple(players))


def test_recent_moves_lists_adds_and_drops_by_team():
    now = T0
    add = TxPlayer("k1", "11", "Riser", "AAA", "add", "freeagents", "", "team", "t2")
    drop = TxPlayer("k2", "12", "Cut", "BBB", "drop", "team", "t2", "waivers", "")
    old = TxPlayer("k3", "13", "Old", "CCC", "add", "waivers", "", "team", "t1")
    txs = [
        _tx("x1", now - timedelta(hours=3), "add/drop", [add, drop]),
        _tx("x2", now - timedelta(days=9), "add", [old]),
        Transaction("x3", "trade", "successful", now, ()),
        Transaction("x4", "add", "pending", now, (add,)),
    ]
    moves = ownership.recent_moves(txs, {"t2": "Other"}, now, 3, {"11": "e11"})
    assert [(m["action"], m["player"], m["team"], m["id"], m["via"]) for m in moves] == [
        ("add", "Riser", "Other", "e11", "freeagents"),
        ("drop", "Cut", "Other", None, "waivers"),
    ]
    summary = ownership.summarize_moves(moves)
    assert summary["by_team"] == {"Other": {"adds": 1, "drops": 1}}


# ------------------------------------------------------------------ radar scoring


def test_radar_score_levels():
    quiet = radar.score(radar.RadarInput())
    assert quiet.score == 0 and quiet.confidence == "low" and not quiet.reasons
    sig = {"kind": "opportunity", "direction": "up", "confidence": "high", "note": "X is out"}
    alone = radar.score(radar.RadarInput(signals=[sig]))
    assert alone.score == 3.0 and alone.confidence == "medium"
    full = radar.score(
        radar.RadarInput(
            signals=[sig, {"kind": "role_change", "direction": "up", "confidence": "low", "note": "starts"}],
            recent_mpg=24.0,
            minutes_trend=4.5,
            owned_delta=6.0,
            owned_pct=30.0,
            ros_gain=1.2,
            worst_name="Worst",
        )
    )
    assert full.score == 3.0 + 0.5 + 1.5 + 1.5 + 2.0 and full.confidence == "high"
    assert len(full.reasons) == 5
    thin = radar.score(radar.RadarInput(recent_mpg=10.0, minutes_trend=5.0))  # end-of-bench jump ignored
    assert thin.score == 0
    lost = {"kind": "role_change", "direction": "down", "confidence": "high", "note": "lost job"}
    down = radar.score(radar.RadarInput(signals=[lost]))
    assert down.score == 0 and down.cautions


def test_radar_constants_registered():
    for key in (
        "ownership.delta_days",
        "ownership.trend_days",
        "radar.points_high",
        "radar.min_ros_gain",
        "radar.high_score",
        "radar.medium_score",
    ):
        assert key in constants.REGISTRY


# ------------------------------------------------------------------ fa_radar / ownership_trends orchestration


class FakeSvc:
    class GMError(RuntimeError):
        pass

    def __init__(self, conn, league):
        self.conn, self.league = conn, league
        index = ["w1", "up", "rise", "meh", "gone"]
        self.c = SimpleNamespace(
            values=pd.DataFrame(
                {
                    "name": ["Worst", "Upside", "Riser", "Meh", "Gone"],
                    "team": ["AAA", "BBB", "BBB", "CCC", "DDD"],
                    "position": ["PG"] * 5,
                },
                index=index,
            ),
            projections=pd.DataFrame(
                {
                    "name": ["Worst", "Upside", "Riser", "Meh", "Gone"],
                    "team": ["AAA", "BBB", "BBB", "CCC", "DDD"],
                    "minutes_trend": [0.0, 0.0, 5.0, 0.0, 0.0],
                    "recent_mpg": [20.0, 20.0, 26.0, 20.0, 20.0],
                },
                index=index,
            ),
        )
        self.ros = pd.Series({"w1": -2.0, "up": 0.0, "rise": 0.5, "meh": -1.9, "gone": 3.0})

    def active_league(self):
        return self.league

    def ctx(self):
        return self.c

    def _conn(self):
        return self.conn

    def _owners(self, league):
        return {p.player_id: t for t in league.teams for p in t.players}

    def _strategy_punts(self, punts):
        return ()

    def _ros_value(self, c, punts):
        return self.ros

    def _week_window(self):
        return date(2026, 11, 2), date(2026, 11, 2), date(2026, 11, 8)

    def _teamless(self, c):
        return frozenset({"gone"})

    def _r(self, x, nd=2):
        return round(float(x), nd)

    def _role(self, c, pid):
        return None

    def _injury(self, pid):
        return None


@pytest.fixture
def fake(conn, monkeypatch):
    league = _league({"up": (20.0, 0.0), "rise": (10.0, 0.0), "meh": (3.0, 0.0)}, on_waivers={"rise": "2026-11-03"})
    svc = FakeSvc(conn, league)
    monkeypatch.setattr(pickups, "_svc", lambda: svc)
    monkeypatch.setattr(pickups, "_games_left", lambda start, end: {"AAA": 2, "BBB": 4, "CCC": 3})
    return svc


def test_fa_radar_ranks_signals_minutes_ownership_and_flags_waivers(conn, fake):
    signal_feed.record(
        conn,
        2027,
        [Signal("opportunity", "up", "up", "Upside", "BBB", 3.0, "high", ["teammate_out"], "Star is out", {})],
    )
    for hours, pct in ((0, 4.0), (30, 10.0)):  # "rise" doubles+ its ownership
        ownership.record(conn, _league({"rise": (pct, 0.0)}, fetched_at=T0 + timedelta(hours=hours)))
    out = pickups.fa_radar(top=5, min_confidence="low")
    names = [p["player"] for p in out["players"]]
    assert "Gone" not in names and "Worst" not in names  # teamless and rostered players are skipped
    assert out["my_worst_rostered"]["player"] == "Worst" and out["scanned_free_agents"] == 3
    top = {p["player"]: p for p in out["players"]}
    rise, up = top["Riser"], top["Upside"]
    assert rise["owner_status"] == "on waivers (clears 2026-11-03 ET)"
    assert rise["confirm_with"].startswith("waiver_decision(") and up["confirm_with"].startswith("evaluate_add_drop(")
    assert rise["suggested_drop"]["player"] == "Worst"
    assert any(r.startswith("minutes up") for r in rise["reasons"])
    assert any(r.startswith("ROS value") for r in rise["reasons"])
    assert rise["ros_gain_vs_worst"] == 2.5 and up["ros_gain_vs_worst"] == 2.0
    assert up["reasons"][0].startswith("opportunity (high)")
    # defaults to medium: the value-only 'Meh' (gain 0.1 < min) is not listed
    medium = pickups.fa_radar()
    assert all(p["confidence"] in ("high", "medium") for p in medium["players"])
    assert "Meh" not in [p["player"] for p in medium["players"]]
    with pytest.raises(FakeSvc.GMError):
        pickups.fa_radar(min_confidence="bogus")


def test_radar_summary_is_compact(conn, fake):
    signal_feed.record(
        conn, 2027, [Signal("opportunity", "up", "up", "Upside", "BBB", 3.0, "high", [], "Star is out", {})]
    )
    rows = pickups.radar_summary()
    assert rows and set(rows[0]) == {"player", "nba", "confidence", "owner_status", "ros_gain_vs_worst", "reasons"}


def test_ownership_trends_lists_risers_fallers_and_thin_history_note(conn, fake):
    out = pickups.ownership_trends(window_days=1)
    assert out["risers"] == [] and any("History is thin" in n for n in out["notes"])
    for hours, a, b in ((0, 10.0, 30.0), (30, 16.0, 22.0)):
        ownership.record(conn, _league({"up": (a, 0.0), "rise": (b, 0.0)}, fetched_at=T0 + timedelta(hours=hours)))
    out = pickups.ownership_trends(window_days=1, top=5)
    assert [r["player"] for r in out["risers"]] == ["Upside"] and out["risers"][0]["pct_owned_delta"] == 6.0
    assert [r["player"] for r in out["fallers"]] == ["Riser"] and out["fallers"][0]["pct_owned_delta"] == -8.0
    assert out["history"]["snapshots"] == 2
    assert out["league_moves"]["available"] and out["league_moves"]["adds"] == []
    with pytest.raises(FakeSvc.GMError):
        pickups.ownership_trends(scope="nope")
