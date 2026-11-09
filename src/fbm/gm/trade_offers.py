"""What happened to a trade offer after the owner sent it?

Yahoo's completed-transaction feed only shows trades that happened, so a declined offer leaves no trace there. Two
read-only sources fill the gap:

* every snapshot records Yahoo's PENDING trade offers (`observe`, table `trade_offers`): an offer that was pending
  and later is not listed, with no completed trade, was declined or withdrawn (Yahoo does not say which);
* the OWNER can tell FBM what the partner said (`proposals.record_trade_response`, stored in the proposal's
  `execution["owner_response"]` with the owner's own words).

`verification.py` turns both into the proposal's trade response (`verification["response"]`). Precedence: a completed
matching Yahoo trade always wins; then the owner's recorded response; then what the pending list shows; then the
verify.trade_window_hours deadline (`ignored`). This module holds the pure matching, the observation store and the
outcome statistics (manager profiles, weekly retro, partner-order weights). FBM never writes to Yahoo.
"""

from __future__ import annotations

import json
import sqlite3
import statistics
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from fbm.constants import C
from fbm.gm import proposals as props
from fbm.yahoo.league_reads import Transaction, TxPlayer

RESPONSES = props.TRADE_RESPONSES
NEGATIVE = tuple(r for r in RESPONSES if r != "accepted")
PENDING = "pending"
# Yahoo status text on a listed offer that already tells the outcome (the pending list normally shows "proposed").
_STATUS_RESPONSE = {
    "rejected": "declined",
    "declined": "declined",
    "vetoed": "vetoed",
    "disallowed": "vetoed",
    "cancelled": "withdrawn",
    "canceled": "withdrawn",
    "withdrawn": "withdrawn",
    "expired": "ignored",
}
# HT ROS value premium to us (received minus sent, % of sent): band key -> [low, high)
PREMIUM_BANDS = (("below_0", None, 0.0), ("0_to_10", 0.0, 10.0), ("10_to_25", 10.0, 25.0), ("above_25", 25.0, None))
Resolve = Callable[[TxPlayer], str | None]


@dataclass(frozen=True)
class Offer:
    tx_key: str
    direction: str  # outgoing (mine to them) | incoming
    partner: str  # the other team's key
    status: str
    proposed_at: datetime | None
    first_seen: datetime
    last_seen: datetime
    gone_at: datetime | None  # first snapshot that no longer listed it
    give: tuple[str, ...]  # ESPN ids (or "yahoo:<id>" when unmatched) leaving my team
    get: tuple[str, ...]
    give_names: tuple[str, ...] = ()
    get_names: tuple[str, ...] = ()

    @property
    def sent_at(self) -> datetime:
        return self.proposed_at or self.first_seen


def _dt(iso: str | None) -> datetime | None:
    if not iso:
        return None
    d = datetime.fromisoformat(iso)
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone(UTC).isoformat(timespec="seconds") if dt else None


def status_response(status: str) -> str | None:
    """The response a Yahoo status text states outright (rejected, vetoed, cancelled ...), else None."""
    return _STATUS_RESPONSE.get((status or "").strip().lower())


# ------------------------------------------------------------------ observing Yahoo's pending list


def _legs(tx: Transaction, my_key: str, mapper: Callable[[str, str, str], str | None]):
    give, get = [], []
    for p in tx.players:
        try:
            pid = mapper(p.player_id, p.name, p.nba_team)
        except Exception:  # an unmatched player is kept under its Yahoo id
            pid = None
        row = {"id": pid or f"yahoo:{p.player_id}", "name": p.name}
        if p.source_team_key == my_key:
            give.append(row)
        elif p.destination_team_key == my_key:
            get.append(row)
    return give, get


