from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from fbm.gm import memory, steps
from fbm.gm import proposals as props
from fbm.league.model import FantasyTeam, LeagueState, RosterPlayer, SlotSpec
from fbm.notify import email as notify
from fbm.settings import Settings
from fbm.store import db


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def _proposal(conn, **kw):
    args = dict(
        kind="add_drop",
        title="Add X",
        summary="s",
        payload={"add": "x", "drop": "a"},
        rationale="tool says so",
        evidence={"delta": 1.2},
    )
    args.update(kw)
    return props.create(conn, **args)


def test_lifecycle_requires_review_then_owner_quote(conn):
    p = _proposal(conn)
    assert p.status == "pending_review" and p.ref == f"P-{p.id}"
    with pytest.raises(props.ProposalError):
        props.record_owner_decision(conn, p.ref, True, "yes")  # not reviewed yet
    with pytest.raises(props.ProposalError):
        props.ready_for_steps(conn, p.ref)
    with pytest.raises(props.ProposalError):  # same model family as GM
        props.record_review(conn, p.ref, "AGREE", "ok", "claude-sonnet-5", gm_model="claude-opus-5.5")
    p = props.record_review(conn, p.ref, "agree", "numbers check out", "gpt-5.6-sol", 0.8, "claude-opus-5.5")
    assert p.status == "pending_approval" and p.review_verdict == "AGREE"
    with pytest.raises(props.ProposalError):
        props.record_owner_decision(conn, p.ref, True, "   ")
    p = props.record_owner_decision(conn, p.ref, True, "yes do it")
    assert p.status == "approved" and props.ready_for_steps(conn, p.ref).id == p.id
    events = [r["event"] for r in conn.execute("SELECT event FROM proposal_events WHERE proposal_id=?", (p.id,))]
    assert events == ["created", "reviewed", "approved"]


def test_expired_proposals_get_no_steps(conn):
    p = _proposal(conn, expires_in_hours=1)
    props.record_review(conn, p.ref, "AGREE", "ok", "gpt-5.6-sol")
    props.record_owner_decision(conn, p.ref, True, "ok")
    past = (datetime.now(ZoneInfo("UTC")) - timedelta(minutes=1)).isoformat(timespec="seconds")
    conn.execute("UPDATE proposals SET expires_at=? WHERE id=?", (past, p.id))
    with pytest.raises(props.ProposalError, match="expired"):
        props.ready_for_steps(conn, p.ref)


def test_create_validation(conn):
    with pytest.raises(props.ProposalError):
        _proposal(conn, kind="yolo")
    with pytest.raises(props.ProposalError):
        _proposal(conn, rationale=" ")


def _league(tmp_path, monkeypatch) -> LeagueState:
    monkeypatch.setattr("fbm.league.demo.DEMO_PATH", tmp_path / "demo.json")
    me = FantasyTeam("t1", "Me", players=[RosterPlayer("a", "A", "BOS", ("PG", "UTIL"), "PG")])
    them = FantasyTeam("t2", "Them", players=[RosterPlayer("b", "B", "LAL", ("C", "UTIL"), "C")])
    return LeagueState(
        "demo", "Demo", 2027, "t1", [me, them], slots=(SlotSpec("PG", 1, True), SlotSpec("BN", 2, False)), source="demo"
    )


VALUES = pd.DataFrame({"name": ["X Free"], "team": ["MIA"], "position": ["F"]}, index=["x"])


def test_steps_never_change_the_league_until_owner_confirms(conn, tmp_path, monkeypatch):
    league = _league(tmp_path, monkeypatch)
    monkeypatch.setattr("fbm.gm.steps.save_league", lambda lg: None)
    p = _proposal(conn, payload={"add": "x", "add_name": "X Free", "drop": "a", "drop_name": "A"})
    assert steps.revalidate(p, league) == []
    result = steps.owner_steps(p, league)
    assert result.status == "awaiting_owner"
    assert any("X Free" in s for s in result.detail["manual_steps"])
    assert [x.player_id for x in league.my_team.players] == ["a"]  # nothing changed
    # The owner confirms -> the demo league is updated to mirror the move.
    assert steps.simulate_in_demo(p, league, VALUES) == {"demo_league": "updated to reflect the owner's move"}
    assert [x.player_id for x in league.my_team.players] == ["x"]
    assert len(steps.revalidate(p, league)) == 2  # x rostered, a gone


def test_no_drop_claim_counts_only_non_il_players(conn, tmp_path, monkeypatch):
    league = _league(tmp_path, monkeypatch)
    league.slots = (SlotSpec("PG", 1, True), SlotSpec("BN", 1, False), SlotSpec("IL", 1, False))
    league.my_team.players.append(RosterPlayer("c", "C Hurt", "BOS", ("PG", "UTIL"), "BN"))
    p = _proposal(conn, kind="waiver_claim", payload={"add": "x", "add_name": "X Free", "drop": None})
    assert steps.revalidate(p, league) == ["roster is full: a drop is required"]
    league.my_team.players[-1].selected = "IL"  # owner stashes the injured player on IL
    assert steps.revalidate(p, league) == []


def test_yahoo_source_returns_steps_and_never_simulates(conn, tmp_path, monkeypatch):
    league = _league(tmp_path, monkeypatch)
    league.source = "yahoo"
    p = _proposal(conn, payload={"add": "x", "add_name": "X Free", "drop": "a", "drop_name": "A"})
    result = steps.owner_steps(p, league)
    assert result.status == "awaiting_owner"
    assert not any("Demo league" in s for s in result.detail["manual_steps"])
    assert steps.simulate_in_demo(p, league, VALUES) == {}
    assert [x.player_id for x in league.my_team.players] == ["a"]


