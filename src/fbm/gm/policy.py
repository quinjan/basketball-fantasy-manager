"""Policy settings: the decision rules FBM tunes from replayed outcomes (owner decisions 2026-10-26).

A policy setting is a decision rule ("concede a category below 25% mapped odds", "use HT STREAM7 for lineups"),
not a projection. Current values live in `policy_params`; a key without a row uses its registry default. Values change
ONLY through an owner-approved `policy` proposal (owner decision 1), which records `policy_history` and bumps the
global policy version. Every logged decision snapshots (version, values) so the replay grader can attribute outcomes
to the settings in force.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fbm.hashtag import VIEWS


@dataclass(frozen=True)
class PolicyParam:
    key: str
    default: Any
    kind: str  # number | int | choice | bool | json
    description: str
    learned_from: str
    bounds: tuple[float, float] | None = None  # number/int
    choices: tuple[str, ...] | None = None  # choice
    max_step: float | None = None  # largest change one proposal may make (number/int)


def _view(key: str, default: str, decisions: str) -> PolicyParam:
    return PolicyParam(
        key,
        default,
        "choice",
        f"HT projection view used for {decisions}.",
        "per-view replay: re-run past decisions with each view, replay on real box scores (design §6.1)",
        choices=VIEWS,
    )


REGISTRY_LIST: list[PolicyParam] = [
    # HT projection view per decision (baseline: design §6.1; each retro tests the alternatives)
    _view("policy.view.weekly_odds", "STREAM7", "the weekly H2H totals/odds, chase/concede and week weights"),
    _view("policy.view.lineup", "STREAM7", "daily lineups (start/sit)"),
    _view("policy.view.stream", "STREAM7", "streams within the week"),
    _view("policy.view.claim", "BLEND14", "waiver claims and adds kept beyond this week"),
    _view("policy.view.ros", "ROS", "drops, trades, IL hold/drop, season category weights, playoff planning"),
    # matchup odds = HT totals + FBM simulator spread (owner decision on HT's share-formula odds)
    PolicyParam(
        "policy.odds_spread_scale",
        1.0,
        "number",
        "Multiplier on the simulator's week-to-week spread when turning HT's projected weekly totals into win odds "
        "(1 = the simulator's spread as is; >1 = less confident).",
        "odds reliability: Brier/reliability of past weeks' odds vs Yahoo finals",
        bounds=(0.5, 2.5),
        max_step=0.25,
    ),
    # chase / hold / concede (on calibrated odds)
    PolicyParam(
        "policy.contested_band",
        0.25,
        "number",
        "Distance of P(win category) from 50% within which a category counts as contested (full weight).",
        "lineup + chase/concede replay",
        bounds=(0.05, 0.45),
        max_step=0.05,
    ),
    PolicyParam(
        "policy.weight_floor",
        0.2,
        "number",
        "Weight for categories already won or lost this week (punts get 0).",
        "lineup replay",
        bounds=(0.0, 0.6),
        max_step=0.1,
    ),
    PolicyParam(
        "policy.concede_below",
        0.25,
        "number",
        "P(win category) below which the category is conceded for the week (stop spending moves on it).",
        "chase/concede replay",
        bounds=(0.05, 0.45),
        max_step=0.05,
    ),
    PolicyParam(
        "policy.lock_above",
        0.80,
        "number",
        "P(win category) above which the category is treated as locked for the week.",
        "chase/concede replay",
        bounds=(0.55, 0.95),
        max_step=0.05,
    ),
    PolicyParam(
        "policy.season_weights",
        {},
        "json",
        "Season category multipliers sent to HT (snapped to HT's x0.25-x3 steps). {} = derive from the approved "
        "strategy (targets up, soft-conceded categories down, punts excluded).",
        "category_weights replay",
    ),
    # streams
    PolicyParam(
        "policy.stream_min_gain",
        0.05,
        "number",
        "Smallest gain in expected categories won worth spending an add on.",
        "stream replay",
        bounds=(0.0, 0.3),
        max_step=0.02,
    ),
    PolicyParam(
        "policy.ros_penalty",
        0.5,
        "number",
        "Expected-category penalty per point of ROS value a streaming drop gives up.",
        "stream replay (14-day ROS cost of drops)",
        bounds=(0.0, 2.0),
        max_step=0.25,
    ),
    PolicyParam(
        "policy.add_reserve",
        1,
        "int",
        "Adds held back until Thursday (ET) for late-week needs (injuries, close categories).",
        "stream replay",
        bounds=(0, 3),
        max_step=1,
    ),
    # start/sit
    PolicyParam(
        "policy.pct_risk.enabled",
        False,
        "bool",
        "Sit a high-volume poor shooter when FG%/FT% is contested and his expected impact is below the threshold.",
        "lineup replay",
    ),
    PolicyParam(
        "policy.pct_risk.max_impact",
        -0.5,
        "number",
        "Expected FG%/FT% impact (G points for the day) below which the pct-risk rule sits a player.",
        "lineup replay",
        bounds=(-2.0, 0.0),
        max_step=0.25,
    ),
    # IL / injury
    PolicyParam(
        "policy.il_hold_days",
        14,
        "int",
        "Hold an injured player expected back within this many days (else consider dropping).",
        "il replay",
        bounds=(3, 42),
        max_step=7,
    ),
    # opponent
    PolicyParam(
        "policy.opponent_adds",
        "manager_model",
        "choice",
        "Opponent streaming assumed for the week: the manager model, or a fixed number of adds.",
        "opponent's actual adds vs assumed",
        choices=("manager_model", "fixed:0", "fixed:1", "fixed:2", "fixed:3", "fixed:4"),
    ),
]
REGISTRY: dict[str, PolicyParam] = {p.key: p for p in REGISTRY_LIST}


class PolicyError(ValueError):
    pass


def param(key: str) -> PolicyParam:
    try:
        return REGISTRY[key]
    except KeyError as err:
        raise PolicyError(f"Unknown policy setting '{key}'.") from err


def validate(key: str, value: Any) -> Any:
    """Return the value coerced to the setting's type, or raise PolicyError."""
    p = param(key)
    if p.kind in ("number", "int"):
        try:
            v = int(value) if p.kind == "int" else float(value)
        except (TypeError, ValueError) as err:
            raise PolicyError(f"{key}: expected a number, got {value!r}.") from err
        if p.bounds and not (p.bounds[0] <= v <= p.bounds[1]):
            raise PolicyError(f"{key}: {v} outside bounds {p.bounds}.")
        return v
    if p.kind == "choice":
        if value not in (p.choices or ()):
            raise PolicyError(f"{key}: {value!r} not one of {p.choices}.")
        return value
    if p.kind == "bool":
        if not isinstance(value, bool):
            raise PolicyError(f"{key}: expected true/false, got {value!r}.")
        return value
    if p.kind == "json":
        json.dumps(value)
        return value
    raise PolicyError(f"{key}: unknown kind {p.kind}.")


