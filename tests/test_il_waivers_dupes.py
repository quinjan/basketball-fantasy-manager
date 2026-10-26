"""IL activation (simulation + proposal kind), waiver-aware streaming, and the duplicate-proposal guard."""

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest

from fbm.analytics.categories import NINE_CAT
from fbm.analytics.il_activation import activation_phases, phase_games, validate_activation
from fbm.analytics.simulation import PlayerModel, play_probability
from fbm.analytics.streaming import InvalidPlan, Move, plan_streams, roster_by_day
from fbm.gm import il, outcomes, steps
from fbm.gm import proposals as props
from fbm.gm import services as svc
from fbm.gm.waiver_status import clear_dates, games_from
from fbm.league.model import FantasyTeam, LeagueState, RosterPlayer, SlotSpec
from fbm.store import db
from tests.test_outcomes import POOL, add_game, add_line
from tests.test_streaming_trades import D1, D2, D3, GOOD, MEH, stream_ctx, values_frame

SLOTS = (SlotSpec("PG", 1, True), SlotSpec("UTIL", 1, True), SlotSpec("BN", 1, False), SlotSpec("IL", 1, False))


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def player(pid, team, selected, status="", eligible=("PG", "UTIL")):
    return RosterPlayer(pid, pid, team, eligible, selected, status)


def il_roster() -> list[RosterPlayer]:
    # Active spots (PG, UTIL, BN) are full; I is hurt on IL.
    return [
        player("A", "AAA", "PG"),
        player("B", "BBB", "UTIL"),
        player("D", "DDD", "BN"),
        player("I", "III", "IL", "O", ("PG", "UTIL", "IL", "IL+")),
    ]


# ---------------------------------------------------------------- IL activation: pure helpers


def test_validate_activation():
    roster = il_roster()
    assert validate_activation(roster, "I", "D", 3) == []
    assert "roster is full (3/3 active)" in validate_activation(roster, "I", None, 3)[0]
    assert validate_activation(roster, "I", None, 4) == []  # an open active spot needs no drop
    assert validate_activation(roster, "A", "D", 3) == ["A is not in an IL slot"]
    assert validate_activation(roster, "I", "zzz", 3)[0] == "zzz is not on your roster"
    assert validate_activation(roster, "nope", None, 3) == ["nope is not on your roster"]
    assert validate_activation(roster, "I", "I", 3)[0].startswith("the player being activated")
    roster.append(player("J", "JJJ", "IL", "O", ("IL", "IL+")))
    assert "does not open an active roster spot" in validate_activation(roster, "I", "J", 3)[0]


def test_activated_player_counts_only_from_the_effective_day():
    playing = {D1: {"AAA", "BBB", "DDD", "III"}, D2: {"AAA", "BBB", "DDD", "III"}, D3: {"III"}}
    value = {"A": 2.0, "B": -1.0, "D": -2.0, "I": 1.0}
    days = [D1, D2, D3]
    slots = ["PG", "UTIL"]
    phases = activation_phases(il_roster(), "I", "D", D2, available_from=D2)
    games = phase_games(phases, days, playing, slots, value)
    assert ("I", D1) not in games  # still on IL on D1
    assert ("I", D2) in games and ("I", D3) in games
    assert ("D", D2) not in games and ("D", D1) not in games  # D benched on D1, dropped from D2
    assert [g for g in games if g[1] == D1] == [("A", D1), ("B", D1)]
    # Injured until D3: he sits on the bench on D2 and only starts on D3.
    late = activation_phases(il_roster(), "I", "D", D2, available_from=D3)
    late_games = phase_games(late, days, playing, slots, value)
    assert ("I", D2) not in late_games and ("I", D3) in late_games
    # Never returns inside the window: no games at all.
    never = phase_games(activation_phases(il_roster(), "I", "D", D2, None), days, playing, slots, value)
    assert not any(pid == "I" for pid, _ in never)
    # The inputs are not mutated.
    assert [p.selected for p in il_roster()] == ["PG", "UTIL", "BN", "IL"]


# ---------------------------------------------------------------- IL activation: evaluate service (fake context)

SAMPLES = {"A": GOOD, "B": MEH, "D": MEH, "I": GOOD, "O1": GOOD}