def test_owner_confirmation_lifecycle(conn):
    p = _proposal(conn)
    props.record_review(conn, p.ref, "AGREE", "ok", "gpt-5.6-sol")
    props.record_owner_decision(conn, p.ref, True, "yes")
    with pytest.raises(props.ProposalError):
        props.confirm_done(conn, p.ref, "did it")  # no steps yet
    props.record_outcome(conn, p.ref, "awaiting_owner", {"manual_steps": ["x"]})
    assert p.ref in [x.ref for x in props.list_proposals(conn, "open")]
    done = props.confirm_done(conn, p.ref, "did it", {"demo_league": "updated"})
    assert done.status == "done" and done.execution["owner_confirmation"] == "did it"
    with pytest.raises(props.ProposalError):
        props.record_outcome(conn, p.ref, "executed", {})


def test_owner_can_reject_awaiting_owner_but_not_reapprove(conn):
    p = _proposal(conn)
    props.record_review(conn, p.ref, "AGREE", "ok", "gpt-5.6-sol")
    props.record_owner_decision(conn, p.ref, True, "yes")
    props.record_outcome(conn, p.ref, "awaiting_owner", {"manual_steps": ["x"]})
    with pytest.raises(props.ProposalError, match="only be rejected"):
        props.record_owner_decision(conn, p.ref, True, "yes again")
    p = props.record_owner_decision(conn, p.ref, False, "changed my mind")
    assert p.status == "rejected" and p.owner_quote == "changed my mind"
    assert p.ref not in [x.ref for x in props.list_proposals(conn, "open")]
    with pytest.raises(props.ProposalError):
        props.confirm_done(conn, p.ref, "did it")
    with pytest.raises(props.ProposalError):
        props.record_owner_decision(conn, p.ref, False, "again")


def test_strategy_proposal_writes_strategy(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "STRATEGY_JSON", tmp_path / "strategy.json")
    league = _league(tmp_path, monkeypatch)
    p = _proposal(conn, kind="strategy", payload={"punts": ["FT%"], "targets": ["BLK"], "notes": "bigs"})
    assert steps.owner_steps(p, league).status == "done"
    assert memory.current_strategy()["punts"] == ["FT%"]


def test_lineup_steps_change_nothing(tmp_path, monkeypatch):
    from datetime import date

    league = _league(tmp_path, monkeypatch)
    saved = []
    monkeypatch.setattr("fbm.gm.steps.save_league", lambda lg: saved.append(lg))
    out = steps.lineup_steps(league, {"a": "BN"}, date(2026, 10, 21))
    assert out["changes"] == ["A: PG → BN"] and "2026-10-21" in out["manual_steps"][0]
    assert league.my_team.players[0].selected == "PG" and saved == []
    assert steps.simulate_lineup_in_demo(league, {"a": "BN"}) is True
    assert league.my_team.players[0].selected == "BN" and len(saved) == 1
    league.source = "yahoo"
    assert steps.simulate_lineup_in_demo(league, {"a": "PG"}) is False


def test_journal_append(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "JOURNAL_DIR", tmp_path / "journal")
    moment = datetime(2026, 10, 21, 9, 30, tzinfo=ZoneInfo("Asia/Manila"))
    path = memory.journal_append("decision", "Start Sarr", "Because BLK swing.", now=moment)
    text = path.read_text(encoding="utf-8")
    assert path.name == "2026-10-21.md" and "09:30 local / Tue 21:30 ET · decision · Start Sarr" in text
    with pytest.raises(ValueError):
        memory.journal_append("gossip", "x", "y")


class FakeSMTP:
    sent = []

    def __init__(self, host, port, context=None, timeout=None):
        self.host = host

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def login(self, user, pw):
        self.user = user

    def send_message(self, msg):
        FakeSMTP.sent.append(msg)


def test_send_email_and_quiet_hours(conn, monkeypatch):
    creds = {"gmail_address": "me@example.com", "gmail_app_password": "abcdabcdabcdabcd"}
    monkeypatch.setattr("fbm.credentials.get", lambda k: creds.get(k))
    res = notify.send_email(
        "Daily",
        "## Hi\n\n| a | b |\n|---|---|\n| 1 | 2 |",
        conn=conn,
        settings=Settings(email_enabled=True),
        smtp_factory=FakeSMTP,
    )
    assert res["status"] == "sent" and FakeSMTP.sent[-1]["Subject"] == "[FBM] Daily"
    html = FakeSMTP.sent[-1].get_body(("html",)).get_content()
    assert "<table>" in html and "Yahoo Fantasy" in html
    quiet = Settings(email_enabled=True, quiet_hours="00:00-23:59")
    assert notify.send_email("x", "y", "report", conn, quiet, FakeSMTP)["status"] == "held"
    assert notify.send_email("x", "y", "approval", conn, quiet, FakeSMTP)["status"] == "sent"
    statuses = [r[0] for r in conn.execute("SELECT status FROM outbox ORDER BY id")]
    assert statuses == ["sent", "held", "sent"]


def test_quiet_hours_wraps_midnight():
    assert notify.in_quiet_hours("23:00-07:00", datetime(2026, 1, 1, 2, 0))
    assert not notify.in_quiet_hours("23:00-07:00", datetime(2026, 1, 1, 12, 0))
    assert not notify.in_quiet_hours("", datetime(2026, 1, 1, 2, 0))
