"""Opponent intel: manager profiles from league transactions, history sync, and the engine feeds."""

import sqlite3
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from fbm.analytics import opponents as op
from fbm.analytics.waivers import Competitor, decide
from fbm.gm import services as svc
from fbm.league import tx_history
from fbm.store import db
from fbm.yahoo import xmlparse
from fbm.yahoo.league_reads import Transaction, TxPlayer
from tests.test_yahoo_league import NS

A, B, Z = "466.l.1.t.1", "466.l.1.t.2", "466.l.1.t.3"
TEAMS = {A: "Alpha", B: "Bravo", Z: "Zulu"}
NOW = datetime(2026, 12, 1, 12, tzinfo=UTC)


def mv(pid: str, action: str, team: str, source: str = "freeagents") -> TxPlayer:
    if action == "add":
        return TxPlayer(f"p.{pid}", pid, f"P{pid}", "BOS", "add", source, "", "team", team)
    return TxPlayer(f"p.{pid}", pid, f"P{pid}", "BOS", "drop", "team", team, "waivers", "")


def tx(key: str, days_ago: float, *players: TxPlayer, type_: str = "add/drop", **kw) -> Transaction:
    return Transaction(key, type_, kw.pop("status", "successful"), NOW - timedelta(days=days_ago), players, **kw)


def history() -> list[Transaction]:
    return [
        tx("1", 27, mv("1", "add", A, "waivers"), mv("9", "drop", A)),
        tx("2", 26, mv("2", "add", A), mv("1", "drop", A)),  # 1 added 27d ago, dropped 1 day later: a stream
        tx("3", 10, mv("3", "add", A, "waivers")),
        tx("4", 5, mv("4", "add", A), mv("2", "drop", A)),  # 2 kept 21 days: not a stream
        tx("5", 3, mv("5", "add", A)),
        tx("6", 2, mv("6", "add", A), mv("5", "drop", A)),  # stream
        tx("t1", 4, mv("7", "trade", B), type_="trade", trader_team_key=A, tradee_team_key=B),
        tx("t2", 1, type_="trade", status="rejected", trader_team_key=B, tradee_team_key=A),
        tx("b1", 20, mv("8", "add", B)),
    ]


def test_profile_numbers():
    p = op.profile_teams(history(), TEAMS, NOW, NOW - timedelta(days=28))
    a, b, z = p[A], p[B], p[Z]
    assert (a.adds, a.waiver_claims, a.fa_adds, a.drops, a.streams) == (6, 2, 4, 4, 2)
    assert a.adds_per_week == 1.5 and a.recent_adds_per_week == 2.0  # 4 adds in the last 14 days
    assert a.streaming_propensity == 0.33
    assert a.claim_activity == 0.5  # claims in week 0 and week 2 of 4
    assert (a.trades_completed, a.trades_proposed) == (1, 1)
    assert (b.trades_completed, b.trades_proposed) == (1, 1)
    assert a.tier == "active" and a.confidence == "medium" and a.has_history and a.typical_adds() == 2
    assert b.tier == "casual" and b.confidence == "low" and not b.has_history
    assert z.tier == "inactive" and z.confidence == "none" and z.last_activity is None


def test_demo_fallback_no_history():
    p = op.profile_teams([], TEAMS, NOW, builds={A: {"PTS": 0.7, "AST": 0.2, "REB": 0.35}})
    assert all(x.tier == "unknown" and not x.has_history for x in p.values())
    assert op.claim_activity(p) == {}
    assert (p[A].strong_cats, p[A].weak_cats, p[A].likely_punts) == (["PTS"], ["REB"], ["AST"])


def test_partner_ranking_and_fit():
    mine = {"PTS": 0.3, "AST": 0.7}
    builds = {A: mine, B: {"PTS": 0.8, "AST": 0.2}, Z: {"PTS": 0.6, "AST": 0.4}}
    p = op.profile_teams(history(), TEAMS, NOW, NOW - timedelta(days=28), builds)
    assert op.complementary_fit(mine, builds[B]) == round(0.3 * 0.2 + 0.2 * 0.3, 3)
    ranked = [x.team_key for x, _ in op.rank_partners(p, A, mine)]
    assert ranked == [B, Z]  # casual Bravo before inactive Zulu
    p[B].tier = "inactive"
    assert [x.team_key for x, _ in op.rank_partners(p, A, mine)] == [B, Z]  # same group -> by fit