def observe(
    conn: sqlite3.Connection,
    txs: Iterable[Transaction],
    my_key: str,
    mapper: Callable[[str, str, str], str | None],
    now: datetime,
) -> dict[str, Any]:
    """Record Yahoo's current pending trades (offers I sent or received). `txs` must be a successful read of the
    pending list: stored offers it no longer lists are marked gone at `now`. Idempotent per snapshot."""
    stamp, seen, new = _iso(now), set(), 0
    for tx in txs:
        if tx.type != "pending_trade" or not tx.key:
            continue
        if tx.trader_team_key == my_key:
            direction, partner = "outgoing", tx.tradee_team_key
        elif tx.tradee_team_key == my_key:
            direction, partner = "incoming", tx.trader_team_key
        else:
            continue
        seen.add(tx.key)
        give, get = _legs(tx, my_key, mapper)
        if conn.execute("SELECT 1 FROM trade_offers WHERE tx_key = ?", (tx.key,)).fetchone():
            conn.execute(
                "UPDATE trade_offers SET status=?, last_seen=?, gone_at=NULL, give=?, get=? WHERE tx_key=?",
                (tx.status, stamp, json.dumps(give), json.dumps(get), tx.key),
            )
        else:
            new += 1
            conn.execute(
                "INSERT INTO trade_offers(tx_key, direction, partner_team_key, status, proposed_at, first_seen, "
                "last_seen, give, get) VALUES (?,?,?,?,?,?,?,?,?)",
                (tx.key, direction, partner, tx.status, _iso(tx.timestamp), stamp, stamp, json.dumps(give),
                 json.dumps(get)),
            )  # fmt: skip
    gone = 0
    for (key,) in conn.execute("SELECT tx_key FROM trade_offers WHERE gone_at IS NULL").fetchall():
        if key not in seen:
            conn.execute("UPDATE trade_offers SET gone_at=? WHERE tx_key=?", (stamp, key))
            gone += 1
    conn.commit()
    return {"pending_seen": len(seen), "new": new, "gone": gone}


def load_offers(conn: sqlite3.Connection) -> list[Offer]:
    out = []
    for r in conn.execute("SELECT * FROM trade_offers ORDER BY COALESCE(proposed_at, first_seen), tx_key"):
        give, get = json.loads(r["give"]), json.loads(r["get"])
        out.append(
            Offer(
                tx_key=r["tx_key"],
                direction=r["direction"],
                partner=r["partner_team_key"],
                status=r["status"] or "",
                proposed_at=_dt(r["proposed_at"]),
                first_seen=_dt(r["first_seen"]),
                last_seen=_dt(r["last_seen"]),
                gone_at=_dt(r["gone_at"]),
                give=tuple(str(x["id"]) for x in give),
                get=tuple(str(x["id"]) for x in get),
                give_names=tuple(x.get("name", "") for x in give),
                get_names=tuple(x.get("name", "") for x in get),
            )
        )
    return out


# ------------------------------------------------------------------ matching (pure)


def link(
    cases: Sequence[tuple[str, str, Sequence[str], Sequence[str], datetime]],
    offers: Sequence[Offer],
    claimed: Iterable[str] = (),
) -> dict[str, Offer]:
    """{proposal ref: the outgoing Yahoo offer it became}. `cases` = (ref, partner team key, give ids, get ids,
    approved_at) ordered by approval. An offer matches on partner + both sides' player ids, must be sent after the
    owner approved, and is linked to at most one proposal (earliest first); `claimed` offers are taken already."""
    taken, out = set(claimed), {}
    for ref, partner, give, get, approved in cases:
        want = (sorted(str(x) for x in give), sorted(str(x) for x in get))
        for o in sorted(offers, key=lambda x: x.sent_at):
            if o.direction != "outgoing" or o.tx_key in taken or o.partner != partner or o.sent_at < approved:
                continue
            if (sorted(o.give), sorted(o.get)) == want:
                taken.add(o.tx_key)
                out[ref] = o
                break
    return out


def completed_trade(
    trades: Iterable[Transaction],
    resolve: Resolve,
    mine: str,
    give: Sequence[str],
    get: Sequence[str],
    after: datetime,
) -> Transaction | None:
    """The completed (successful) Yahoo trade that moved exactly `give` off my team and `get` onto it, after `after`."""
    want = (sorted(str(x) for x in give), sorted(str(x) for x in get))
    for tx in trades:
        if tx.type != "trade" or tx.status.lower() != "successful" or tx.timestamp is None or tx.timestamp < after:
            continue

        def ids(side: str, tx=tx) -> list[str]:
            return sorted(
                resolve(p) or f"yahoo:{p.player_id}" for p in tx.players if getattr(p, f"{side}_team_key") == mine
            )

        if (ids("source"), ids("destination")) == want:
            return tx
    return None


def offer_response(
    o: Offer, now: datetime, window_hours: float | None, tx: Transaction | None = None
) -> tuple[str, datetime | None, str]:
    """(response, when, basis) for an offer from what Yahoo shows. `pending` until something decides it; with
    `window_hours` an offer still pending that long after it was sent is `ignored` (None: the caller judges that)."""
    if tx is not None:
        return "accepted", tx.timestamp, f"Yahoo completed it as {tx.key.rsplit('.', 1)[-1]}"
    if stated := status_response(o.status):
        return stated, o.gone_at or o.last_seen, f"Yahoo lists the offer as '{o.status}'"
    if o.gone_at:
        return (
            "declined",
            o.gone_at,
            "left Yahoo's pending list with no completed trade (declined or withdrawn; Yahoo does not say which; "
            "time = the snapshot that noticed)",
        )
    if window_hours is not None and now - o.sent_at > timedelta(hours=window_hours):
        return "ignored", None, f"still pending in Yahoo more than {window_hours:g}h after it was sent"
    return PENDING, None, "still pending in Yahoo"