def il_world(monkeypatch, conn, status="O", return_date="2026-10-21"):
    me = FantasyTeam("t1", "Me", players=il_roster())
    opp = FantasyTeam("t2", "Opp", players=[player("O1", "OOO", "PG")])
    league = LeagueState("L", "Test", 2027, "t1", [me, opp], slots=SLOTS, source="demo", opponent_key="t2")
    values = values_frame({pid: {"pts": 1.0} for pid in SAMPLES})
    teams = {"A": "AAA", "B": "BBB", "D": "DDD", "I": "III", "O1": "OOO"}
    projections = pd.DataFrame({"name": list(teams), "team": list(teams.values())}, index=list(teams))
    statuses = {"I": status} if status else {}

    class Ctx:
        cfg = SimpleNamespace(categories=NINE_CAT)
        ros_values = None

        def __init__(self):
            self.values, self.projections = values, projections

        def statuses(self, c=None):
            return statuses

        def models(self, ids, c=None, horizon="week"):
            return {pid: PlayerModel(pid, SAMPLES[pid][None], play_probability(statuses.get(pid, ""))) for pid in ids}

    if status:
        conn.execute(
            "INSERT INTO injuries_current(espn_id, status, fantasy_status, return_date, fetched_at) VALUES "
            "('I', 'Out', ?, ?, 'x')",
            (status, return_date),
        )
    for day, teams_playing in {
        D1: ["AAA", "BBB", "DDD", "OOO"],
        D2: ["AAA", "BBB", "III", "OOO"],
        D3: ["III", "OOO"],
    }.items():
        for t in teams_playing:
            add_game(conn, f"{t}{day}", day.isoformat())
            conn.execute("UPDATE games SET home = ?, away = 'ZZZ' WHERE event_id = ?", (t, f"{t}{day}"))
    monkeypatch.setattr(svc, "active_league", lambda: league)
    monkeypatch.setattr(svc, "ctx", lambda: Ctx())
    monkeypatch.setattr(svc, "_conn", lambda: conn)
    monkeypatch.setattr(svc, "_week_window", lambda day=None: (D1, D1, D3))
    monkeypatch.setattr(svc, "_strategy_punts", lambda punts=None: ())
    return league


def test_evaluate_il_activation_end_to_end(monkeypatch, conn):
    il_world(monkeypatch, conn)
    res = il.evaluate_il_activation("I", "D", effective=D2.isoformat(), n=400)
    assert res["move"] == "il_activation" and res["activate_id"] == "I" and res["drop_id"] == "D"
    assert res["effective_et"] == "2026-10-21"
    # Out with a return date of D2: counted from D2 at the GTD play probability.
    inj = res["activate_injury"]
    assert inj["counts_from_et"] == "2026-10-21" and inj["return_date"] == "2026-10-21"
    assert inj["play_probability_used"] == pytest.approx(play_probability("GTD"), abs=1e-3)
    assert res["games_left_this_week"] == {"activate": 2, "drop_lost": 0}
    assert res["games_started_this_week"]["after"] > res["games_started_this_week"]["before"]
    assert res["expected_cats_after"] > res["expected_cats_before"]
    assert set(res["cat_p_win_change"]) == {c.label for c in NINE_CAT}
    for key in ("ros_value_delta", "ros_g_delta_by_cat", "p_win_majority_before", "p_win_majority_after"):
        assert key in res
    # Starting later in the week only keeps the last game.
    late = il.evaluate_il_activation("I", "D", effective=D3.isoformat(), play_prob=1.0, n=400)
    assert late["games_left_this_week"]["activate"] == 1 and late["activate_injury"]["basis"].startswith("override")
    assert il.evaluate_il_activation("I", "D", effective=D2.isoformat(), play_prob=1.0)["games_left_this_week"] == {
        "activate": 2,
        "drop_lost": 0,
    }
    # evaluate_add_drop(activate=...) routes to the same model.
    routed = svc.evaluate_add_drop(activate="I", drop="D", effective=D2.isoformat(), n=400)
    assert routed["move"] == "il_activation"


