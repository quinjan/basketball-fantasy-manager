"""HTML -> rows for each Hashtag Basketball page FBM reads.

Every parser checks the page structure it relies on (table headers, form fields) and raises `HashtagLayoutChanged`
on any mismatch: it never returns partial data. Text from HT (player notes, team names) is untrusted data.

Conventions: category keys are FBM's (fg_pct ft_pct tpm pts reb ast stl blk tov); percentages are fractions 0..1;
per-category z values are oriented so that higher is better (HT's turnover z is stored with its sign flipped).
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from fbm.hashtag import CATEGORY_KEYS, VIEWS
from fbm.hashtag.dom import Node, parse_html
from fbm.hashtag.errors import HashtagLayoutChanged

HEADER_CAT = {
    "FG%": "fg_pct",
    "FT%": "ft_pct",
    "3PM": "tpm",
    "PTS": "pts",
    "REB": "reb",
    "TREB": "reb",
    "AST": "ast",
    "STL": "stl",
    "BLK": "blk",
    "TO": "tov",
}
CAT_HEADERS = ("FG%", "FT%", "3PM", "PTS", "REB", "AST", "STL", "BLK", "TO")
# HT multiplier dropdowns / include checkboxes per FBM category.
MULT_FIELD = {
    "fg_pct": "FGP",
    "ft_pct": "FTP",
    "tpm": "3PM",
    "pts": "PTS",
    "reb": "REB",
    "ast": "AST",
    "stl": "STL",
    "blk": "BLK",
    "tov": "TO",
}
MULT_STEPS = (3.0, 2.5, 2.0, 1.5, 1.25, 1.0, 0.75, 0.5, 0.25)
# hidden z-value inputs in the projections/rankings grids (HFTUR: positive = more turnovers = worse)
Z_FIELD = {
    "fg_pct": "HFFGP",
    "ft_pct": "HFFTP",
    "tpm": "HFTGM",
    "pts": "HFPTS",
    "reb": "HFREB",
    "ast": "HFAST",
    "stl": "HFSTL",
    "blk": "HFBLK",
    "tov": "HFTUR",
}
_NUM = re.compile(r"-?\d*\.?\d+")
_PCT = re.compile(r"(-?\d*\.\d+|\d+)\s*\(\s*(\d*\.?\d+)\s*/\s*(\d*\.?\d+)\s*\)")
_PLAYER_HREF = re.compile(r"^/(\d+)/player")


# ------------------------------------------------------------------ small helpers


def _doc(html: str | Node) -> Node:
    return html if isinstance(html, Node) else parse_html(html)


def num(text: str | None) -> float | None:
    if text is None:
        return None
    m = _NUM.search(text.replace(",", ""))
    return float(m.group()) if m else None


def pct_parts(text: str) -> tuple[float | None, float | None, float | None]:
    """'0.573 (10.5/18.3)' -> (0.573, 10.5, 18.3)."""
    m = _PCT.search(text)
    if m:
        return float(m.group(1)), float(m.group(2)), float(m.group(3))
    return num(text), None, None


def headers(row: Node) -> list[str]:
    return [c.text().upper() for c in row.children_tags() if c.tag in ("th", "td")]


def view_from_text(text: str | None) -> str | None:
    """HT duration dropdown text -> FBM view (ROS, BLEND7, ..., STREAM30); None for season-stat durations."""
    if not text:
        return None
    m = re.search(r"\((BLEND|STREAM)(7|14|30)\)", text.upper())
    if m:
        return m.group(1) + m.group(2)
    if re.search(r"rest[- ]of[- ]season", text, re.I):
        return "ROS"
    return None


def duration_from_text(text: str | None) -> str | None:
    """Season-stats duration: 'season' | 'last7' | 'last14' | 'last30' (None for projections/past seasons)."""
    if not text:
        return None
    m = re.search(r"last (7|14|30) days", text, re.I)
    if m:
        return f"last{m.group(1)}"
    if re.search(r"regular season", text, re.I) and re.search(r"updated daily|rankings", text, re.I):
        return "season"
    return None


def _select(doc: Node, short: str) -> Node | None:
    return doc.find("select", pred=lambda n: (n.get("name") or "").endswith("$" + short))


def selected(doc: Node, short: str) -> tuple[str, str] | None:
    """(value, text) of the selected option of a select, by short name."""
    sel = _select(doc, short)
    if sel is None:
        return None
    opts = sel.find_all("option")
    chosen = [o for o in opts if "selected" in o.attrs] or opts[:1]
    return (chosen[0].get("value") or "", chosen[0].text()) if chosen else None


def options(doc: Node, short: str) -> list[tuple[str, str]]:
    sel = _select(doc, short)
    return [] if sel is None else [(o.get("value") or "", o.text()) for o in sel.find_all("option")]


def _checked(doc: Node, short: str) -> bool | None:
    box = doc.find("input", pred=lambda n: (n.get("name") or "").endswith("$" + short))
    return None if box is None else "checked" in box.attrs


def multipliers(doc: Node) -> dict[str, float]:
    """Category multipliers in force (unticked categories are absent)."""
    out = {}
    for cat, f in MULT_FIELD.items():
        sel = selected(doc, "DD" + f)
        if sel is None:
            continue
        if _checked(doc, "CB" + f) is False:
            continue
        out[cat] = float(num(sel[1]) or 0.0)
    return out


def _find_table(doc: Node, page: str, required: tuple[str, ...], table_id: str | None = None) -> tuple[Node, list]:
    tables = [doc.find("table", id=table_id)] if table_id else doc.find_all("table")
    for t in tables:
        if t is None:
            continue
        rows = t.find_all("tr")
        if rows and all(h in headers(rows[0]) for h in required):
            return t, rows
    raise HashtagLayoutChanged(page, f"no table with columns {', '.join(required)}")


def _player_link(cell: Node) -> tuple[str | None, str]:
    for a in cell.find_all("a"):
        m = _PLAYER_HREF.match(a.get("href") or "")
        if m:
            return m.group(1), a.text()
    return None, cell.text()


def _notes(cell: Node) -> str | None:
    parts = []
    for s in cell.find_all("span"):
        title = (s.get("title") or "").strip()
        if title:
            tone = "+" if "text-success" in s.classes() else "-" if "text-danger" in s.classes() else "~"
            parts.append(f"[{tone}] {title}")
    return " | ".join(parts) or None


# ------------------------------------------------------------------ player stat grids


PLAYER_GRID_REQUIRED = (
    "R#",
    "PLAYER",
    "POS",
    "TEAM",
    "GP",
    "MPG",
    *CAT_HEADERS[:4],
    "AST",
    "STL",
    "BLK",
    "TO",
    "TOTAL",
)


def _grid_rows(doc: Node, page: str, table_id: str | None = None, with_z: bool = False) -> list[dict[str, Any]]:
    """Generic HT player grid (projections, rankings, waiver wire): one dict per player row."""
    required = tuple(h for h in PLAYER_GRID_REQUIRED)
    table, rows = _find_table(doc, page, required, table_id)
    head = headers(rows[0])
    if not ("REB" in head or "TREB" in head):
        raise HashtagLayoutChanged(page, "no REB/TREB column")
    idx = {h: i for i, h in enumerate(head)}
    out = []
    for r in rows[1:]:
        cells = r.children_tags("td")
        if not cells or [c.text().upper() for c in cells] == head:
            continue  # repeated header rows
        if len(cells) != len(head):
            raise HashtagLayoutChanged(page, f"row has {len(cells)} cells, header has {len(head)}")
        ht_id, name = _player_link(cells[idx["PLAYER"]])
        if ht_id is None:
            raise HashtagLayoutChanged(page, "player cell without a /<id>/player link")
        row: dict[str, Any] = {
            "rank": int(num(cells[idx["R#"]].text()) or 0) or None,
            "ht_id": ht_id,
            "name": name,
            "positions": cells[idx["POS"]].text(),
            "team": cells[idx["TEAM"]].text(),
            "gp": num(cells[idx["GP"]].text()),
            "minutes": num(cells[idx["MPG"]].text()),
            "adp": num(cells[idx["ADP"]].text()) if "ADP" in idx else None,
            "total": num(cells[idx["TOTAL"]].text()),
            "note": _notes(cells[idx["PLAYER"]]),
        }
        fg = pct_parts(cells[idx["FG%"]].text())
        ft = pct_parts(cells[idx["FT%"]].text())
        row.update(fg_pct=fg[0], fgm=fg[1], fga=fg[2], ft_pct=ft[0], ftm=ft[1], fta=ft[2])
        for h, cat in (("3PM", "tpm"), ("PTS", "pts"), ("AST", "ast"), ("STL", "stl"), ("BLK", "blk"), ("TO", "tov")):
            row[cat] = num(cells[idx[h]].text())
        row["reb"] = num(cells[idx["TREB" if "TREB" in idx else "REB"]].text())
        if with_z:
            for cat, field in Z_FIELD.items():
                inp = r.find("input", pred=lambda n, f=field: re.search(rf"_{f}_\d+$", n.get("id") or "") is not None)
                if inp is None:
                    raise HashtagLayoutChanged(page, f"missing hidden z field {field}")
                z = num(inp.get("value"))
                row[f"z_{cat}"] = (-z if z is not None else None) if cat == "tov" else z
        out.append(row)
    return out


def parse_projections(html: str | Node) -> dict[str, Any]:
    """Premium projections page -> {view, settings: {show, rank, type, posfrom}, rows: [...]}.

    rows: rank, ht_id, name, positions, team, adp, gp, minutes, fgm/fga/fg_pct, ftm/fta/ft_pct, tpm, pts, reb, ast,
    stl, blk, tov, z_<cat> (higher = better), total, note."""
    doc = _doc(html)
    page = "projections"
    dur = selected(doc, "DDDURATION")
    if dur is None:
        raise HashtagLayoutChanged(page, "no DDDURATION dropdown")
    view = view_from_text(dur[1])
    if view not in VIEWS:
        raise HashtagLayoutChanged(page, f"unknown projection view '{dur[1]}'")
    settings = {k: (selected(doc, f) or ("", ""))[1] for k, f in
                (("show", "DDSHOW"), ("rank", "DDRANK"), ("type", "DDTYPE"), ("posfrom", "DDPOSFROM"))}  # fmt: skip
    rows = _grid_rows(doc, page, "ContentPlaceHolder1_GridView1", with_z=True)
    if not rows:
        raise HashtagLayoutChanged(page, "no player rows")
    return {"view": view, "settings": settings, "rows": rows}


def parse_season_rankings(html: str | Node) -> dict[str, Any]:
    """Season-to-date rankings (/fantasy-basketball-rankings) -> {duration, rows} (same row keys as projections)."""
    doc = _doc(html)
    page = "season_stats"
    dur = selected(doc, "DDDURATION")
    duration = duration_from_text(dur[1] if dur else None)
    if duration is None:
        raise HashtagLayoutChanged(page, f"unknown season duration '{dur[1] if dur else None}'")
    rows = _grid_rows(doc, page, "ContentPlaceHolder1_GridView1", with_z=True)
    if not rows:
        raise HashtagLayoutChanged(page, "no player rows")
    return {"duration": duration, "rows": rows}


def parse_waiver(html: str | Node, page: str = "waiver") -> dict[str, Any]:
    """League Waiver Wire (or League Rankings, same layout) -> {view, weights, show, rows}."""
    doc = _doc(html)
    dur = selected(doc, "DDDURATION")
    view = view_from_text(dur[1] if dur else None)
    if view is None:
        raise HashtagLayoutChanged(page, f"unknown view '{dur[1] if dur else None}'")
    weights = multipliers(doc)
    if not weights:
        raise HashtagLayoutChanged(page, "no category multiplier dropdowns")
    rows = _grid_rows(doc, page)
    return {"view": view, "weights": weights, "show": (selected(doc, "DDSHOW") or ("", ""))[1], "rows": rows}


# ------------------------------------------------------------------ H2H Matchup Planner

_PLANNER_IDS = {
    "fg_pct": "fgp2",
    "ft_pct": "ftp2",
    "tpm": "tgm2",
    "pts": "pts2",
    "reb": "trb2",
    "ast": "ast2",
    "stl": "stl2",
    "blk": "blk2",
    "tov": "tur2",
}


def ht_share(cat: str, me: float | None, opp: float | None) -> float:
    """HT's displayed matchup 'probability' (a share formula, reference only).

    counting: mine / (mine + opp); pct: 0.5 + (pct-point diff) / 40; TO: lower is better; clipped 0.05..0.95."""
    me, opp = float(me or 0.0), float(opp or 0.0)
    if cat == "tov":
        me, opp = opp, me
    if cat in ("fg_pct", "ft_pct"):
        if me == 0 and opp == 0:
            return 0.5
        p = 0.5 + ((me - opp) * 100.0) / 40.0
    else:
        if me + opp == 0:
            return 0.5
        p = me / (me + opp)
    return min(0.95, max(0.05, p))


def parse_h2h_planner(html: str | Node) -> dict[str, Any]:
    """H2H Matchup Planner -> {my_team, opp_team, week, view, spots, categories: {cat: {me_before, opp_before,
    me_after, opp_after, ht_share}}, roster_side: me|opp, roster: [{yahoo_id, name, positions, team, days}]}.

    before = banked this week; after = banked + HT's projection for the rest of the week."""
    doc = _doc(html)
    page = "h2h_planner"
    t, rows = _find_table(doc, page, ("BEFORE", *CAT_HEADERS), "statsTable")
    head = [h for h in headers(rows[0])]
    if head[1:] != list(CAT_HEADERS):
        raise HashtagLayoutChanged(page, f"unexpected planner columns {head}")
    values: dict[str, dict[str, float | None]] = {}
    for prefix in ("BHOME", "BAWAY", "AHOME", "AAWAY"):
        values[prefix] = {}
        for cat, suffix in _PLANNER_IDS.items():
            cell = doc.find("td", id=f"ContentPlaceHolder1_{prefix}_{suffix}")
            if cell is None:
                raise HashtagLayoutChanged(page, f"missing cell {prefix}_{suffix}")
            values[prefix][cat] = num(cell.text())
    if "AFTER" not in [headers(r)[0] for r in rows if r.find("th")]:
        raise HashtagLayoutChanged(page, "no AFTER section")
    cats = {}
    for cat in CATEGORY_KEYS:
        me_a, opp_a = values["AHOME"][cat], values["AAWAY"][cat]
        cats[cat] = {
            "me_before": values["BHOME"][cat],
            "opp_before": values["BAWAY"][cat],
            "me_after": me_a,
            "opp_after": opp_a,
            "ht_share": ht_share(cat, me_a, opp_a),
        }
    team1, team2 = selected(doc, "DDTEAM1"), selected(doc, "DDTEAM2")
    week, dur, spots = selected(doc, "DDWEEK"), selected(doc, "DDDURATION"), selected(doc, "DDSPOTS")
    if not (team1 and team2 and week and dur):
        raise HashtagLayoutChanged(page, "missing team/week/duration dropdowns")
    radio = [i for i in doc.find_all("input", type="radio") if (i.get("name") or "").endswith("$RadioButtonList1")]
    side = "opp" if any(i.get("value") == "2" and "checked" in i.attrs for i in radio) else "me"
    return {
        "my_team": team1[1],
        "opp_team": team2[1] if team2[0] != "999" else None,
        "week": int(num(week[1]) or 0),
        "view": view_from_text(dur[1]),
        "duration_text": dur[1],
        "spots": int(num(spots[1]) or 0) if spots else None,
        "categories": cats,
        "roster_side": side,
        "roster": parse_planner_roster(doc),
    }