# ------------------------------------------------------------------ outcome rows & statistics


def ht_premium_pct(evidence: Mapping[str, Any] | None) -> float | None:
    """HT ROS value received minus sent, as % of sent, from a trade proposal's evidence (find_trades `ht_value`)."""
    ev = evidence or {}
    nested = ev.get("ht_value")
    for value in (nested.get("pct_of_sent") if isinstance(nested, Mapping) else None, ev.get("ht_value_pct_of_sent")):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    realism = ev.get("trade_realism")
    for rule in (realism.get("rules") or []) if isinstance(realism, Mapping) else []:
        value = rule.get("value") if isinstance(rule, Mapping) and rule.get("rule") == "ht_value_premium" else None
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value) * 100.0  # create_proposal's realism gate stores a fraction
    return None


def premium_band(pct: float | None) -> str:
    if pct is None:
        return "unknown"
    return next(k for k, lo, hi in PREMIUM_BANDS if (lo is None or pct >= lo) and (hi is None or pct < hi))


def _hours(sent: datetime | None, at: datetime | None) -> float | None:
    return round(max((at - sent).total_seconds(), 0.0) / 3600, 1) if sent and at else None


def sent_rows(
    proposals: Iterable[props.Proposal],
    offers: Sequence[Offer],
    trades: Sequence[Transaction],
    resolve: Resolve,
    mine: str,
    now: datetime,
    window_hours: float,
    names: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """One row per trade offer I sent: owner-confirmed trade proposals (their stored response) plus outgoing Yahoo
    offers that match no proposal (sent outside FBM). `response` = accepted | declined | ... | pending."""
    names, rows, claimed = names or {}, [], set()
    for p in proposals:
        if props.effective_kind(p.kind, p.payload) != "trade" or p.status not in ("done", "not_executed"):
            continue
        ex = p.execution or {}
        ver = ex.get("verification") or {}
        owner = ex.get("owner_response")
        resp = ver.get("response") or (
            {
                "response": owner["response"],
                "response_at": owner.get("response_at"),
                "reason": owner.get("reason"),
                "source": "owner",
            }
            if owner
            else {"response": "accepted", "source": "verification"}
            if ver.get("state") == "verified"
            else {"response": PENDING}
        )
        if resp.get("offer"):
            claimed.add(resp["offer"])
        partner = str(p.payload.get("partner") or "")
        sent = _dt(p.executed_at)
        rows.append(
            {
                "ref": p.ref,
                "partner": partner,
                "partner_name": p.payload.get("partner_name") or names.get(partner) or partner,
                "sent_at": _iso(sent),
                "response": resp["response"],
                "response_at": resp.get("response_at"),
                "hours_to_response": resp.get("hours_to_response")
                if resp.get("hours_to_response") is not None
                else _hours(sent, _dt(resp.get("response_at"))),
                "source": resp.get("source"),
                "reason": resp.get("reason"),
                "ht_premium_pct": ht_premium_pct(p.evidence),
            }
        )
    for o in offers:
        if o.direction != "outgoing" or o.tx_key in claimed:
            continue
        tx = completed_trade(trades, resolve, mine, o.give, o.get, o.sent_at)
        response, at, basis = offer_response(o, now, window_hours, tx)
        rows.append(
            {
                "ref": None,
                "offer": o.tx_key,
                "partner": o.partner,
                "partner_name": names.get(o.partner, o.partner),
                "sent_at": _iso(o.sent_at),
                "response": response,
                "response_at": _iso(at),
                "hours_to_response": _hours(o.sent_at, at),
                "source": "yahoo_transaction" if tx else "yahoo_pending",
                "reason": None,
                "ht_premium_pct": None,
            }
        )
    return sorted(rows, key=lambda r: r["sent_at"] or "")  # oldest first: "the last N" are the most recent


def _rate(accepted: int, outcomes: int) -> float | None:
    return round(accepted / outcomes, 2) if outcomes else None


def summarize(rows: Sequence[Mapping[str, Any]], incoming: int = 0) -> dict[str, Any]:
    """Compact outcome counts for one set of offers I sent (a partner, a premium band, or all)."""
    counts = {r: sum(1 for x in rows if x["response"] == r) for r in (*RESPONSES, PENDING)}
    outcomes = len(rows) - counts[PENDING]
    hours = [
        x["hours_to_response"] for x in rows if x["response"] != PENDING and x.get("hours_to_response") is not None
    ]
    reasons = [
        {"ref": x["ref"], "response": x["response"], "reason": str(x["reason"])[:240]}
        for x in rows
        if x.get("reason") and x["response"] != PENDING
    ]
    need = int(C("trade_offers.min_outcomes"))
    return {
        "offers_received_from_us": len(rows),
        "outcomes": outcomes,
        **counts,
        "acceptance_rate": _rate(counts["accepted"], outcomes),
        "median_response_hours": round(statistics.median(hours), 1) if hours else None,
        "owner_reasons": reasons[-5:],
        "offers_to_us": incoming,
        "weighted": outcomes >= need,
        "weighting": "none: counts only" if outcomes < need else "acceptance rate weights this partner's order",
    }


def partner_stats(rows: Sequence[Mapping[str, Any]], offers: Sequence[Offer] = ()) -> dict[str, dict[str, Any]]:
    """{partner team key: summarize(...)} over every offer I sent them; incoming offers are counted per partner."""
    keys = {r["partner"] for r in rows} | {o.partner for o in offers if o.direction == "incoming"}
    return {
        k: summarize(
            [r for r in rows if r["partner"] == k],
            sum(1 for o in offers if o.direction == "incoming" and o.partner == k),
        )
        for k in keys
    }


def acceptance_weight(stats: Mapping[str, Any] | None) -> float:
    """Multiplier for a partner's complementary fit. 1.0 (no weighting) until the partner has
    trade_offers.min_outcomes recorded responses."""
    if not stats or stats["outcomes"] < int(C("trade_offers.min_outcomes")) or stats["acceptance_rate"] is None:
        return 1.0
    floor = float(C("trade_offers.weight_floor"))
    return round(floor + (1 - floor) * float(stats["acceptance_rate"]), 3)


def compact_stats(stats: Mapping[str, Any] | None) -> dict[str, Any]:
    """The few fields trade tools carry per partner (partner_intel)."""
    if not stats:
        return {"offers_received_from_us": 0, "outcomes": 0, "acceptance_weight": 1.0}
    return {
        "offers_received_from_us": stats["offers_received_from_us"],
        "accepted": stats["accepted"],
        "declined": stats["declined"],
        "outcomes": stats["outcomes"],
        "median_response_hours": stats["median_response_hours"],
        "acceptance_weight": acceptance_weight(stats),
    }


def retro_summary(
    rows: Sequence[Mapping[str, Any]], lo: str, hi: str, names: Mapping[str, str] | None = None
) -> dict[str, Any]:
    """Trade acceptance for the weekly retro: all offers, by partner, by HT value premium band, and the offers
    sent or answered in the week [lo, hi) (UTC ISO). Rates are shown with counts; `enough_data` marks groups with at
    least trade_offers.min_outcomes responses."""
    names, need = names or {}, int(C("trade_offers.min_outcomes"))

    def group(key: Callable[[Mapping[str, Any]], str], label: str) -> list[dict[str, Any]]:
        keys = sorted({key(r) for r in rows})
        out = []
        for k in keys:
            s = summarize([r for r in rows if key(r) == k])
            out.append(
                {
                    label: names.get(k, k) if label == "partner" else k,
                    "offers": s["offers_received_from_us"],
                    "outcomes": s["outcomes"],
                    "accepted": s["accepted"],
                    "acceptance_rate": s["acceptance_rate"],
                    "median_response_hours": s["median_response_hours"],
                    "enough_data": s["outcomes"] >= need,
                }
            )
        return out

    overall = summarize(rows)
    this_week = [
        {k: r.get(k) for k in ("ref", "partner_name", "sent_at", "response", "response_at", "source", "reason")}
        for r in rows
        if lo <= (r.get("sent_at") or "") < hi or lo <= (r.get("response_at") or "") < hi
    ]
    return {
        "min_outcomes": need,
        "overall": {
            k: overall[k]
            for k in (
                "offers_received_from_us",
                "outcomes",
                "accepted",
                "declined",
                "ignored",
                "countered",
                "vetoed",
                "withdrawn",
                "pending",
                "acceptance_rate",
                "median_response_hours",
            )
        },  # fmt: skip
        "by_partner": group(lambda r: r["partner"], "partner"),
        "by_ht_premium_band": group(lambda r: premium_band(r.get("ht_premium_pct")), "band"),
        "this_week": this_week,
        "bands": "ht_value % of sent (received minus sent): below_0 = we overpay; above_25 = we ask a lot more",
        "note": "Only recorded or Yahoo-observed responses count; below min_outcomes treat rates as anecdotes.",
    }
