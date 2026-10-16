"""CLI commands for the live Yahoo league (read-only views). Registered by importing this module from fbm.cli."""

from __future__ import annotations

import typer
from rich.table import Table

from fbm.cli import _svc, app, console


def _demo(r: dict) -> bool:
    if r.get("available") is False:
        console.print(f"[yellow]{r['message']}[/]")
        return True
    return False


@app.command()
def snapshot() -> None:
    """Re-read the Yahoo league now (rosters, scoreboard, standings, waivers, ownership)."""
    from fbm.gm import services as svc

    r = _svc(svc.snapshot_tool)
    if _demo(r):
        return
    start, end = r["window"]
    console.print(
        f"[bold]{r['league']}[/] week {r['week']} ({start} → {end}) · opponent {r['opponent'] or '-'} · "
        f"{r['teams']} teams · fetched {r['fetched_at']}"
    )
    if r["unmatched_players"]:
        console.print(f"[yellow]{len(r['unmatched_players'])} unmatched players[/] — {r['how_to_fix_unmatched']}")
        for p in r["unmatched_players"]:
            console.print(f"  {p['yahoo_id']}: {p['name']} ({p['team']})")


@app.command()
def ids() -> None:
    """Yahoo players not mapped to ESPN ids, and how to fix them."""
    from fbm.gm import services as svc

    r = _svc(svc.yahoo_ids_tool)
    if not r["unmatched"]:
        console.print("[green]Every Yahoo player is mapped to an ESPN id.[/]")
        return
    table = Table(title=f"Unmatched Yahoo players ({len(r['unmatched'])})")
    for col in ("Yahoo id", "Name", "Team"):
        table.add_column(col)
    for p in r["unmatched"]:
        table.add_row(p["yahoo_id"], p["name"], p["team"] or "")
    console.print(table)
    console.print(r["how_to_fix"])


@app.command()
def standings() -> None:
    """League standings (Yahoo)."""
    from fbm.gm import services as svc

    r = _svc(svc.standings_tool)
    if _demo(r):
        return
    table = Table(title=f"{r['league']} standings · as of {r['as_of']}")
    for col in ("Rank", "Team", "W-L-T", "Pct", "GB", "Seed"):
        table.add_column(col)
    for row in r["standings"]:
        name = f"[bold]{row['name']}[/]" if row["mine"] else row["name"]
        pct = f"{row['pct']:.3f}" if row.get("pct") is not None else "-"
        table.add_row(
            str(row.get("rank") or "-"),
            name,
            f"{row['wins']}-{row['losses']}-{row['ties']}",
            pct,
            row.get("games_back") or "-",
            str(row.get("playoff_seed") or "-"),
        )
    console.print(table)


def _fmt(v) -> str:
    if v is None:
        return "-"
    return f"{v:.3f}" if v < 1 and v != 0 else f"{v:g}"


@app.command()
def scoreboard() -> None:
    """This week's live category totals for every matchup (mine first)."""
    from fbm.gm import services as svc

    r = _svc(svc.scoreboard_tool)
    if _demo(r):
        return
    for m in r["matchups"]:
        a, b = (m["teams"] + ["-"])[:2]
        won = m["categories_won"]
        table = Table(title=f"{a} {won.get(a, 0)} – {won.get(b, 0)} {b} · {m['status']}")
        for col in ("Cat", a, b, "Leader"):
            table.add_column(col)
        for c in m["categories"]:
            table.add_row(c["category"], _fmt(c["values"].get(a)), _fmt(c["values"].get(b)), c["leader"] or "tie")
        console.print(table)
    console.print(f"as of {r['as_of']}")


@app.command()
def transactions(
    count: int = typer.Option(25, "--count", help="How many recent transactions."),
    team: str = typer.Option(None, "--team", help="Only transactions involving this team (name or key)."),
) -> None:
    """Recent league transactions (Yahoo)."""
    from fbm.gm import services as svc

    r = _svc(svc.transactions_tool, count, team)
    if _demo(r):
        return
    table = Table(title=f"Transactions ({r['count']})")
    for col in ("Time (UTC)", "Type", "Status", "Moves"):
        table.add_column(col)
    for tx in r["transactions"]:
        moves = "; ".join(f"{p['action']} {p['player']} ({p['from']} → {p['to']})" for p in tx["players"])
        table.add_row(tx["time_utc"] or "-", tx["type"], tx["status"], moves)
    console.print(table)