def parse_planner_roster(doc: Node) -> list[dict[str, Any]]:
    """Roster rows of the planner (Repeater2): Yahoo player id (hidden HFpid; not an HT id), name, positions/team,
    days with a game."""
    out = []
    for inp in doc.find_all("input", pred=lambda n: re.search(r"_Repeater2_HFpid_\d+$", n.get("id") or "") is not None):
        cell = inp.parent
        row = cell.parent if cell is not None else None
        span = cell.find("span") if cell is not None else None
        label = span.text() if span is not None else (cell.text() if cell is not None else "")
        m = re.match(r"(.+?)\s*\(([^)]*)-([A-Z]{2,4})\)", label)
        name, positions, team = (m.group(1), m.group(2), m.group(3)) if m else (label, "", "")
        days = []
        if row is not None:
            for cb in row.find_all("input", type="checkbox"):
                d = re.search(r"\$CB([A-Z]{3})$", cb.get("name") or "")
                if d:
                    days.append(d.group(1))
        out.append({"yahoo_id": inp.get("value"), "name": name.strip(), "positions": positions, "team": team,
                    "days": days})  # fmt: skip
    return out


# ------------------------------------------------------------------ team ranks (scouting report)


def parse_team_ranks(html: str | Node) -> dict[str, Any]:
    """Scouting Report -> {view, rows: [{team_name, rank, total, categories: {cat: {value, rank}}}]}."""
    doc = _doc(html)
    page = "team_ranks"
    dur = selected(doc, "DDDURATION")
    view = view_from_text(dur[1] if dur else None)
    if view is None:
        raise HashtagLayoutChanged(page, f"unknown view '{dur[1] if dur else None}'")
    _, rows = _find_table(doc, page, ("R#", "TEAM", "TOTAL", *CAT_HEADERS))
    head = headers(rows[0])
    out = []
    for r in rows[1:]:
        cells = r.children_tags("td")
        if not cells:
            continue
        if len(cells) != len(head):
            raise HashtagLayoutChanged(page, "row/header cell count mismatch")
        cats = {}
        for h in CAT_HEADERS:
            text = cells[head.index(h)].text()
            m = re.match(r"\s*(-?\d*\.?\d+)\s*#(\d+)", text)
            if not m:
                raise HashtagLayoutChanged(page, f"unexpected cell '{text}' in {h}")
            cats[HEADER_CAT[h]] = {"value": float(m.group(1)), "rank": int(m.group(2))}
        out.append({
            "rank": int(num(cells[head.index("R#")].text()) or 0),
            "team_name": cells[head.index("TEAM")].text(),
            "total": num(cells[head.index("TOTAL")].text()),
            "categories": cats,
        })  # fmt: skip
    if not out:
        raise HashtagLayoutChanged(page, "no team rows")
    return {"view": view, "rows": out}