def test_evaluate_il_activation_rejects_impossible_moves_and_unknown_return(monkeypatch, conn):
    il_world(monkeypatch, conn, return_date=None)
    with pytest.raises(svc.GMError, match="roster is full"):
        il.evaluate_il_activation("I", None, effective=D2.isoformat())
    with pytest.raises(svc.GMError, match="not in an IL slot"):
        il.evaluate_il_activation("A", "D", effective=D2.isoformat())
    res = il.evaluate_il_activation("I", "D", effective=D2.isoformat(), n=300)
    assert res["activate_injury"]["play_probability_used"] == 0 and res["games_left_this_week"]["activate"] == 0
    assert any("no return date" in n for n in res["notes"])
    with pytest.raises(svc.GMError, match="play_prob"):
        il.evaluate_il_activation("I", "D", play_prob=1.5)
    with pytest.raises(svc.GMError, match="`add`"):
        svc.evaluate_add_drop()


def test_create_il_activation_proposal_validates_and_names(monkeypatch, conn):
    il_world(monkeypatch, conn)
    payload = svc._il_activation_payload({"activate": "I", "drop": "D", "effective_et": "2026-10-23"})
    assert payload["activate_name"] == "I" and payload["drop"] == "D" and payload["effective_et"] == "2026-10-23"
    with pytest.raises(svc.GMError, match="Invalid IL activation"):
        svc._il_activation_payload({"activate": "I"})  # roster full, no drop
    with pytest.raises(svc.GMError, match="activate"):
        svc._il_activation_payload({"drop": "D"})


# ---------------------------------------------------------------- il_activation proposal kind


def proposal(conn, kind="il_activation", payload=None, approve=True, **kw):
    payload = payload or {"activate": "I", "activate_name": "I Hurt", "drop": "D", "drop_name": "D Bench"}
    p = props.create(conn, kind, kw.get("title", "Activate I"), "s", payload, "tool says so", {})
    if approve:
        props.record_review(conn, p.ref, "AGREE", "ok", "gpt-5.6-sol")
        p = props.record_owner_decision(conn, p.ref, True, "approve")
    return p


def il_league(source="demo") -> LeagueState:
    me = FantasyTeam("t1", "Me", players=il_roster())
    return LeagueState("L", "Test", 2027, "t1", [me, FantasyTeam("t2", "Them")], slots=SLOTS, source=source)


def test_il_activation_steps_validation_and_demo_application(conn, monkeypatch):
    monkeypatch.setattr("fbm.gm.steps.save_league", lambda lg: None)
    league = il_league()
    p = proposal(
        conn,
        payload={
            "activate": "I",
            "activate_name": "I Hurt",
            "drop": "D",
            "drop_name": "D Bench",
            "effective_et": "2026-10-23",
        },
    )
    assert not steps.already_made(p, league) and steps.revalidate(p, league) == []
    result = steps.owner_steps(p, league)
    assert result.status == "awaiting_owner"
    text = "\n".join(result.detail["manual_steps"])
    assert "2026-10-23" in text and "Drop **D Bench**" in text and "Move **I Hurt** from IL" in text
    assert text.index("Drop **D Bench**") < text.index("Move **I Hurt**")  # the drop opens the spot first
    assert "Demo league" in text
    assert [x.selected for x in league.my_team.players] == ["PG", "UTIL", "BN", "IL"]  # nothing changed yet
    assert steps.simulate_in_demo(p, league) == {"demo_league": "updated to reflect the owner's move"}
    assert {x.player_id: x.selected for x in league.my_team.players} == {"A": "PG", "B": "UTIL", "I": "BN"}
    assert steps.already_made(p, league)
    again = steps.owner_steps(p, league)
    assert again.status == "awaiting_owner" and again.detail["already_made"] is True


def test_il_activation_revalidation_failures(conn):
    league = il_league()
    p = proposal(conn, payload={"activate": "I", "activate_name": "I Hurt", "drop": None})
    assert steps.revalidate(p, league) == ["roster is full (3/3 active): a drop is required to activate I"]
    assert steps.owner_steps(p, league).status == "failed"
    league.my_team.players = [x for x in league.my_team.players if x.player_id != "D"]  # the drop is already gone
    p2 = proposal(conn, payload={"activate": "I", "activate_name": "I Hurt", "drop": "D", "drop_name": "D Bench"})
    assert steps.revalidate(p2, league) == ["D Bench is not on your roster"]
    p3 = proposal(conn, payload={"activate": "gone", "activate_name": "Gone Guy", "drop": None})
    assert steps.revalidate(p3, league) == ["Gone Guy is not on your roster"]


