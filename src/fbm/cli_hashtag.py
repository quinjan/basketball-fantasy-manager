"""`fbm hashtag`: Hashtag Basketball Premium access (sign in, import, check). Registered by importing from fbm.cli."""

from __future__ import annotations

import typer
from rich.markup import escape
from rich.table import Table

from fbm.cli import _clipboard, _fail, _printable, _svc, app, console

hashtag_app = typer.Typer(
    no_args_is_help=True,
    help="Hashtag Basketball Premium: sign in with your own browser session, import projections and matchup totals.",
)
app.add_typer(hashtag_app, name="hashtag")

_PASTE_STEPS = (
    "In the browser where you're signed in to Hashtag Basketball Premium, open:\n   {url}",
    "Press F12 → Network tab → reload the page → click the first request → Headers → Request Headers.",
    "Copy the whole value of [bold]cookie[/], then press [bold]Enter[/] here: FBM reads it from your clipboard "
    "(and clears it). You can also paste it into the hidden prompt.",
)


def _paste_session():
    from fbm.hashtag import session as hs

    for i, step in enumerate(_PASTE_STEPS, 1):
        console.print(f"[bold]{i}.[/] {step.format(url=hs.PREMIUM_HOME)}")
    typed = _printable(typer.prompt("Cookie header", hide_input=True, default="", show_default=False))
    value = typed or _printable(_clipboard("read"))
    if not typed and value:
        _clipboard("clear")
    if not value:
        _fail("No cookie header received.")
    return hs.from_cookie_header(value)


@hashtag_app.command("login")
def hashtag_login(
    paste: bool = typer.Option(
        False, "--paste", help="Paste the Cookie header from your browser's DevTools instead of opening a browser."
    ),
    browser: str = typer.Option(None, "--browser", help="msedge | chrome | chromium (default: first that starts)."),
    timeout: int = typer.Option(600, "--timeout", help="Seconds to wait for you to finish signing in."),
) -> None:
    """Sign in to Hashtag Basketball Premium once (your own browser sign-in; FBM never sees the password)."""
    from fbm.hashtag import parsers as P
    from fbm.hashtag import session as hs
    from fbm.hashtag.client import HashtagClient
    from fbm.hashtag.errors import HashtagError

    console.print(
        "FBM reads Hashtag Basketball Premium pages with your own sign-in, for your personal use only. Read-only: "
        "FBM never saves anything on Hashtag. The session is kept in your OS credential store, never in a file or "
        "the AI chat.\n"
    )
    try:
        new = _paste_session() if paste else hs.browser_login(timeout, browser, echo=console.print)
        with HashtagClient(new, save_rotated=False) as client:
            leagues = P.parse_premium_home(client.get("/premium/").doc)
    except HashtagError as err:
        _fail(f"{err} Nothing saved.")
    hs.save(new)
    console.print(
        f"[green]Signed in.[/] Hashtag session saved to your OS credential store ({len(new.cookies)} cookies)."
    )
    if leagues:
        console.print("Leagues on Hashtag: " + ", ".join(f"{lg['name']} (id {lg['lid']})" for lg in leagues))
        console.print(
            "Set `\\[hashtag] league_id` in config/settings.toml to your league's id, then `fbm hashtag check`."
        )
    else:
        console.print("[yellow]No Yahoo league found on Hashtag yet: import it on Hashtag (Premium > Import).[/]")


@hashtag_app.command("logout")
def hashtag_logout() -> None:
    """Forget the Hashtag sign-in (HT then reports unhealthy: no suggestions while HT is enabled)."""
    from fbm.hashtag import session as hs

    hs.clear()
    console.print("Hashtag session removed from your OS credential store.")


def _health_table(h: dict) -> None:
    mark = "[green]healthy[/]" if h["healthy"] else ("[red]UNHEALTHY[/]" if h["enabled"] else "[dim]not enabled[/]")
    console.print(f"Hashtag Basketball: {mark} (checked {h['checked_at']})")
    for r in h["reasons"]:
        console.print(f"  • {escape(r)}")
    for f in h["fix"]:
        console.print(f"  [bold]fix:[/] {escape(f)}")