# ------------------------------------------------------------------ trade finder


def _trade_players(table: Node, page: str) -> tuple[list[dict], list[dict]]:
    send: list[dict] = []
    get: list[dict] = []
    current: list[dict] | None = None
    for r in table.find_all("tr"):
        hs = headers(r) if r.find("th") else []
        if hs:
            current = send if "YOU SEND" in hs else get if "YOU RECEIVE" in hs else None
            if current is None:
                raise HashtagLayoutChanged(page, f"unexpected trade table header {hs[:3]}")
            continue
        cells = r.children_tags("td")
        if current is None or len(cells) < 16:
            continue
        ht_id, name = _player_link(cells[1])
        if ht_id is None:
            continue  # the AVERAGE row
        current.append({
            "ht_id": ht_id, "name": name, "positions": cells[2].text(), "team": cells[3].text(),
            "rank": int(num(cells[0].text()) or 0) or None, "total": num(cells[15].text()),
        })  # fmt: skip
    return send, get


def parse_trade_finder(html: str | Node) -> dict[str, Any]:
    """Trade Finder (after FIND TRADES) -> {settings, ideas: [{partner_name, badges, give, get, my_delta,
    their_delta, ranks_now, ranks_after, alternatives}]}. my_delta = HT value received - HT value sent."""
    doc = _doc(html)
    page = "trade_finder"
    if doc.find("input", pred=lambda n: (n.get("name") or "").endswith("$BtnFind")) is None:
        raise HashtagLayoutChanged(page, "no FIND TRADES button")
    settings = {
        "fairness": (selected(doc, "DDFAIR") or ("", ""))[0],
        "partner": (selected(doc, "DDPARTNER") or ("", ""))[1],
        "size": (selected(doc, "DDSIZE") or ("", ""))[0],
        "view": view_from_text((selected(doc, "DDDURATION") or ("", ""))[1]),
        "weights": multipliers(doc),
    }
    ideas = []
    for card in doc.find_all("div", pred=lambda n: "psg-data-card" in n.classes()):
        title = card.find("h5", pred=lambda n: "card-title" in n.classes())
        if title is None:
            continue
        m = re.match(r"\s*\d+\.\s*(.+)", title.text())
        if not m:
            continue  # "Your Team" summary card
        impact = card.find("table", pred=lambda n: "tf-impact" in n.classes())
        players = card.find("table", pred=lambda n: "sticky-rd" in n.classes())
        if impact is None or players is None:
            raise HashtagLayoutChanged(page, "trade card without impact/players tables")
        irows = impact.find_all("tr")
        ihead = headers(irows[0])
        if ihead[1:] != list(CAT_HEADERS):
            raise HashtagLayoutChanged(page, f"unexpected impact columns {ihead}")
        ranks = {}
        for r in irows[1:]:
            cells = [c.text() for c in r.children_tags()]
            key = "now" if cells[0].lower().startswith("now") else "after"
            ranks[key] = {HEADER_CAT[h]: int(num(v) or 0) for h, v in zip(CAT_HEADERS, cells[1:], strict=False)}
        give, get = _trade_players(players, page)
        if not give or not get:
            raise HashtagLayoutChanged(page, "trade card without players")
        my_delta = round(sum(p["total"] or 0 for p in get) - sum(p["total"] or 0 for p in give), 3)
        badges = [
            s.text() for s in card.find_all("span", pred=lambda n: "badge" in n.classes() and "rounded-pill" in
                                                                   n.classes()) if not s.text().startswith("#")
        ]  # fmt: skip
        alts = [b for b in badges if "+" in b]
        ideas.append({
            "partner_name": m.group(1).strip(),
            "badges": [b for b in badges if b not in alts],
            "give": give, "get": get,
            "my_delta": my_delta, "their_delta": -my_delta,
            "ranks_now": ranks.get("now", {}), "ranks_after": ranks.get("after", {}),
            "alternatives": alts,
        })  # fmt: skip
    return {"settings": settings, "ideas": ideas}