@app.command()
def offers() -> None:
    """My pending trades (incoming offers evaluated) and waiver claims. Accept/reject manually in Yahoo."""
    from fbm.gm import services as svc

    r = _svc(svc.pending_offers_tool)
    if _demo(r):
        return
    for o in r["incoming_trades"]:
        gets = [p["player"] for p in o["players"] if p["to"] != o["trader"]]
        gives = [p["player"] for p in o["players"] if p["to"] == o["trader"]]
        console.print(f"[bold]Offer from {o['trader']}[/]: you give {', '.join(gives)} · you get {', '.join(gets)}")
        ev = o.get("evaluation") or {}
        if "error" in ev:
            console.print(f"  [yellow]not evaluated: {ev['error']}[/]")
        else:
            console.print(f"  verdict [bold]{ev['verdict']}[/] · expected cats {ev['expected_cats_delta']:+.3f}")
        for step in o["manual_steps"]["accept"]:
            console.print(f"  accept: {step}")
    for label in ("outgoing_trades", "waiver_claims"):
        for tx in r[label]:
            moves = ", ".join(f"{p['action']} {p['player']}" for p in tx["players"])
            console.print(f"{label.replace('_', ' ')}: {tx['status']} · {moves}")
    if not any(r[k] for k in ("incoming_trades", "outgoing_trades", "waiver_claims")):
        console.print("No pending trades or waiver claims.")
    console.print(f"[dim]{r['note']}[/]")


@app.command()
def intel(
    team: str = typer.Option(None, "--team", "-t", help="Profile one manager (default: league-wide intel)."),
    sync: bool = typer.Option(False, "--sync", help="Sync Yahoo transaction history + manager notes first."),
) -> None:
    """Opponent intel: manager activity, streaming, trades, builds, best trade partners."""
    from fbm.gm import services as svc

    if sync:
        s = _svc(svc.sync_transactions_tool)
        if not _demo(s):
            console.print(f"Synced {s['new']} new transactions ({s['total_stored']} stored, {s['pages']} pages).")
    if team:
        p = _svc(svc.manager_profile, team)
        console.print(f"[bold]{p['name']}[/] · {p['tier']} (confidence {p['confidence']}, {p['moves']} moves)")
        console.print(
            f"adds/week {p['adds_per_week']} (recent {p['recent_adds_per_week']}) · waiver claims {p['waiver_claims']}"
            f" · FA adds {p['fa_adds']} · streaming {p['streaming_propensity']} · trades {p['trades_completed']} done"
            f" / {p['trades_proposed']} proposed · last activity {p['last_activity'] or '-'}"
        )
        console.print(
            f"strong {', '.join(p['strong_cats']) or '-'} · weak {', '.join(p['weak_cats']) or '-'} · likely punts "
            f"{', '.join(p['likely_punts']) or '-'} · fit with me {p['complementary_fit_with_me']}"
        )
        for m in p["recent_moves"]:
            console.print(f"  {m['utc']}: {'; '.join(m['moves'])}")
        if p["note"]:
            console.print(f"[dim]{p['note']}[/]")
        return
    r = _svc(svc.league_intel)
    table = Table("Team", "Tier", "Conf", "Adds/wk", "Recent", "Claims", "Stream%", "Trades")
    for row in r["activity_ranking"]:
        table.add_row(
            row["team"],
            row["tier"],
            row["confidence"],
            str(row["adds_per_week"]),
            str(row["recent_adds_per_week"]),
            str(row["waiver_claims"]),
            "-" if row["streaming_propensity"] is None else f"{row['streaming_propensity']:.0%}",
            f"{row['trades_completed']}/{row['trades_proposed']}",
        )
    console.print(table)
    console.print("[bold]Best trade partners[/] (" + r["partner_order"] + ")")
    for row in r["best_trade_partners"]:
        console.print(
            f"  {row['team']} · fit {row['complementary_fit']} · {row['tier']} · strong "
            f"{', '.join(row['their_strong_cats']) or '-'} · weak {', '.join(row['their_weak_cats']) or '-'}"
        )
    if r["note"]:
        console.print(f"[dim]{r['note']}[/]")