@hashtag_app.command("status")
def hashtag_status() -> None:
    """HT health (the suggestion gate), the latest import of each page, unmatched players. Never prints cookies."""
    from fbm.gm import hashtag_services as hsvc

    r = _svc(hsvc.hashtag_status)
    _health_table(r["health"])
    s = r["session"]
    console.print(
        f"Sign-in: saved {s['age_days']} days ago via {s['source']} ({s['cookies']} cookies)"
        if s["stored"]
        else "Sign-in: [yellow]none stored[/] (`fbm hashtag login`)"
    )
    table = Table(title="Latest Hashtag imports")
    for col in ("Page", "View", "Status", "Rows", "Finished (UTC)", "Detail"):
        table.add_column(col)
    for row in r["last_imports"]:
        color = "green" if row["status"] == "ok" else "red"
        table.add_row(row["page"], row["view"] or "-", f"[{color}]{row['status']}[/]", str(row["rows"] or 0),
                      row["finished_at"] or "-", escape((row["detail"] or "")[:80]))  # fmt: skip
    console.print(table)
    if r["unmatched_projected"]:
        console.print(f"[yellow]{len(r['unmatched_projected'])} projected players without an ESPN id[/]")
        for p in r["unmatched_projected"][:15]:
            console.print(f"  {p['ht_id']}: {p['name']} ({p['team']}) rank {p['rank']}")
        console.print(r["how_to_fix_unmatched"])


@hashtag_app.command("import")
def hashtag_import(
    pages: str = typer.Option(
        None, "--pages", "-p", help="Only these pages (comma list), e.g. projections,h2h_planner,league_sync."
    ),
) -> None:
    """Import Hashtag Basketball now (projections, H2H planner, league sync, waiver wire, ranks, notes, ...)."""
    from fbm.gm import hashtag_services as hsvc

    wanted = [x.strip() for x in (pages or "").split(",") if x.strip()] or None
    r = _svc(hsvc.hashtag_import, wanted)
    table = Table(title=f"Hashtag import {r['run_id']}")
    for col in ("Page", "Status", "Rows", "Detail"):
        table.add_column(col)
    for label, v in r["pages"].items():
        color = "green" if v["status"] == "ok" else "red"
        table.add_row(label, f"[{color}]{v['status']}[/]", str(v["rows"]), escape((v["detail"] or "")[:90]))
    console.print(table)
    if r["healthy"]:
        console.print("[green]HT healthy.[/]")
    else:
        console.print("[red]HT not healthy:[/] " + escape("; ".join(r.get("health_reasons") or [])))
        raise typer.Exit(code=1)


@hashtag_app.command("check")
def hashtag_check() -> None:
    """Signed in? League visible on Hashtag? Does every page FBM reads still parse? Unmatched ids. Read-only."""
    from fbm.gm import hashtag_services as hsvc

    r = _svc(hsvc.hashtag_check)
    console.print(
        f"Signed in: {'[green]yes[/]' if r['signed_in'] else '[red]no[/]'} · HT league id: "
        f"{r['league_id'] or '[yellow]not set[/]'} · league visible: {r['league_visible']}"
    )
    if r.get("error"):
        console.print(f"[red]{r['error']}[/]")
    table = Table(title="Hashtag pages")
    for col in ("Page", "", "Detail"):
        table.add_column(col)
    for p in r["pages"]:
        table.add_row(p["page"], "[green]OK[/]" if p["ok"] else "[red]FAIL[/]", escape(p["detail"]))
    console.print(table)
    if r.get("unmatched_players"):
        console.print(f"[yellow]{len(r['unmatched_players'])} HT players without an ESPN id:[/]")
        for p in r["unmatched_players"][:25]:
            console.print(f"  {p['ht_id']}: {p['name']} ({p['team']})")
    if r.get("health"):
        _health_table(r["health"])
    if not r["signed_in"] or not all(p["ok"] for p in r["pages"]):
        raise typer.Exit(code=1)
