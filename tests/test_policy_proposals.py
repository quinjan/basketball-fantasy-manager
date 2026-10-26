import pytest

from fbm.gm import policy, steps
from fbm.gm import proposals as props
from fbm.store import db


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def _policy_proposal(conn, changes, evidence=None):
    return props.create(
        conn,
        kind="policy",
        title="Policy change",
        summary="s",
        payload={"changes": changes, "evidence": evidence or {"weeks": 5}},
        rationale="policy_suggestions says so",
    )


def _approve(conn, p):
    props.record_review(conn, p.ref, "AGREE", "replay checks out", "gpt-5.6-sol", 0.8, "claude-opus-5.5")
    return props.record_owner_decision(conn, p.ref, True, "yes, change it")


def test_policy_proposal_applies_only_after_review_and_owner(conn):
    p = _policy_proposal(conn, {"policy.contested_band": 0.3})
    with pytest.raises(props.ProposalError):
        props.ready_for_steps(conn, p.ref)
    assert policy.get(conn, "policy.contested_band") == 0.25
    props.record_review(conn, p.ref, "AGREE", "ok", "gpt-5.6-sol")
    with pytest.raises(props.ProposalError):
        props.ready_for_steps(conn, p.ref)
    assert policy.get(conn, "policy.contested_band") == 0.25
    props.record_owner_decision(conn, p.ref, True, "approved")
    p = props.ready_for_steps(conn, p.ref)
    res = steps.owner_steps(p, None, conn=conn)
    assert res.status == "done" and res.detail["policy_version"] == 1
    assert res.detail["changes"]["policy.contested_band"] == {"old": 0.25, "new": 0.3}
    p = props.record_outcome(conn, p.id, res.status, res.detail)
    assert p.status == "done"
    assert policy.get(conn, "policy.contested_band") == 0.3 and policy.version(conn) == 1
    h = policy.history(conn)
    assert h[0]["proposal_ref"] == p.ref and h[0]["evidence"] == {"weeks": 5} and h[0]["old_value"] == 0.25


def test_rejected_policy_is_never_applied(conn):
    p = _policy_proposal(conn, {"policy.view.lineup": "STREAM14"})
    props.record_review(conn, p.ref, "AGREE", "ok", "gpt-5.6-sol")
    props.record_owner_decision(conn, p.ref, False, "no")
    with pytest.raises(props.ProposalError):
        props.ready_for_steps(conn, p.ref)
    assert policy.get(conn, "policy.view.lineup") == "STREAM7" and policy.version(conn) == 0


def test_oversized_or_invalid_steps_fail(conn):
    p = _approve(conn, _policy_proposal(conn, {"policy.contested_band": 0.35}))  # max_step 0.05
    res = steps.owner_steps(p, None, conn=conn)
    assert res.status == "failed" and "max_step" in res.detail["revalidation"][0]
    p2 = _approve(conn, _policy_proposal(conn, {"policy.view.lineup": "NOPE"}))
    assert steps.owner_steps(p2, None, conn=conn).status == "failed"
    assert policy.version(conn) == 0


def test_step_checked_against_value_at_steps_time(conn):
    p = _approve(conn, _policy_proposal(conn, {"policy.stream_min_gain": 0.07}))
    policy.apply_changes(conn, {"policy.stream_min_gain": 0.02}, source="test")  # current moved meanwhile
    res = steps.owner_steps(p, None, conn=conn)
    assert res.status == "failed"
    assert policy.get(conn, "policy.stream_min_gain") == 0.02


def test_check_changes_and_duplicates(conn):
    clean, problems = policy.check_changes(conn, {"policy.add_reserve": 2, "policy.il_hold_days": 30})
    assert clean == {"policy.add_reserve": 2} and len(problems) == 1
    assert policy.check_changes(conn, {})[1]
    assert policy.check_changes(conn, {"policy.unknown": 1})[1]
    _policy_proposal(conn, {"policy.add_reserve": 2})
    with pytest.raises(props.ProposalError, match="already covers"):
        _policy_proposal(conn, {"policy.add_reserve": 2})
    _policy_proposal(conn, {"policy.add_reserve": 0})  # different move


def test_services_validate_policy_payload(monkeypatch, conn):
    from fbm.gm import memory, services

    monkeypatch.setattr(services, "_conn", lambda: conn)
    monkeypatch.setattr(memory, "journal_append", lambda *a, **k: None)
    with pytest.raises(services.GMError):
        services.create_proposal("policy", "t", "s", {"changes": {"policy.contested_band": 0.45}}, "r")
    out = services.create_proposal("policy", "t", "s", {"changes": {"policy.contested_band": "0.3"}}, "r")
    assert out["payload"]["changes"] == {"policy.contested_band": 0.3}
