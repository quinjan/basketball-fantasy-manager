"""Proposal lifecycle. FBM never changes the league: a proposal ends in exact steps the OWNER performs.

pending_review --record_review--> pending_approval --owner decision--> approved | rejected
approved --proposal_steps (re-validated)--> awaiting_owner (steps handed to the owner) | failed
awaiting_owner --owner confirms (confirm_done)--> done
(a move the league already shows is not a failure: proposal_steps -> awaiting_owner; a proposal that `failed` only
 because the owner made the move first can still be confirmed done once the league shows it)
(`failed` is for re-validation problems. A kind with no step template is not a failure: proposal_steps returns generic
 steps + a warning. A proposal that failed with a tool `error` (not a re-validation) keeps the owner's approval and can
 be re-run through proposal_steps until it expires.)
awaiting_owner --owner changes their mind before making the move (owner decision, reject)--> rejected
strategy proposals change only FBM's memory: approved --proposal_steps--> done
any open state past expires_at --> expired   (no steps after expiry)
(The `executed_at` column records when the proposal reached its outcome.)
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

KINDS = ("add_drop", "waiver_claim", "trade", "strategy", "il_activation", "other")
VERDICTS = ("AGREE", "DISAGREE", "SPLIT")
OPEN = ("pending_review", "pending_approval", "approved")
LIVE = (*OPEN, "awaiting_owner")  # every state in which a proposal still covers its move
LEGACY_IL_ACTION = "activate_from_il_and_drop"  # kind "other" + payload.action used before il_activation existed


class ProposalError(RuntimeError):
    pass


def effective_kind(kind: str, payload: dict[str, Any]) -> str:
    """The kind a proposal behaves as: legacy `other` IL-activation proposals are treated as il_activation."""
    if kind == "other" and payload.get("action") == LEGACY_IL_ACTION and payload.get("activate"):
        return "il_activation"
    return kind


def _ids(payload: dict[str, Any], key: str) -> tuple[str, ...]:
    return tuple(sorted(str(x) for x in payload.get(key) or []))


def move_key(kind: str, payload: dict[str, Any], title: str = "") -> tuple | None:
    """Identity of the move a proposal asks for; two live proposals with the same key are duplicates."""
    kind = effective_kind(kind, payload)
    if kind in ("add_drop", "waiver_claim"):
        return ("acquire", str(payload.get("add")), str(payload.get("drop") or ""))
    if kind == "il_activation":
        return ("il_activation", str(payload.get("activate")), str(payload.get("drop") or ""))
    if kind == "trade":
        return ("trade", str(payload.get("partner")), _ids(payload, "give"), _ids(payload, "get"))
    if kind == "strategy":
        return ("strategy", _ids(payload, "punts"), _ids(payload, "targets"))
    if payload:
        return ("other", json.dumps(payload, sort_keys=True, default=str))
    return ("other", title.strip().lower()) if title.strip() else None


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Proposal:
    id: int
    created_at: str
    kind: str
    title: str
    summary: str
    payload: dict[str, Any]
    rationale: str
    evidence: dict[str, Any] | None
    status: str
    expires_at: str
    review_verdict: str | None
    review_confidence: float | None
    review_notes: str | None
    review_model: str | None
    decided_at: str | None
    owner_quote: str | None
    executed_at: str | None
    execution: dict[str, Any] | None

    @property
    def ref(self) -> str:
        return f"P-{self.id}"

    @property
    def is_expired(self) -> bool:
        return datetime.fromisoformat(self.expires_at) <= _now()

    def as_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["ref"] = self.ref
        return d


def _row(row: sqlite3.Row) -> Proposal:
    d = dict(row)
    for key in ("payload", "evidence", "execution"):
        d[key] = json.loads(d[key]) if d.get(key) else (None if key != "payload" else {})
    return Proposal(**d)


def _event(conn: sqlite3.Connection, pid: int, event: str, detail: str = "") -> None:
    conn.execute(
        "INSERT INTO proposal_events(proposal_id, at, event, detail) VALUES (?,?,?,?)",
        (pid, _iso(_now()), event, detail),
    )


def parse_ref(ref: str | int) -> int:
    text = str(ref).strip().upper()
    return int(text[2:] if text.startswith("P-") else text)


def get(conn: sqlite3.Connection, ref: str | int) -> Proposal:
    row = conn.execute("SELECT * FROM proposals WHERE id = ?", (parse_ref(ref),)).fetchone()
    if row is None:
        raise ProposalError(f"Proposal {ref} not found.")
    return _row(row)


def expire_stale(conn: sqlite3.Connection) -> list[int]:
    now = _iso(_now())
    ids = [
        r[0]
        for r in conn.execute(
            f"SELECT id FROM proposals WHERE status IN ({','.join('?' * len(OPEN))}) AND expires_at <= ?", (*OPEN, now)
        )
    ]
    for pid in ids:
        conn.execute("UPDATE proposals SET status = 'expired' WHERE id = ?", (pid,))
        _event(conn, pid, "expired")
    conn.commit()
    return ids


def find_duplicate(conn: sqlite3.Connection, kind: str, payload: dict[str, Any], title: str = "") -> Proposal | None:
    """A live (pending_review/pending_approval/approved/awaiting_owner, unexpired) proposal for the same move."""
    key = move_key(kind, payload, title)
    if key is None:
        return None
    expire_stale(conn)
    rows = conn.execute(f"SELECT * FROM proposals WHERE status IN ({','.join('?' * len(LIVE))})", LIVE).fetchall()
    for row in rows:
        p = _row(row)
        if move_key(p.kind, p.payload, p.title) == key:
            return p
    return None


def create(
    conn: sqlite3.Connection,
    kind: str,
    title: str,
    summary: str,
    payload: dict[str, Any],
    rationale: str,
    evidence: dict[str, Any] | None = None,
    expires_in_hours: float = 18.0,
) -> Proposal:
    if kind not in KINDS:
        raise ProposalError(f"Unknown kind '{kind}'. Use one of {KINDS}.")
    if not rationale.strip():
        raise ProposalError("A proposal needs a rationale grounded in tool outputs.")
    if not 0 < expires_in_hours <= 24 * 7:
        raise ProposalError("expires_in_hours must be between 0 and 168.")
    dup = find_duplicate(conn, kind, payload, title)
    if dup is not None:
        raise ProposalError(
            f'{dup.ref} ({dup.status}) already covers this move: "{dup.title}". Reject it (owner decision) or let it '
            "expire before proposing the same move again."
        )
    created = _now()
    cur = conn.execute(
        "INSERT INTO proposals(created_at, kind, title, summary, payload, rationale, evidence, status, expires_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (
            _iso(created),
            kind,
            title,
            summary,
            json.dumps(payload),
            rationale,
            json.dumps(evidence or {}),
            "pending_review",
            _iso(created + timedelta(hours=expires_in_hours)),
        ),
    )
    _event(conn, cur.lastrowid, "created", title)
    conn.commit()
    return get(conn, cur.lastrowid)


def record_review(
    conn: sqlite3.Connection,
    ref: str | int,
    verdict: str,
    notes: str,
    model: str,
    confidence: float | None = None,
    gm_model: str | None = None,
) -> Proposal:
    p = get(conn, ref)
    verdict = verdict.upper()
    if verdict not in VERDICTS:
        raise ProposalError(f"verdict must be one of {VERDICTS}")
    if p.status != "pending_review":
        raise ProposalError(f"{p.ref} is {p.status}; reviews are recorded only while pending_review.")
    if gm_model and model and model.split("-")[0] == gm_model.split("-")[0]:
        raise ProposalError(f"Reviewer model '{model}' is the same family as the GM ('{gm_model}').")
    conn.execute(
        "UPDATE proposals SET status='pending_approval', review_verdict=?, review_confidence=?, review_notes=?, "
        "review_model=? WHERE id=?",
        (verdict, confidence, notes, model, p.id),
    )
    _event(conn, p.id, "reviewed", f"{verdict} by {model}")
    conn.commit()
    return get(conn, p.id)


def record_owner_decision(conn: sqlite3.Connection, ref: str | int, approved: bool, owner_quote: str) -> Proposal:
    """Record the OWNER's decision. `owner_quote` must be the owner's own words (audit trail).

    An approved proposal still `awaiting_owner` (steps handed over, move not made) can only be rejected."""
    expire_stale(conn)
    p = get(conn, ref)
    if p.status == "awaiting_owner" and approved:
        raise ProposalError(f"{p.ref} is already approved and awaiting_owner; it can only be rejected now.")
    if p.status not in ("pending_approval", "awaiting_owner"):
        raise ProposalError(f"{p.ref} is {p.status}; only reviewed proposals (pending_approval) can be decided.")
    if not owner_quote.strip():
        raise ProposalError("owner_quote is required: the owner's exact reply.")
    status = "approved" if approved else "rejected"
    conn.execute(
        "UPDATE proposals SET status=?, decided_at=?, owner_quote=? WHERE id=?",
        (status, _iso(_now()), owner_quote.strip(), p.id),
    )
    _event(conn, p.id, status, owner_quote.strip()[:200])
    conn.commit()
    return get(conn, p.id)


def _retryable_failure(p: Proposal) -> bool:
    """`failed` because FBM had no step template/hit a tool error (not a re-validation failure): the owner's approval
    stands, so proposal_steps may be run again once the template exists. Never after expiry."""
    ex = p.execution or {}
    return p.status == "failed" and bool(p.owner_quote) and bool(ex.get("error")) and not ex.get("revalidation")


def ready_for_steps(conn: sqlite3.Connection, ref: str | int) -> Proposal:
    expire_stale(conn)
    p = get(conn, ref)
    if p.status != "approved" and not (_retryable_failure(p) and not p.is_expired):
        raise ProposalError(f"{p.ref} is {p.status}; only approved, unexpired proposals get owner steps.")
    if not p.owner_quote:
        raise ProposalError(f"{p.ref} has no recorded owner approval.")
    return p


def record_outcome(conn: sqlite3.Connection, ref: str | int, status: str, result: dict[str, Any]) -> Proposal:
    """awaiting_owner (steps handed to the owner) | done (owner confirmed / FBM-internal change) | failed."""
    if status not in ("awaiting_owner", "done", "failed"):
        raise ProposalError("invalid proposal outcome status")
    p = get(conn, ref)
    conn.execute(
        "UPDATE proposals SET status=?, executed_at=?, execution=? WHERE id=?",
        (status, _iso(_now()), json.dumps(result), p.id),
    )
    _event(conn, p.id, status, json.dumps(result)[:300])
    conn.commit()
    return get(conn, p.id)


def confirm_done(
    conn: sqlite3.Connection,
    ref: str | int,
    note: str,
    extra: dict[str, Any] | None = None,
    move_on_roster: bool = False,
) -> Proposal:
    """The OWNER says they made the move in their league. `move_on_roster`: the league already shows the move, which
    also recovers a proposal that `failed` re-validation only because the owner made the move first."""
    p = get(conn, ref)
    if not (p.status == "awaiting_owner" or (p.status == "failed" and move_on_roster)):
        raise ProposalError(f"{p.ref} is {p.status}, not awaiting_owner.")
    result = {**(p.execution or {}), **(extra or {}), "owner_confirmation": note}
    return record_outcome(conn, p.id, "done", result)


def list_proposals(conn: sqlite3.Connection, status: str | None = None, limit: int = 20) -> list[Proposal]:
    expire_stale(conn)
    if status == "open":
        rows = conn.execute(
            f"SELECT * FROM proposals WHERE status IN ({','.join('?' * len(OPEN))}, 'awaiting_owner') "
            "ORDER BY id DESC LIMIT ?",
            (*OPEN, limit),
        )
    elif status:
        rows = conn.execute("SELECT * FROM proposals WHERE status = ? ORDER BY id DESC LIMIT ?", (status, limit))
    else:
        rows = conn.execute("SELECT * FROM proposals ORDER BY id DESC LIMIT ?", (limit,))
    return [_row(r) for r in rows]