def test_il_activation_graded_like_a_move(conn):
    p = proposal(conn, payload={"activate": "x", "drop": "y"})
    executed = datetime(2026, 10, 20, 16, 0, tzinfo=UTC).isoformat()
    conn.execute("UPDATE proposals SET status='done', executed_at=? WHERE id=?", (executed, p.id))
    add_game(conn, "g0", "2026-10-21")
    add_line(
        conn, "g0", "x", "2026-10-21", pts=22, reb=8, ast=4, stl=1, blk=1, tov=2, tpm=2, fgm=8, fga=15, ftm=4, fta=5
    )
    (g,) = outcomes.grade_decisions(conn, POOL, NINE_CAT, date(2026, 10, 26), lookback_days=10_000)
    assert g["kind"] == "il_activation" and g["acquired"][0]["games"] == 1 and g["gave_up"][0]["games"] == 0


# ---------------------------------------------------------------- unknown kinds fall back to generic steps


def test_unknown_kind_gets_generic_steps_not_a_failure(conn):
    p = proposal(conn, kind="other", payload={"note": "x"}, title="Do something unusual")
    result = steps.owner_steps(p, il_league())
    assert result.status == "awaiting_owner"
    assert "no step template for proposal kind 'other'" in result.detail["warning"]
    assert any("Do something unusual" in s for s in result.detail["manual_steps"])


def test_legacy_other_il_proposal_behaves_as_il_activation(conn):
    legacy = {
        "action": "activate_from_il_and_drop",
        "activate": "I",
        "activate_name": "I Hurt",
        "drop": "D",
        "drop_name": "D Bench",
        "timing": "after tonight's game",
    }
    p = proposal(conn, kind="other", payload=legacy)
    result = steps.owner_steps(p, il_league())
    assert result.status == "awaiting_owner" and "warning" not in result.detail
    assert any("after tonight's game" in s for s in result.detail["manual_steps"])
    assert steps.revalidate(proposal(conn, kind="other", payload={**legacy, "drop": None}, title="t2"), il_league())


def test_failed_for_missing_template_can_be_rerun_but_revalidation_failures_cannot(conn):
    p = proposal(conn, kind="other", payload={"a": 1})
    props.record_outcome(conn, p.ref, "failed", {"error": "no owner steps for proposal kind other"})
    assert props.ready_for_steps(conn, p.ref).id == p.id
    q = proposal(conn, kind="other", payload={"a": 2})
    props.record_outcome(conn, q.ref, "failed", {"revalidation": ["X is no longer available"]})
    with pytest.raises(props.ProposalError, match="failed"):
        props.ready_for_steps(conn, q.ref)
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat(timespec="seconds")
    conn.execute("UPDATE proposals SET expires_at=? WHERE id=?", (past, p.id))
    with pytest.raises(props.ProposalError, match="failed"):
        props.ready_for_steps(conn, p.ref)


# ---------------------------------------------------------------- duplicate proposal guard


def test_duplicate_open_proposal_is_refused_with_the_existing_ref(conn):
    first = proposal(conn, approve=False)
    with pytest.raises(props.ProposalError, match=rf"{first.ref} \(pending_review\) already covers this move"):
        proposal(conn, approve=False, title="Same move again")
    # Still refused at every open stage, including awaiting_owner.
    props.record_review(conn, first.ref, "AGREE", "ok", "gpt-5.6-sol")
    props.record_owner_decision(conn, first.ref, True, "yes")
    with pytest.raises(props.ProposalError, match="approved"):
        proposal(conn, approve=False)
    props.record_outcome(conn, first.ref, "awaiting_owner", {"manual_steps": []})
    with pytest.raises(props.ProposalError, match="awaiting_owner"):
        proposal(conn, approve=False)
    # A different drop is a different move; a closed proposal frees the move up.
    assert proposal(conn, approve=False, payload={"activate": "I", "drop": "B"}).id != first.id
    props.confirm_done(conn, first.ref, "did it")
    assert proposal(conn, approve=False, title="again, after done").status == "pending_review"


