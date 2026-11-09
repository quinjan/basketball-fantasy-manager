"""Did the owner's "done" move really happen in Yahoo?

`confirm_done` records only that the OWNER said they made the move. Yahoo is the source of truth, so every
confirmed add/drop, waiver claim, trade and IL activation is reconciled against the stored league transactions, the
current rosters and the daily lineup history (all read-only data FBM already syncs):

    verified      Yahoo shows the move (a matching transaction after the owner approved it, or the players sit
                  where the move puts them; an IL activation shows the player out of an IL slot).
    pending       not seen yet and still inside the window in which Yahoo may complete it (waiver processing, the
                  ~2-day league vote on trades) or the data is older than the window.
    not_executed  contradicted: the claim went to another team, or the window passed with no trace of the move
                  (trade rejected / withdrawn / vetoed, add never made, player still in the IL slot).

One Yahoo transaction (leg) is credited to at most one proposal. Only `verified` moves are graded; the proposal
status becomes `not_executed` for the contradicted ones (and returns to `done` if Yahoo later shows the move).
`verified` is sticky: later roster churn never un-verifies a move. Pure logic in `reconcile`; `verify_all` applies it.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from fbm.constants import C
from fbm.gm import proposals as props
from fbm.gm import trade_offers
from fbm.league.model import IL_SLOTS
from fbm.timeutil import ET
from fbm.yahoo.league_reads import Transaction, TxPlayer

VERIFIED, PENDING, NOT_EXECUTED = "verified", "pending", "not_executed"
VERIFIABLE_KINDS = ("add_drop", "waiver_claim", "trade", "il_activation")
RESOLVED = "successful"  # Yahoo transaction status of a completed move


def _no_resolve(tp: TxPlayer) -> str | None:
    return None


@dataclass(frozen=True)
class YahooView:
    """Everything Yahoo currently shows that the verifier needs (read-only)."""

    my_team_key: str
    owners: Mapping[str, str]  # espn id -> team_key (current rosters)
    my_slots: Mapping[str, str]  # espn id -> my selected slot
    as_of: datetime  # when the rosters / transactions were read
    transactions: Sequence[Transaction] = ()
    resolve: Callable[[TxPlayer], str | None] = _no_resolve  # transaction player -> espn id
    team_names: Mapping[str, str] = field(default_factory=dict)
    lineups: Mapping[str, Mapping[str, str]] = field(default_factory=dict)  # ET day -> {espn id: my slot}
    offers: Sequence[trade_offers.Offer] = ()  # Yahoo's pending trade offers seen on snapshots (read-only)


@dataclass(frozen=True)
class _Ev:
    tx: Transaction
    tp: TxPlayer
    pid: str | None
    ts: datetime


def _dt(iso: str | None) -> datetime | None:
    if not iso:
        return None
    d = datetime.fromisoformat(iso)
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone(UTC).isoformat(timespec="seconds") if dt else None


def _short(tx_key: str) -> str:
    return "tr." + tx_key.rsplit(".", 1)[-1]


def _et_day(dt: datetime) -> str:
    return dt.astimezone(ET).date().isoformat()


def _events(view: YahooView) -> list[_Ev]:
    out = []
    for tx in view.transactions:
        if tx.timestamp is None or tx.status.lower() != RESOLVED:
            continue
        out += [_Ev(tx, tp, view.resolve(tp), tx.timestamp) for tp in tx.players]
    return sorted(out, key=lambda e: e.ts)


def _leg_key(ev: _Ev) -> tuple[str, str]:
    return ev.tx.key, ev.tp.player_key or ev.tp.player_id or ev.tp.name


def _is(ev: _Ev, pid: str, name: str | None) -> bool:
    return ev.pid == pid or bool(name and ev.tp.name.casefold() == name.casefold())


class _Case:
    """One proposal being verified."""

    def __init__(self, p: props.Proposal, prior: dict[str, Any] | None):
        self.p = p
        self.kind = props.effective_kind(p.kind, p.payload)
        self.pay = p.payload
        self.prior = prior or {}
        self.approved = _dt(p.decided_at) or _dt(p.created_at)
        self.confirmed = _dt(p.executed_at) or self.approved
        window = "verify.trade_window_hours" if self.kind == "trade" else "verify.claim_window_hours"
        self.window_end = self.confirmed + timedelta(hours=float(C(window)))
        self.legs: list[dict[str, Any]] = []
        self.notes: list[str] = []
        self.response: dict[str, Any] | None = None  # trades: what the partner did with the offer

    def leg(self, role: str, player: str, name: str | None, status: str, detail: str, **extra: Any) -> dict[str, Any]:
        row = {"role": role, "player": player, "name": name or player, "status": status, "detail": detail, **extra}
        self.legs.append(row)
        return row


class _Verifier:
    def __init__(self, view: YahooView, now: datetime):
        self.view, self.now = view, now
        self.events = _events(view)
        self.ledger: dict[tuple[str, str], str] = {}
        self.links: dict[str, trade_offers.Offer] = {}  # trade proposal ref -> the Yahoo offer it became

    # ---------------------------------------------------------------- shared helpers

    def _fresh(self, since: datetime) -> bool:
        return self.view.as_of >= since

    def _expired(self, c: _Case) -> bool:
        return self.now >= c.window_end and self._fresh(c.window_end)

    def _team(self, key: str) -> str:
        return self.view.team_names.get(key, key)

    def _find(self, c: _Case, pid: str, name: str | None, action: str, side: str) -> tuple[_Ev | None, str | None]:
        """First unclaimed transaction player-leg for `pid` on my team inside the proposal's window. Returns
        (event, None) and claims it, or (None, ref of the proposal that already holds the only match)."""
        shared = None
        for ev in self.events:
            if ev.ts < c.approved or ev.ts > c.window_end or ev.tp.action != action or not _is(ev, pid, name):
                continue
            team = ev.tp.destination_team_key if side == "to" else ev.tp.source_team_key
            if team != self.view.my_team_key:
                continue
            key = _leg_key(ev)
            holder = self.ledger.get(key)
            if holder in (None, c.p.ref):
                self.ledger[key] = c.p.ref
                return ev, None
            shared = shared or holder
        return None, shared

    def _verified_leg(self, c: _Case, role: str, pid: str, name: str | None, ev: _Ev) -> None:
        c.leg(
            role,
            pid,
            name,
            VERIFIED,
            f"{_short(ev.tx.key)}: {ev.tp.action} of {ev.tp.name} at {_iso(ev.ts)}",
            via="transaction",
            tx=ev.tx.key,
            at=_iso(ev.ts),
            leg_key=list(_leg_key(ev)),
        )

    # ---------------------------------------------------------------- essential legs (pass A)

    def acquire(self, c: _Case) -> None:
        add, name = str(c.pay.get("add")), c.pay.get("add_name")
        mine = self.view.my_team_key
        ev, shared = self._find(c, add, name, "add", "to")
        if ev:
            self._verified_leg(c, "add", add, name, ev)
        elif shared:
            c.leg("add", add, name, "shared", f"that add is already credited to {shared}")
        elif self.view.owners.get(add) == mine and not c.prior.get("state") == NOT_EXECUTED:
            c.leg(
                "add",
                add,
                name,
                VERIFIED,
                f"{name or add} is on your roster (no matching transaction seen)",
                via="roster",
            )
        elif add in self.view.owners and self.view.owners[add] != mine:
            owner = self._team(self.view.owners[add])
            taker = next((e for e in self.events if e.pid == add and e.tp.action == "add" and e.ts >= c.approved), None)
            via = f" ({_short(taker.tx.key)} at {_iso(taker.ts)})" if taker else ""
            status = "contradicted" if self._fresh(c.confirmed) else PENDING
            c.leg("add", add, name, status, f"{name or add} is on {owner}'s roster{via}, not yours")
        elif self._expired(c):
            c.leg("add", add, name, "contradicted", f"no add of {name or add} by you seen in Yahoo")
        else:
            c.leg("add", add, name, PENDING, f"no add of {name or add} seen yet")

    def trade(self, c: _Case) -> None:
        give, get = [str(x) for x in c.pay.get("give") or []], [str(x) for x in c.pay.get("get") or []]
        gnames = list(c.pay.get("give_names") or [])
        tnames = list(c.pay.get("get_names") or [])
        gname = dict(zip(give, gnames, strict=False))
        tname = dict(zip(get, tnames, strict=False))
        mine, partner = self.view.my_team_key, str(c.pay.get("partner"))
        offer = self.links.get(c.p.ref)
        for tx in self.view.transactions:
            if tx.type != "trade" or tx.status.lower() != RESOLVED or tx.timestamp is None:
                continue
            if tx.timestamp < c.approved or tx.timestamp > c.window_end:
                continue
            evs = [e for e in self.events if e.tx.key == tx.key]
            legs = []
            for role, ids, names, side in (("give", give, gname, "source"), ("get", get, tname, "destination")):
                for p in ids:
                    ev = next(
                        (e for e in evs if _is(e, p, names.get(p)) and getattr(e.tp, f"{side}_team_key") == mine), None
                    )
                    legs.append((role, p, names.get(p), ev))
            keys = [_leg_key(ev) for *_, ev in legs if ev]
            if (
                not legs
                or any(ev is None for *_, ev in legs)
                or any(self.ledger.get(k, c.p.ref) != c.p.ref for k in keys)
            ):
                continue
            for k in keys:
                self.ledger[k] = c.p.ref
            for role, p, nm, ev in legs:
                self._verified_leg(c, role, p, nm, ev)
            c.response = self._trade_response(c, offer, tx=tx)
            return
        owners = self.view.owners
        landed = (
            bool(give or get)
            and all(owners.get(p) == partner for p in give)
            and all(owners.get(p) == mine for p in get)
        )
        if landed and not c.prior.get("state") == NOT_EXECUTED:
            c.response = self._trade_response(c, offer, roster=True)
            for p in give:
                c.leg(
                    "give",
                    p,
                    gname.get(p),
                    VERIFIED,
                    "now on the partner's roster (no matching transaction seen)",
                    via="roster",
                )
            for p in get:
                c.leg(
                    "get", p, tname.get(p), VERIFIED, "now on your roster (no matching transaction seen)", via="roster"
                )
            return
        where = [
            f"{gname.get(p, p)} still on {self._team(owners.get(p, 'no team'))}"
            for p in give
            if owners.get(p) != partner
        ]
        where += [
            f"{tname.get(p, p)} still on {self._team(owners.get(p, 'no team'))}" for p in get if owners.get(p) != mine
        ]
        resp = c.response = self._trade_response(c, offer)
        if resp["response"] in trade_offers.NEGATIVE and resp["source"] != "window":  # decided by the partner
            who = "the owner reports" if resp["source"] == "owner" else "Yahoo shows"
            quote = f' (owner: "{resp["reason"][:160]}")' if resp.get("reason") else ""
            c.leg(
                "trade",
                ",".join(give + get),
                None,
                "contradicted",
                f"offer {resp['response']}: {who} it was not accepted; {resp['basis']}{quote}",
            )
        elif self._expired(c):
            c.leg(
                "trade",
                ",".join(give + get),
                None,
                "contradicted",
                "no trade transaction after approval; "
                + "; ".join(where)
                + f" (rejected, withdrawn or vetoed); {resp['response']}: {resp['basis']}",
            )
        else:
            c.leg(
                "trade",
                ",".join(give + get),
                None,
                PENDING,
                "no completed trade seen yet (league vote / review window still open)",
            )

    def _trade_response(
        self, c: _Case, offer: trade_offers.Offer | None, tx: Transaction | None = None, roster: bool = False
    ) -> dict[str, Any]:
        """What the partner did with the offer. A completed Yahoo trade (or the players landing) wins; then the
        owner's recorded response; then Yahoo's pending list (an offer that left it with no trade was declined or
        withdrawn); then the verify.trade_window_hours deadline (`ignored`)."""
        owner = (c.p.execution or {}).get("owner_response") or {}
        hours = float(C("verify.trade_window_hours"))
        source, at, quote = "yahoo_pending", None, None
        if tx is not None:
            response, at, source = "accepted", tx.timestamp, "yahoo_transaction"
            basis = f"Yahoo completed it as {_short(tx.key)}"
        elif roster:
            response, source, basis = "accepted", "roster", "the players are on the new rosters"
        elif owner:
            response, source, quote = owner["response"], "owner", owner.get("reason")
            at, basis = _dt(owner.get("response_at")), "reported by the owner"
            if response == "accepted" and self._expired(c):
                basis = f"the owner says accepted, but Yahoo shows no completed trade within {hours:g}h"
        elif offer is not None:
            response, at, basis = trade_offers.offer_response(offer, self.now, None)
            source = "yahoo_status" if trade_offers.status_response(offer.status) else "yahoo_pending"
            if response == trade_offers.PENDING and self._expired(c):
                response, source = "ignored", "window"
                basis = f"still pending in Yahoo more than {hours:g}h after the owner sent it"
        elif self._expired(c):
            response, source = "ignored", "window"
            basis = f"never seen in Yahoo's pending list and no completed trade within {hours:g}h"
        else:
            response, basis = trade_offers.PENDING, "not seen in Yahoo's pending list yet"
        sent = c.confirmed
        out = {
            "response": response,
            "source": source,
            "basis": basis,
            "partner": str(c.pay.get("partner")),
            "partner_name": self._team(str(c.pay.get("partner"))),
            "sent_at": _iso(sent),
            "response_at": _iso(at),
            "hours_to_response": round(max((at - sent).total_seconds(), 0.0) / 3600, 1) if at else None,
            "offer": offer.tx_key if offer else None,
            "offer_status": offer.status if offer else None,
            "reason": quote,
        }
        return {k: v for k, v in out.items() if v is not None}

    def il_activation(self, c: _Case) -> None:
        act, name = str(c.pay.get("activate")), c.pay.get("activate_name")
        days = sorted(d for d in self.view.lineups if d >= _et_day(c.approved))
        slots = {d: str(self.view.lineups[d].get(act, "")).upper() for d in days}
        active = [d for d in days if slots[d] and slots[d] not in IL_SLOTS]
        now_slot = str(self.view.my_slots.get(act, "")).upper()
        if active:
            first = active[0]
            c.leg(
                "activate",
                act,
                name,
                VERIFIED,
                f"{name or act} out of an IL slot ({slots[first]}) on {first}",
                via="lineup",
                at=first,
            )
            back = next((d for d in days if d > first and slots[d] in IL_SLOTS), None)
            if back:
                c.notes.append(f"{name or act} was back in an IL slot on {back} (Yahoo lineup history)")
        elif now_slot and now_slot not in IL_SLOTS and not c.prior.get("state") == NOT_EXECUTED:
            c.leg("activate", act, name, VERIFIED, f"{name or act} is in {now_slot} now", via="roster")
        elif self._expired(c):
            where = f"still in the {now_slot} slot" if now_slot else "not on your roster"
            c.leg("activate", act, name, "contradicted", f"{name or act} {where}; the activation never showed in Yahoo")
        else:
            c.leg("activate", act, name, PENDING, f"{name or act} is still in an IL slot")

    # ---------------------------------------------------------------- drop legs (pass B)

    def drop(self, c: _Case) -> None:
        drop = c.pay.get("drop")
        if not drop:
            return
        drop, name = str(drop), c.pay.get("drop_name")
        ev, shared = self._find(c, drop, name, "drop", "from")
        if ev:
            self._verified_leg(c, "drop", drop, name, ev)
        elif shared:
            c.leg("drop", drop, name, "shared", f"his drop transaction is already credited to {shared}")
        elif self.view.owners.get(drop) != self.view.my_team_key:
            c.leg(
                "drop",
                drop,
                name,
                VERIFIED,
                f"{name or drop} is off your roster (no matching transaction seen)",
                via="roster",
            )
        else:
            c.leg("drop", drop, name, "missing", f"{name or drop} is still on your roster")
            c.notes.append(f"the drop of {name or drop} did not happen")

    # ---------------------------------------------------------------- result

    def result(self, c: _Case) -> dict[str, Any]:
        essential = [lg for lg in c.legs if lg["role"] != "drop"]
        bad = [lg for lg in essential if lg["status"] in ("contradicted", "shared")]
        waiting = [lg for lg in essential if lg["status"] == PENDING]
        state = NOT_EXECUTED if bad else PENDING if waiting or not essential else VERIFIED
        reason = "; ".join(lg["detail"] for lg in (bad or waiting or essential))
        credited = {"in": [], "out": []}
        if state == VERIFIED:
            for lg in c.legs:
                if lg["status"] != VERIFIED:
                    continue
                credited["out" if lg["role"] in ("drop", "give") else "in"].append(lg["player"])
        credited_legs = [lg for lg in c.legs if lg["status"] == VERIFIED and lg.get("at")] if state == VERIFIED else []
        days = [_et_day(_dt(lg["at"])) if lg["via"] == "transaction" else lg["at"] for lg in credited_legs]
        out = {
            "state": state,
            "reason": reason,
            "checked_at": _iso(self.now),
            "data_as_of": _iso(self.view.as_of),
            "legs": c.legs,
            "notes": c.notes,
            "transactions": sorted({lg["tx"] for lg in c.legs if lg.get("tx") and state == VERIFIED}),
            "credited": credited,
            "effective_et": min(days, default=None),  # first ET day the move shows in Yahoo (grading starts here)
        }
        if c.response:
            out["response"] = c.response
        return out


def reconcile(
    proposals: Sequence[props.Proposal], view: YahooView, now: datetime | None = None
) -> dict[str, dict[str, Any]]:
    """{proposal ref: verification} for every done/not_executed add/drop, waiver claim, trade and IL activation.
    A `verified` proposal keeps its stored verification (sticky) and its transactions stay credited to it."""
    now = now or datetime.now(UTC)
    v = _Verifier(view, now)
    cases = []
    claimed_offers: set[str] = set()
    out: dict[str, dict[str, Any]] = {}
    for p in sorted(proposals, key=lambda x: (_dt(x.decided_at) or _dt(x.created_at), x.id)):
        if p.status not in ("done", "not_executed") or props.effective_kind(p.kind, p.payload) not in VERIFIABLE_KINDS:
            continue
        prior = (p.execution or {}).get("verification")
        if prior and prior.get("state") == VERIFIED:
            for lg in prior.get("legs") or []:
                if lg.get("leg_key"):
                    v.ledger[(lg["leg_key"][0], lg["leg_key"][1])] = p.ref
            if offer_key := (prior.get("response") or {}).get("offer"):
                claimed_offers.add(offer_key)
            out[p.ref] = prior
            continue
        cases.append(_Case(p, prior))
    v.links = trade_offers.link(
        [
            (c.p.ref, str(c.pay.get("partner")), c.pay.get("give") or [], c.pay.get("get") or [], c.approved)
            for c in cases
            if c.kind == "trade"
        ],
        view.offers,
        claimed_offers,
    )
    for c in cases:
        {"trade": v.trade, "il_activation": v.il_activation}.get(c.kind, v.acquire)(c)
    for c in cases:  # an IL activation needs its drop to free the spot, so it claims the drop transaction first
        if c.kind == "il_activation":
            v.drop(c)
    for c in cases:
        if c.kind in ("add_drop", "waiver_claim"):
            v.drop(c)
    for c in cases:
        out[c.p.ref] = v.result(c)
    return out


def credited_moves(p: props.Proposal) -> tuple[list[str], list[str]] | None:
    """(players gained, players given up) credited to a verified proposal, or None when it is not verified."""
    ver = (p.execution or {}).get("verification") or {}
    if p.status != "done" or ver.get("state") != VERIFIED:
        return None
    cred = ver.get("credited") or {}
    return list(cred.get("in") or []), list(cred.get("out") or [])


def verify_all(conn: sqlite3.Connection, view: YahooView, now: datetime | None = None) -> list[dict[str, Any]]:
    """Reconcile every done/not_executed move against Yahoo and store the result on the proposal (status
    done <-> not_executed). Returns one row per verifiable proposal."""
    proposals = [p for status in ("done", "not_executed") for p in props.list_proposals(conn, status, 1000)]
    results = reconcile(proposals, view, now)
    rows = []
    for p in sorted(proposals, key=lambda x: x.id):
        ver = results.get(p.ref)
        if ver is None:
            continue
        old = (p.execution or {}).get("verification") or {}
        if ver is not old and (
            ver.get("state") != old.get("state")
            or ver.get("legs") != old.get("legs")
            or ver.get("response") != old.get("response")
        ):
            p = props.record_verification(conn, p.id, ver)
        rows.append({"ref": p.ref, "kind": p.kind, "title": p.title, "status": p.status, **brief(ver)})
    return rows


def brief(ver: Mapping[str, Any] | None) -> dict[str, Any]:
    if not ver:
        return {"verification": "unverified", "reason": "not verified against Yahoo yet"}
    out = {"verification": ver.get("state"), "reason": ver.get("reason")}
    if ver.get("notes"):
        out["notes"] = ver["notes"]
    if ver.get("response"):
        out["trade_response"] = ver["response"].get("response")
    return out
