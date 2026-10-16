"""League transaction history (SQLite) and the incremental, read-only Yahoo sync that fills it."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Any

from fbm.constants import C
from fbm.store.db import now_iso
from fbm.yahoo.league_reads import Transaction, TxPlayer, fetch_transactions


def known_keys(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT tx_key FROM league_transactions")}


def store(conn: sqlite3.Connection, txs: list[Transaction]) -> int:
    """Insert or refresh transactions (trade notes are not stored: untrusted free text). Returns new rows."""
    known, new = known_keys(conn), 0
    for tx in txs:
        if not tx.key:
            continue
        new += tx.key not in known
        conn.execute(
            """INSERT INTO league_transactions(tx_key, type, status, ts_utc, trader_team_key, tradee_team_key,
               fetched_at) VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(tx_key) DO UPDATE SET status=excluded.status, fetched_at=excluded.fetched_at""",
            (
                tx.key,
                tx.type,
                tx.status,
                tx.timestamp.isoformat() if tx.timestamp else None,
                tx.trader_team_key,
                tx.tradee_team_key,
                now_iso(),
            ),
        )
        conn.execute("DELETE FROM league_transaction_players WHERE tx_key = ?", (tx.key,))
        conn.executemany(
            """INSERT INTO league_transaction_players(tx_key, seq, player_key, player_id, name, nba_team, action,
               source_type, source_team_key, destination_type, destination_team_key) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            [
                (
                    tx.key,
                    i,
                    p.player_key,
                    p.player_id,
                    p.name,
                    p.nba_team,
                    p.action,
                    p.source_type,
                    p.source_team_key,
                    p.destination_type,
                    p.destination_team_key,
                )
                for i, p in enumerate(tx.players)
            ],
        )
    conn.commit()
    return new


def load(conn: sqlite3.Connection) -> list[Transaction]:
    players: dict[str, list[TxPlayer]] = {}
    for r in conn.execute("SELECT * FROM league_transaction_players ORDER BY tx_key, seq"):
        players.setdefault(r["tx_key"], []).append(
            TxPlayer(
                r["player_key"] or "",
                r["player_id"] or "",
                r["name"] or "",
                r["nba_team"] or "",
                r["action"] or "",
                r["source_type"] or "",
                r["source_team_key"] or "",
                r["destination_type"] or "",
                r["destination_team_key"] or "",
            )
        )
    out = []
    for r in conn.execute("SELECT * FROM league_transactions ORDER BY ts_utc"):
        ts = datetime.fromisoformat(r["ts_utc"]) if r["ts_utc"] else None
        out.append(
            Transaction(
                key=r["tx_key"],
                type=r["type"],
                status=r["status"] or "",
                timestamp=ts.astimezone(UTC) if ts else None,
                players=tuple(players.get(r["tx_key"], [])),
                trader_team_key=r["trader_team_key"] or "",
                tradee_team_key=r["tradee_team_key"] or "",
            )
        )
    return out


def sync(conn: sqlite3.Connection, client: Any, league_key: str) -> dict[str, Any]:
    """Page backwards (newest first) until a page reaches a known transaction, is short, or the page cap."""
    known = known_keys(conn)
    size, cap = int(C("intel.sync_page_size")), int(C("intel.sync_max_pages"))
    fetched: list[Transaction] = []
    pages, reached_known = 0, False
    while pages < cap:
        page = fetch_transactions(client, league_key, size, start=pages * size)
        pages += 1
        fetched += page
        if any(tx.key in known for tx in page):
            reached_known = True
            break
        if len(page) < size:
            reached_known = True  # reached the start of the season
            break
    new = store(conn, fetched)
    return {
        "pages": pages,
        "fetched": len(fetched),
        "new": new,
        "total_stored": len(known_keys(conn)),
        "complete": reached_known,
    }