def step_problem(key: str, current: Any, value: Any) -> str | None:
    """Why moving `key` from `current` to `value` is too big a step for one proposal (None = fine)."""
    p = param(key)
    if p.max_step is None or p.kind not in ("number", "int"):
        return None
    step = abs(float(value) - float(current))
    if step > float(p.max_step) + 1e-9:
        return f"{key}: step {current} -> {value} ({step:g}) exceeds max_step {p.max_step:g}."
    return None


def check_changes(conn: sqlite3.Connection, changes: Any) -> tuple[dict[str, Any], list[str]]:
    """Validate a policy proposal's changes against the CURRENT values: type/bounds/choices and max_step.
    Returns (clean changes, problems); a non-empty problems list means the changes must not be applied."""
    if not isinstance(changes, dict) or not changes:
        return {}, ["payload.changes must be a non-empty {setting: value} object."]
    clean: dict[str, Any] = {}
    problems: list[str] = []
    for k, v in changes.items():
        try:
            nv = validate(k, v)
        except (PolicyError, TypeError) as err:
            problems.append(str(err))
            continue
        cur = get(conn, k)
        if cur == nv:
            problems.append(f"{k}: already {nv!r} (no change).")
            continue
        sp = step_problem(k, cur, nv)
        if sp:
            problems.append(sp)
            continue
        clean[k] = nv
    return clean, problems


def history(conn: sqlite3.Connection, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM policy_history ORDER BY version DESC, id DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        for k in ("old_value", "new_value", "evidence"):
            d[k] = json.loads(d[k]) if d.get(k) else None
        out.append(d)
    return out


def get(conn: sqlite3.Connection, key: str) -> Any:
    p = param(key)
    row = conn.execute("SELECT value FROM policy_params WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value"]) if row else p.default


def all_values(conn: sqlite3.Connection) -> dict[str, Any]:
    stored = {r["key"]: json.loads(r["value"]) for r in conn.execute("SELECT key, value FROM policy_params")}
    return {k: stored.get(k, p.default) for k, p in REGISTRY.items()}


def version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT MAX(version) FROM policy_history").fetchone()
    return int(row[0] or 0)


def snapshot(conn: sqlite3.Connection) -> tuple[int, dict[str, Any]]:
    return version(conn), all_values(conn)


def apply_changes(
    conn: sqlite3.Connection,
    changes: dict[str, Any],
    source: str,
    proposal_ref: str | None = None,
    evidence: dict[str, Any] | None = None,
) -> int:
    """Write validated settings as one new policy version. Only the approved-policy-proposal path (and the one-off
    migration) may call this. Returns the new version."""
    if not changes:
        raise PolicyError("No policy changes given.")
    clean = {k: validate(k, v) for k, v in changes.items()}
    new_version = version(conn) + 1
    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    for k, v in clean.items():
        old = conn.execute("SELECT value FROM policy_params WHERE key = ?", (k,)).fetchone()
        conn.execute(
            "INSERT INTO policy_params(key, value, version, source, updated_at) VALUES (?,?,?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, version=excluded.version, source=excluded.source, "
            "updated_at=excluded.updated_at",
            (k, json.dumps(v), new_version, source, stamp),
        )
        conn.execute(
            "INSERT INTO policy_history(version, key, old_value, new_value, proposal_ref, evidence, changed_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                new_version,
                k,
                old["value"] if old else json.dumps(param(k).default),
                json.dumps(v),
                proposal_ref,
                json.dumps(evidence) if evidence is not None else None,
                stamp,
            ),
        )
    conn.commit()
    return new_version


def table(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every setting with its current value, default and where the value came from."""
    rows = {r["key"]: dict(r) for r in conn.execute("SELECT * FROM policy_params")}
    out = []
    for k, p in REGISTRY.items():
        r = rows.get(k)
        out.append(
            {
                "key": k,
                "value": json.loads(r["value"]) if r else p.default,
                "default": p.default,
                "source": r["source"] if r else "default",
                "since_version": r["version"] if r else 0,
                "kind": p.kind,
                "bounds": p.bounds,
                "choices": p.choices,
                "description": p.description,
                "learned_from": p.learned_from,
            }
        )
    return out
