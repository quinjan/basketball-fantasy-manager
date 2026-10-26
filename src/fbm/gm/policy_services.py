"""Policy settings tools (read-only): current settings + history, and tuner suggestions with ready proposal payloads.
Nothing here changes a setting: only an owner-approved `policy` proposal does (via proposal_steps)."""

from __future__ import annotations

from typing import Any

from fbm.gm import policy, policy_tuning
from fbm.store import db


def policy_settings(conn=None) -> dict[str, Any]:
    conn = conn or db.connect()
    return {"version": policy.version(conn), "settings": policy.table(conn), "history": policy.history(conn, 20)}


def policy_suggestions(conn=None) -> dict[str, Any]:
    conn = conn or db.connect()
    sugg = policy_tuning.suggest(conn)
    return {
        "suggestions": [{**s, "proposal": policy_tuning.draft_proposal_payload(s)} for s in sugg],
        "note": (
            "Suggestions only; nothing was changed. To change a setting, create_proposal(kind='policy', ...) with the "
            "payload shown; it must pass the independent reviewer and the owner's explicit approval before "
            "proposal_steps applies it."
        ),
    }