# ------------------------------------------------------------------ notes


def _date(text: str) -> str | None:
    for fmt in ("%d %B %Y", "%B %d, %Y", "%d %b %Y"):
        try:
            return datetime.strptime(text.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return None


_SOURCES = {"erik's notes": "eriks_notes", "notebook drop": "notebook_drop"}


def parse_notebook(html: str | Node) -> list[dict[str, Any]]:
    """Player Notes (/import-v4/notebook) -> [{note_date, player, team, source, text}] (untrusted text)."""
    doc = _doc(html)
    page = "notes"
    cards = doc.find_all("div", pred=lambda n: "notebook-date-card" in n.classes())
    if not cards:
        raise HashtagLayoutChanged(page, "no notebook-date-card sections")
    out = []
    for card in cards:
        head = card.find(pred=lambda n: "notebook-date-text" in n.classes())
        day = _date(head.text()) if head is not None else None
        if day is None:
            raise HashtagLayoutChanged(page, "unreadable note date")
        for item in card.find_all("div", pred=lambda n: "notebook-update-item" in n.classes()):
            name_p = item.find("p", pred=lambda n: "notebook-player-name" in n.classes())
            comment = item.find("p", pred=lambda n: "notebook-comment" in n.classes())
            if name_p is None or comment is None:
                raise HashtagLayoutChanged(page, "note without player/comment")
            team = name_p.find("span", pred=lambda n: "notebook-player-tsum" in n.classes())
            badge = name_p.find("span", pred=lambda n: "badge" in n.classes())
            src_text = badge.text() if badge is not None else ""
            out.append({
                "note_date": day,
                "player": name_p.own_text(),
                "team": team.text() if team is not None else "",
                "source": _SOURCES.get(src_text.lower(), re.sub(r"\W+", "_", src_text.lower()).strip("_") or "ht"),
                "text": comment.text(),
            })  # fmt: skip
    return out


# ------------------------------------------------------------------ Next Man Up


def parse_next_man_up(html: str | Node) -> dict[str, Any]:
    """Next Man Up -> {season, team, absent_ht_id, absent_name, title, teammates: [{ht_id, name, gp_without,
    min_with, min_without, stats: {stat: {with, without}}}]}. Empty teammates when no player is selected yet."""
    doc = _doc(html)
    page = "next_man_up"
    season, team, player = selected(doc, "DDSEASON"), selected(doc, "DDTEAM"), selected(doc, "DDPLAYER")
    if not (season and team):
        raise HashtagLayoutChanged(page, "missing season/team dropdowns")
    name_to_id = {" ".join(t.split()).casefold(): v for v, t in options(doc, "DDPLAYER")}
    out: dict[str, Any] = {
        "season": season[0], "team": team[0],
        "absent_ht_id": player[0] if player else None, "absent_name": player[1] if player else None,
        "title": "", "teammates": [],
    }  # fmt: skip
    table = doc.find("table", id="ContentPlaceHolder1_GvRows")
    if table is None:
        return out
    title = doc.find("span", id="ContentPlaceHolder1_LblTitle")
    out["title"] = title.text() if title is not None else ""
    rows = table.find_all("tr")
    head = headers(rows[0])
    for h in ("PLAYER", "GP", "MIN", "PTS", "REB", "AST", "STL", "BLK", "FG%", "FT%", "TO"):
        if h not in head:
            raise HashtagLayoutChanged(page, f"missing column {h}")
    for r in rows[1:]:
        cells = r.children_tags("td")
        if len(cells) != len(head):
            continue
        name_span = cells[head.index("PLAYER")].find("span", pred=lambda n: "nm-name" in n.classes())
        name = name_span.text() if name_span is not None else cells[head.index("PLAYER")].text()
        stats = {}
        for h in head[head.index("GP") + 1 :]:
            c = cells[head.index(h)]
            big = c.find("div", pred=lambda n: "nm-big" in n.classes())
            pill = c.find("span", pred=lambda n: "nm-pill" in n.classes())
            stats[h.lower()] = {
                "without": big.text() if big is not None else c.text(),
                "with": pill.text() if pill is not None else None,
            }
        out["teammates"].append({
            "ht_id": name_to_id.get(" ".join(name.split()).casefold()),
            "name": name,
            "gp_without": int(num(cells[head.index("GP")].text()) or 0),
            "min_without": num(stats["min"]["without"]),
            "min_with": num(stats["min"]["with"]),
            "stats": stats,
        })  # fmt: skip
    return out


# ------------------------------------------------------------------ premium home


def parse_premium_home(html: str | Node) -> list[dict[str, Any]]:
    """Premium home -> [{lid, name, refresh_path}] for the owner's Yahoo leagues on HT."""
    doc = _doc(html)
    out = []
    for card in doc.find_all("div", pred=lambda n: "league-card" in n.classes()):
        name = card.find("div", pred=lambda n: "league-card-name" in n.classes())
        refresh = card.find("a", pred=lambda n: "refresh-yahoo.aspx" in (n.get("href") or ""))
        if name is None or refresh is None:
            continue
        lid = re.search(r"lid=(\d+)", refresh.get("href") or "")
        out.append({"lid": lid.group(1) if lid else None, "name": name.text(), "refresh_path": refresh.get("href")})
    return out