def test_duplicate_guard_per_kind_and_legacy(conn):
    props.create(conn, "add_drop", "Add X", "s", {"add": "x", "drop": "a"}, "r")
    for kind in ("add_drop", "waiver_claim"):  # same acquisition however it is filed
        with pytest.raises(props.ProposalError, match="already covers"):
            props.create(conn, kind, "Add X again", "s", {"add": "x", "drop": "a", "add_name": "X"}, "r")
    props.create(conn, "add_drop", "Add X for b", "s", {"add": "x", "drop": "b"}, "r")  # different drop: allowed
    props.create(conn, "trade", "T", "s", {"partner": "t2", "give": ["a", "b"], "get": ["c"]}, "r")
    with pytest.raises(props.ProposalError, match="already covers"):
        props.create(conn, "trade", "T2", "s", {"partner": "t2", "give": ["b", "a"], "get": ["c"]}, "r")
    props.create(conn, "strategy", "S", "s", {"punts": ["FT%"], "targets": []}, "r")
    with pytest.raises(props.ProposalError, match="already covers"):
        props.create(conn, "strategy", "S2", "s", {"punts": ["FT%"], "targets": []}, "r")
    legacy = {"action": "activate_from_il_and_drop", "activate": "I", "drop": "D"}
    props.create(conn, "other", "legacy", "s", legacy, "r")
    with pytest.raises(props.ProposalError, match="already covers"):  # the first-class kind sees the legacy one
        props.create(conn, "il_activation", "new", "s", {"activate": "I", "drop": "D"}, "r")
    # Expired and rejected proposals never block.
    p = props.create(conn, "add_drop", "Add Z", "s", {"add": "z"}, "r")
    props.record_review(conn, p.ref, "AGREE", "ok", "gpt-5.6-sol")
    props.record_owner_decision(conn, p.ref, False, "no thanks")
    assert props.create(conn, "add_drop", "Add Z", "s", {"add": "z"}, "r").status == "pending_review"
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat(timespec="seconds")
    conn.execute("UPDATE proposals SET expires_at=? WHERE title='Add X'", (past,))
    assert props.create(conn, "add_drop", "Add X", "s", {"add": "x", "drop": "a"}, "r").status == "pending_review"


# ---------------------------------------------------------------- waiver-aware streaming


def test_waiver_players_are_scheduled_from_their_clear_date():
    ctx = stream_ctx()
    free = plan_streams(ctx, ["C"], ["B"], adds_available=1)
    assert [pm.move for pm in free.moves] == [Move(D2, "C", "B")]
    blocked = stream_ctx()
    blocked.add_not_before = {"C": D3}
    plan = plan_streams(blocked, ["C"], ["B"], adds_available=1)
    assert all(pm.move.day >= D3 for pm in plan.moves)
    assert plan.moves == [] or plan.moves[0].add_games == len(blocked.game_days("C", D3))
    with pytest.raises(InvalidPlan, match="on waivers until"):
        roster_by_day(blocked, [Move(D2, "C", "B")])
    assert roster_by_day(blocked, [Move(D3, "C", "B")])[D3] == ("A", "C")


def test_waiver_status_helpers():
    assert clear_dates({"a": "2026-10-24", "b": "junk", "c": "2026-10-25"}, only=["a", "b"]) == {
        "a": date(2026, 10, 24)
    }
    playing = {D1: {"X"}, D2: {"X"}, D3: {"X"}}
    assert games_from("X", [D1, D2, D3], playing) == 3
    assert games_from("X", [D1, D2, D3], playing, D3) == 1
    assert games_from("Y", [D1, D2, D3], playing, D3) == 0


def test_free_agents_flags_waiver_players_and_counts_games_from_clear_date(monkeypatch, conn):
    league = il_league()
    league.on_waivers = {"w": D3.isoformat()}
    for day in (D1, D2, D3):
        add_game(conn, f"g{day}", day.isoformat())
        conn.execute("UPDATE games SET home = 'WWW', away = 'ZZZ' WHERE event_id = ?", (f"g{day}",))
    monkeypatch.setattr(svc, "active_league", lambda: league)
    monkeypatch.setattr(svc, "_conn", lambda: conn)
    monkeypatch.setattr(svc, "_week_window", lambda day=None: (D1, D1, D3))
    rows = [
        {"id": "w", "nba": "WWW", "value": 1.0, "games_left_this_week": 3},
        {"id": "f", "nba": "WWW", "value": 1.0, "games_left_this_week": 3},
    ]
    svc._mark_waivers(rows)
    assert rows[0] == {
        "id": "w",
        "nba": "WWW",
        "value": 1.0,
        "games_left_this_week": 1,
        "on_waivers": True,
        "clears_waivers_et": "2026-10-22",
    }
    assert rows[1]["on_waivers"] is False and rows[1]["games_left_this_week"] == 3