def test_waiver_decide_uses_per_team_activity():
    comps = [Competitor(B, "Bravo", 1, 1.0)]
    default = decide(0.1, True, 5, 12, comps)
    profiled = decide(0.1, True, 5, 12, comps, {B: 0.05})
    assert default.p_claimed_by_other == 0.5 and round(profiled.p_claimed_by_other, 6) == 0.05
    assert profiled.action == "wait" and default.action == "pass"


def test_opponent_adds_feed(monkeypatch):
    league = SimpleNamespace(
        teams=[SimpleNamespace(team_key=k, name=v) for k, v in TEAMS.items()], max_weekly_adds=None, adds_used={}
    )
    monkeypatch.setattr(svc, "_tx_history", lambda: [])
    adds, source = svc._opponent_adds(league, league.teams[0], None)
    assert source.startswith("constant") and adds == svc.C("sim.opponent_stream_adds")
    monkeypatch.setattr(svc, "_tx_history", history)
    monkeypatch.setattr(svc, "datetime", SimpleNamespace(now=lambda tz=None: NOW))
    adds, source = svc._opponent_adds(league, league.teams[0], None)
    assert adds == 2 and source.startswith("manager profile")
    adds, source = svc._opponent_adds(league, league.teams[1], None)  # Bravo: too little history
    assert source.startswith("constant")
    league.max_weekly_adds, league.adds_used = 4, {A: 3}
    assert svc._opponent_adds(league, league.teams[0], None)[0] == 1  # capped by adds left
    assert svc._opponent_adds(league, league.teams[0], 5)[1] == "caller"


def page_xml(keys: list[str]) -> str:
    body = "".join(
        f"<transaction><transaction_key>{k}</transaction_key><type>add</type><status>successful</status>"
        f"<timestamp>{1793000000 + i}</timestamp><players count='1'><player><player_key>466.p.{i}</player_key>"
        f"<player_id>{i}</player_id><name><full>P {i}</full></name><editorial_team_abbr>BOS</editorial_team_abbr>"
        f"<transaction_data><type>add</type><source_type>freeagents</source_type><destination_type>team"
        f"</destination_type><destination_team_key>{A}</destination_team_key></transaction_data></player></players>"
        "</transaction>"
        for i, k in enumerate(keys)
    )
    return (
        f'<?xml version="1.0"?><fantasy_content {NS}><league><league_key>466.l.1</league_key>'
        f"<transactions>{body}</transactions></league></fantasy_content>"
    )


class PagedClient:
    def __init__(self, keys: list[str], size: int):
        self.keys, self.size, self.paths = keys, size, []

    def get(self, path: str):
        self.paths.append(path)
        start = int(path.split("start=")[1].split(";")[0]) if "start=" in path else 0
        return xmlparse.parse(page_xml(self.keys[start : start + self.size]))


def test_incremental_sync_is_idempotent(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.migrate(conn)
    monkeypatch.setattr(tx_history, "C", lambda k: {"intel.sync_page_size": 2, "intel.sync_max_pages": 10}[k])
    keys = [f"466.l.1.tr.{n}" for n in range(5, 0, -1)]  # newest first
    first = tx_history.sync(conn, PagedClient(keys, 2), "466.l.1")
    assert (first["new"], first["total_stored"], first["pages"], first["complete"]) == (5, 5, 3, True)
    again = PagedClient(keys, 2)
    second = tx_history.sync(conn, again, "466.l.1")
    assert second["new"] == 0 and second["pages"] == 1 and len(again.paths) == 1
    third = tx_history.sync(conn, PagedClient(["466.l.1.tr.7", "466.l.1.tr.6", *keys], 2), "466.l.1")
    assert third["new"] == 2 and third["total_stored"] == 7 and third["pages"] == 2
    loaded = tx_history.load(conn)
    assert len(loaded) == 7 and loaded[0].players[0].destination_team_key == A


def test_upsert_section_preserves_owner_notes():
    text = "# Bravo\n\n## Owner notes\nLoves big men.\n"
    once = op.upsert_section(text, "- Activity: casual")
    twice = op.upsert_section(once.replace("Loves", "Really loves"), "- Activity: active")
    assert "Really loves big men." in twice and "- Activity: active" in twice and "casual" not in twice
    assert twice.count(op.SECTION) == 1
