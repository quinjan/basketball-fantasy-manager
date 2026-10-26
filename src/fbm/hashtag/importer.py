"""The daily Hashtag Basketball import (contract A of the 2026-10-26 overhaul).

`run_import` pulls every page FBM needs, in order, and writes the hashtag_* tables. Every page[/view] gets one
`hashtag_import_log` row (status ok | layout_changed | session_expired | league_mismatch | error). A page that fails
writes nothing (no partial data); a `session_expired` stops the run. Read-only: GETs and view postbacks only.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime
from typing import Any

from fbm.hashtag import CATEGORY_KEYS, VIEWS
from fbm.hashtag import ids as ht_ids
from fbm.hashtag import parsers as P
from fbm.hashtag.client import HashtagClient, Page, option_value, select_options
from fbm.hashtag.errors import HashtagError, HashtagLayoutChanged, HashtagSessionError
from fbm.hashtag.store import weights_key

DEFAULT_PAGES = ("projections", "h2h_planner", "league_sync", "waiver", "team_ranks", "notes", "season_stats")
OPTIONAL_PAGES = ("trade_finder", "next_man_up")
ALL_PAGES = DEFAULT_PAGES + OPTIONAL_PAGES
SEASON_DURATIONS = ("season", "last7", "last14", "last30")

PATH_PROJECTIONS = "/fantasy-basketball-projections"
PATH_RANKINGS = "/fantasy-basketball-rankings"
PATH_NOTEBOOK = "/import-v4/notebook"
PATH_NEXT_MAN_UP = "/next-man-up"
PATH_PREMIUM = "/premium/"


def league_path(tool: str, lid: str) -> str:
    return f"/import-v4/{tool}.aspx?lid={lid}"


SEASON_STATS_SCHEMA = """
CREATE TABLE IF NOT EXISTS hashtag_season_stats (
    as_of TEXT NOT NULL,
    duration TEXT NOT NULL,              -- season | last7 | last14 | last30 (HT season-to-date rankings)
    ht_id TEXT NOT NULL,
    espn_id TEXT,
    name TEXT, team TEXT,
    gp REAL, minutes REAL,
    fgm REAL, fga REAL, ftm REAL, fta REAL, tpm REAL, pts REAL, reb REAL, ast REAL, stl REAL, blk REAL, tov REAL,
    total REAL,
    rank INTEGER,
    PRIMARY KEY (as_of, duration, ht_id)
);
CREATE INDEX IF NOT EXISTS ix_ht_season_espn ON hashtag_season_stats(espn_id, as_of);
"""


def ensure_tables(conn: sqlite3.Connection) -> None:
    """Tables owned by the importer that are not in the core schema (hashtag_season_stats)."""
    conn.executescript(SEASON_STATS_SCHEMA)


# ------------------------------------------------------------------ helpers


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="seconds")


def _ht_league_id(league: Any | None) -> str:
    from fbm.settings import load_settings

    lid = (load_settings().hashtag_league_id or "").strip()
    if not lid and league is not None and ".l." in (getattr(league, "league_key", "") or ""):
        lid = league.league_key.split(".l.")[-1]
    return lid


def _policy_view(conn: sqlite3.Connection, key: str, default: str) -> str:
    try:
        from fbm.gm import policy

        v = str(policy.get(conn, key)).upper()
        return v if v in VIEWS else default
    except Exception:  # noqa: BLE001 - policy registry missing in old DBs: baseline view
        return default


def _snap(x: float) -> float:
    return min(P.MULT_STEPS, key=lambda s: abs(s - float(x)))


def _norm(s: str | None) -> str:
    return " ".join((s or "").split()).casefold()


def _duration_value(page: Page, view: str) -> str:
    for value, text, _ in select_options(page.doc, "DDDURATION"):
        if P.view_from_text(text) == view:
            return value
    raise HashtagLayoutChanged("projections", f"no {view} option in the duration dropdown")


def _try_option(page: Page, name: str, text: str) -> str | None:
    try:
        return option_value(page.doc, name, text)
    except HashtagError:
        return None


class _Run:
    def __init__(self, conn: sqlite3.Connection, now: datetime | None):
        self.conn = conn
        self.run_id = uuid.uuid4().hex[:12]
        self.fixed_now = now
        self.report: dict[str, dict[str, Any]] = {}
        self.stopped = False

    def now(self) -> datetime:
        return self.fixed_now or datetime.now(UTC)

    def log(self, page: str, view: str | None, status: str, rows: int, detail: str, started: str) -> None:
        self.conn.execute(
            "INSERT INTO hashtag_import_log (run_id, started_at, finished_at, page, view, status, rows, detail) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (self.run_id, started, _iso(self.now()), page, view, status, rows, detail[:500] if detail else None),
        )
        self.conn.commit()
        label = f"{page}:{view}" if view else page
        self.report[label] = {"status": status, "rows": rows, "detail": detail}

    def step(self, page: str, view: str | None, fn: Callable[[], tuple[int, str] | int]) -> Any:
        """Run one page[/view]: fn writes its rows (uncommitted) and returns rows (or (rows, detail)).
        Errors roll back and log; a session error stops the run."""
        if self.stopped:
            return None
        started = _iso(self.now())
        try:
            out = fn()
            rows, detail = out if isinstance(out, tuple) else (out, "")
            self.conn.commit()
            self.log(page, view, "ok", rows, detail, started)
            return rows
        except LeagueMismatch as err:
            self.conn.rollback()
            self.log(page, view, "league_mismatch", 0, str(err), started)
        except HashtagSessionError as err:
            self.conn.rollback()
            self.log(page, view, "session_expired", 0, str(err), started)
            self.stopped = True
        except HashtagLayoutChanged as err:
            self.conn.rollback()
            self.log(page, view, "layout_changed", 0, str(err), started)
        except Exception as err:  # noqa: BLE001 - logged per page; the run continues with the next page
            self.conn.rollback()
            self.log(page, view, "error", 0, f"{type(err).__name__}: {err}", started)
        return None


class LeagueMismatch(HashtagError):
    status = "league_mismatch"


# ------------------------------------------------------------------ projections


def _projection_rows(conn: sqlite3.Connection, parsed: dict, as_of: str, fetched: str) -> int:
    rows = parsed["rows"]
    mapping = ht_ids.map_players(conn, rows, commit=False)
    view = parsed["view"]
    conn.execute("DELETE FROM hashtag_projections WHERE as_of = ? AND view = ?", (as_of, view))
    cols = ("name", "team", "positions", "rank", "adp", "gp", "minutes", "fgm", "fga", "ftm", "fta", "tpm", "pts",
            "reb", "ast", "stl", "blk", "tov", *(f"z_{c}" for c in CATEGORY_KEYS), "total", "note")  # fmt: skip
    conn.executemany(
        f"INSERT OR REPLACE INTO hashtag_projections (as_of, view, ht_id, espn_id, {', '.join(cols)}, fetched_at) "
        f"VALUES ({', '.join('?' * (len(cols) + 5))})",
        [(as_of, view, r["ht_id"], mapping.get(r["ht_id"]), *(r.get(c) for c in cols), fetched) for r in rows],
    )
    return len(rows)


def _pull_projections(run: _Run, client: HashtagClient, views: Iterable[str], as_of: str) -> None:
    state: dict[str, Page] = {}

    def base() -> Page:
        if "page" not in state:
            state["page"] = client.get(PATH_PROJECTIONS)
        return state["page"]

    for view in views:

        def one(view: str = view) -> int:
            page = base()
            fields = {"DDDURATION": _duration_value(page, view)}
            for name, text in (("DDSHOW", "All"), ("DDRANK", "Averages"), ("DDTYPE", "H2H"), ("DDPOSFROM", "Yahoo")):
                value = _try_option(page, name, text)
                if value is None:
                    raise HashtagLayoutChanged("projections", f"no '{text}' option in {name}")
                fields[name] = value
            page = client.postback(page, fields=fields, event_target="DDDURATION")
            state["page"] = page
            parsed = P.parse_projections(page.doc)
            want = {"show": "All", "rank": "Averages", "type": "H2H", "posfrom": "Yahoo"}
            if parsed["view"] != view or any(_norm(parsed["settings"][k]) != _norm(v) for k, v in want.items()):
                raise HashtagError(f"HT returned {parsed['view']} {parsed['settings']} instead of {view} {want}")
            return _projection_rows(run.conn, parsed, as_of, _iso(run.now()))

        run.step("projections", view, one)


# ------------------------------------------------------------------ season stats (market view for ws-signals)


def _pull_season_stats(run: _Run, client: HashtagClient, as_of: str, durations=SEASON_DURATIONS) -> None:
    ensure_tables(run.conn)
    state: dict[str, Page] = {}
    for duration in durations:

        def one(duration: str = duration) -> int:
            page = state.get("page") or client.get(PATH_RANKINGS)
            value = next(
                (v for v, t, _ in select_options(page.doc, "DDDURATION") if P.duration_from_text(t) == duration),
                None,
            )
            if value is None:
                raise HashtagLayoutChanged("season_stats", f"no '{duration}' option")
            fields = {"DDDURATION": value}
            for name, text in (("DDSHOW", "All"), ("DDRANK", "Averages"), ("DDTYPE", "H2H")):
                v = _try_option(page, name, text)
                if v is not None:
                    fields[name] = v
            page = client.postback(page, fields=fields, event_target="DDDURATION")
            state["page"] = page
            parsed = P.parse_season_rankings(page.doc)
            if parsed["duration"] != duration:
                raise HashtagError(f"HT returned {parsed['duration']} instead of {duration}")
            mapping = ht_ids.map_players(run.conn, parsed["rows"], commit=False)
            run.conn.execute("DELETE FROM hashtag_season_stats WHERE as_of = ? AND duration = ?", (as_of, duration))
            cols = ("name", "team", "gp", "minutes", "fgm", "fga", "ftm", "fta", "tpm", "pts", "reb", "ast", "stl",
                    "blk", "tov", "total", "rank")  # fmt: skip
            run.conn.executemany(
                f"INSERT OR REPLACE INTO hashtag_season_stats (as_of, duration, ht_id, espn_id, {', '.join(cols)}) "
                f"VALUES ({', '.join('?' * (len(cols) + 4))})",
                [(as_of, duration, r["ht_id"], mapping.get(r["ht_id"]), *(r.get(c) for c in cols))
                 for r in parsed["rows"]],
            )  # fmt: skip
            return len(parsed["rows"])

        run.step("season_stats", duration, one)


# ------------------------------------------------------------------ H2H planner + league sync


def _yahoo_num(key: str) -> str:
    return key.rsplit(".p.", 1)[-1] if key else ""


def roster_diff(ht_roster: list[dict], team: Any) -> tuple[list[str], list[str]]:
    """(on Yahoo but not on HT, on HT but not on Yahoo) for one fantasy team; by Yahoo id, else by name."""
    from fbm.yahoo.ids import norm_name

    yahoo = {(_yahoo_num(p.yahoo_key) or norm_name(p.name)): p.name for p in team.players}
    by_name = {norm_name(p.name): p.name for p in team.players}
    ht: dict[str, str] = {}
    for p in ht_roster:
        key = p.get("yahoo_id") or ""
        if key not in yahoo and norm_name(p["name"]) in by_name:
            key = next(k for k, n in yahoo.items() if norm_name(n) == norm_name(p["name"]))
        ht[key or norm_name(p["name"])] = p["name"]
    missing = sorted(n for k, n in yahoo.items() if k not in ht)
    extra = sorted(n for k, n in ht.items() if k not in yahoo)
    return missing, extra


def _pull_planner(
    run: _Run,
    client: HashtagClient,
    league: Any,
    lid: str,
    views: list[str],
    pages: set[str],
    et_today: date,
) -> None:
    state: dict[str, Any] = {}
    me = league.my_team
    opp = league.team(league.opponent_key) if league.opponent_key else None

    def open_planner() -> Page:
        if "page" not in state:
            state["page"] = client.get(league_path("plan-cat-yahoo", lid))
        return state["page"]

    def one(view: str, first: bool) -> int:
        if opp is None:
            raise HashtagError("No opponent this week in the Yahoo snapshot.")
        page = open_planner()
        fields = {
            "DDTEAM2": option_value(page.doc, "DDTEAM2", opp.name),
            "DDWEEK": option_value(page.doc, "DDWEEK", str(league.week)),
            "DDDURATION": _duration_value(page, view),
        }
        page = client.postback(page, fields=fields, event_target="DDDURATION")
        if first:
            page = client.click(page, "UPDATE SCOREBOARD")
        state["page"] = page
        parsed = P.parse_h2h_planner(page.doc)
        problems = []
        if _norm(parsed["my_team"]) != _norm(me.name):
            problems.append(f"my team on HT is '{parsed['my_team']}', Yahoo says '{me.name}'")
        if _norm(parsed["opp_team"]) != _norm(opp.name):
            problems.append(f"opponent on HT is '{parsed['opp_team']}', expected '{opp.name}'")
        if parsed["week"] != league.week or parsed["view"] != view:
            problems.append(f"HT shows week {parsed['week']} {parsed['view']}, asked week {league.week} {view}")
        if problems:
            raise HashtagError("; ".join(problems))
        state["my_roster"] = parsed["roster"] if parsed["roster_side"] == "me" else state.get("my_roster")
        pulled = _iso(run.now())
        run.conn.executemany(
            "INSERT OR REPLACE INTO hashtag_matchup_totals (pulled_at, et_date, week, view, team_key, opponent_key, "
            "category, me_before, opp_before, me_after, opp_after, ht_share) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (pulled, et_today.isoformat(), league.week, view, me.team_key, opp.team_key, cat,
                 v["me_before"], v["opp_before"], v["me_after"], v["opp_after"], v["ht_share"])
                for cat, v in parsed["categories"].items()
            ],
        )  # fmt: skip
        return len(parsed["categories"])

    if "h2h_planner" in pages:
        for i, view in enumerate(views):
            run.step("h2h_planner", view, lambda view=view, first=(i == 0): one(view, first))

    if "league_sync" in pages:

        def sync() -> tuple[int, str]:
            page = open_planner()
            mine = P.parse_h2h_planner(page.doc)
            if mine["roster_side"] != "me":
                page = client.postback(page, fields={"RadioButtonList1": "1"},
                                       event_target="ctl00$ContentPlaceHolder1$RadioButtonList1$0")  # fmt: skip
                mine = P.parse_h2h_planner(page.doc)
            problems = []
            ht_names = {_norm(t) for _, t, _ in select_options(page.doc, "DDTEAM2")}
            missing_teams = [t.name for t in league.teams if _norm(t.name) not in ht_names]
            if missing_teams:
                problems.append(f"teams not on HT: {', '.join(missing_teams)}")
            checked = 0
            for team, roster in ((me, mine["roster"]), (opp, None)):
                if team is None:
                    continue
                if roster is None:
                    if _norm(mine["opp_team"]) != _norm(team.name):
                        page = client.postback(
                            page,
                            fields={"DDTEAM2": option_value(page.doc, "DDTEAM2", team.name)},
                            event_target="DDTEAM2",
                        )
                    page = client.postback(page, fields={"RadioButtonList1": "2"},
                                           event_target="ctl00$ContentPlaceHolder1$RadioButtonList1$1")  # fmt: skip
                    other = P.parse_h2h_planner(page.doc)
                    if other["roster_side"] != "opp":
                        raise HashtagLayoutChanged("league_sync", "could not switch the planner to the opponent")
                    roster = other["roster"]
                if not roster:
                    raise HashtagLayoutChanged("league_sync", f"no roster rows for {team.name}")
                missing, extra = roster_diff(roster, team)
                checked += len(roster)
                if missing or extra:
                    problems.append(
                        f"{team.name}: on Yahoo not HT [{', '.join(missing) or '-'}], on HT not Yahoo "
                        f"[{', '.join(extra) or '-'}]"
                    )
            state["page"] = page
            if problems:
                raise LeagueMismatch("; ".join(problems))
            return checked, f"rosters match ({me.name}{' vs ' + opp.name if opp else ''})"

        run.step("league_sync", None, sync)


# ------------------------------------------------------------------ waiver wire / trade finder multipliers


def multiplier_fields(page: Page, weights: dict[str, float]) -> dict[str, str | None]:
    """Postback fields that set HT's category multipliers: ticked + snapped value per category, unticked if absent
    or <= 0 (punted)."""
    fields: dict[str, str | None] = {}
    for cat, f in P.MULT_FIELD.items():
        w = float(weights.get(cat, 0.0) or 0.0)
        if w <= 0:
            fields["CB" + f] = None
            continue
        step = _snap(w)
        fields["DD" + f] = next(
            (v for v, t, _ in select_options(page.doc, "DD" + f) if abs((P.num(t) or -1) - step) < 1e-9), None
        )
        if fields["DD" + f] is None:
            raise HashtagLayoutChanged("multipliers", f"no x {step:g} option for {cat}")
        fields["CB" + f] = "on"
    return fields


def _same_weights(asked: dict[str, float], got: dict[str, float]) -> bool:
    want = {c: _snap(w) for c, w in asked.items() if (w or 0) > 0}
    return want == {c: _snap(w) for c, w in got.items()}


def _pull_waiver(
    run: _Run, client: HashtagClient, lid: str, views: list[str], weights: dict[str, float] | None, as_of: str
) -> None:
    state: dict[str, Page] = {}
    for view in views:

        def one(view: str = view) -> int:
            page = state.get("page") or client.get(league_path("waiver-cat-yahoo", lid))
            fields: dict[str, str | None] = {"DDDURATION": _duration_value(page, view)}
            if weights:
                fields.update(multiplier_fields(page, weights))
            page = client.postback(page, fields=fields, event_target="DDDURATION")
            state["page"] = page
            parsed = P.parse_waiver(page.doc)
            if parsed["view"] != view:
                raise HashtagError(f"HT returned {parsed['view']} instead of {view}")
            if weights and not _same_weights(weights, parsed["weights"]):
                raise HashtagError(f"HT applied multipliers {parsed['weights']} instead of {weights}")
            mapping = ht_ids.map_players(run.conn, parsed["rows"], commit=False)
            wkey = weights_key(parsed["weights"])
            run.conn.execute(
                "DELETE FROM hashtag_waiver WHERE as_of = ? AND view = ? AND weights = ?", (as_of, view, wkey)
            )
            run.conn.executemany(
                "INSERT OR REPLACE INTO hashtag_waiver (as_of, view, weights, rank, ht_id, espn_id, name, total, data) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                [
                    (as_of, view, wkey, i + 1, r["ht_id"], mapping.get(r["ht_id"]), r["name"], r["total"],
                     json.dumps({k: v for k, v in r.items() if k not in ("ht_id", "name", "total")}))
                    for i, r in enumerate(parsed["rows"])
                ],
            )  # fmt: skip
            return len(parsed["rows"])

        run.step("waiver", view, one)


def _team_key(league: Any | None, name: str) -> str | None:
    if league is None:
        return None
    return next((t.team_key for t in league.teams if _norm(t.name) == _norm(name)), None)


def _pull_team_ranks(run: _Run, client: HashtagClient, league: Any, lid: str, view: str, as_of: str) -> None:
    def one() -> int:
        page = client.get(league_path("scouting-cat-yahoo", lid))
        page = client.select(page, "DDDURATION", value=_duration_value(page, view))
        parsed = P.parse_team_ranks(page.doc)
        if parsed["view"] != view:
            raise HashtagError(f"HT returned {parsed['view']} instead of {view}")
        run.conn.execute("DELETE FROM hashtag_team_ranks WHERE as_of = ? AND view = ?", (as_of, view))
        rows = [
            (as_of, view, _team_key(league, r["team_name"]), r["team_name"], cat, v["value"], v["rank"])
            for r in parsed["rows"]
            for cat, v in r["categories"].items()
        ]
        rows += [(as_of, view, _team_key(league, r["team_name"]), r["team_name"], "total", r["total"], r["rank"])
                 for r in parsed["rows"]]  # fmt: skip
        run.conn.executemany(
            "INSERT OR REPLACE INTO hashtag_team_ranks (as_of, view, team_key, team_name, category, value, rank) "
            "VALUES (?,?,?,?,?,?,?)",
            rows,
        )
        return len(parsed["rows"])

    run.step("team_ranks", view, one)


def _pull_notes(run: _Run, client: HashtagClient) -> None:
    def one() -> int:
        notes = P.parse_notebook(client.get(PATH_NOTEBOOK).doc)
        mapper = ht_ids.name_mapper(run.conn)
        fetched = _iso(run.now())
        rows = []
        for n in notes:
            m = mapper.match("note", n["player"], n["team"])
            key = "|".join((n["note_date"], n["player"], n["source"], n["text"]))
            rows.append((hashlib.sha1(key.encode()).hexdigest()[:20], n["note_date"], None, m.espn_id, n["player"],
                         n["source"], n["text"], fetched))  # fmt: skip
        cur = run.conn.executemany(
            "INSERT OR IGNORE INTO hashtag_notes (id, note_date, ht_id, espn_id, player, source, text, fetched_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            rows,
        )
        return cur.rowcount if cur.rowcount >= 0 else len(rows)

    run.step("notes", None, one)


# ------------------------------------------------------------------ trade finder / next man up


def _trade_finder(
    run: _Run,
    client: HashtagClient,
    league: Any | None,
    lid: str,
    weights: dict[str, float] | None,
    fairness: str,
    partner: str | None,
    out: list[dict],
) -> None:
    def one() -> int:
        page = client.get(league_path("trade-finder-cat-yahoo", lid))
        fields: dict[str, str | None] = {}
        fair_opts = select_options(page.doc, "DDFAIR")
        fair = next((v for v, t, _ in fair_opts if fairness in (v, t) or _norm(fairness) == _norm(t)), None)
        if fair is None:
            raise HashtagError(f"Unknown fairness '{fairness}' (HT offers {[t for _, t, _ in fair_opts]}).")
        fields["DDFAIR"] = fair
        if partner:
            name = partner
            if league is not None:
                name = next((t.name for t in league.teams if partner in (t.team_key, t.name)), partner)
            fields["DDPARTNER"] = option_value(page.doc, "DDPARTNER", name)
        if weights:
            fields.update(multiplier_fields(page, weights))
        page = client.click(page, "FIND TRADES", fields=fields)
        parsed = P.parse_trade_finder(page.doc)
        if weights and not _same_weights(weights, parsed["settings"]["weights"]):
            raise HashtagError(f"HT applied multipliers {parsed['settings']['weights']} instead of {weights}")
        players = [p for idea in parsed["ideas"] for p in idea["give"] + idea["get"]]
        mapping = ht_ids.map_players(run.conn, players, commit=False)
        pulled = _iso(run.now())
        settings = json.dumps({**parsed["settings"], "fairness": fairness, "partner": partner}, sort_keys=True)
        for idea in parsed["ideas"]:
            give = [{"ht_id": p["ht_id"], "espn_id": mapping.get(p["ht_id"]), "name": p["name"]} for p in idea["give"]]
            get = [{"ht_id": p["ht_id"], "espn_id": mapping.get(p["ht_id"]), "name": p["name"]} for p in idea["get"]]
            data = {k: idea[k] for k in ("badges", "ranks_now", "ranks_after", "alternatives")}
            data["players"] = {"give": idea["give"], "get": idea["get"]}
            tkey = _team_key(league, idea["partner_name"])
            cur = run.conn.execute(
                "INSERT INTO hashtag_trade_ideas (pulled_at, settings, partner_team_key, partner_name, give, get, "
                "my_delta, their_delta, data) VALUES (?,?,?,?,?,?,?,?,?)",
                (pulled, settings, tkey, idea["partner_name"], json.dumps(give), json.dumps(get), idea["my_delta"],
                 idea["their_delta"], json.dumps(data)),
            )  # fmt: skip
            out.append({"id": cur.lastrowid, "pulled_at": pulled, "partner_team_key": tkey,
                        "partner_name": idea["partner_name"], "give": give, "get": get,
                        "my_delta": idea["my_delta"], "their_delta": idea["their_delta"], "data": data})  # fmt: skip
        return len(parsed["ideas"])

    run.step("trade_finder", None, one)


def _next_man_up(run: _Run, client: HashtagClient, absent: Iterable[str], as_of: str, counter: list[int]) -> None:
    for espn_id in dict.fromkeys(str(a) for a in absent):

        def one(espn_id: str = espn_id) -> tuple[int, str]:
            who = ht_ids.ht_for_espn(run.conn, espn_id)
            if who is None:
                raise HashtagError(f"ESPN {espn_id} has no HT id yet (import projections first).")
            page = client.get(PATH_NEXT_MAN_UP)
            seasons = select_options(page.doc, "DDSEASON")
            if not seasons:
                raise HashtagLayoutChanged("next_man_up", "no season dropdown")
            page = client.select(page, "DDSEASON", value=seasons[0][0])
            page = client.select(page, "DDTEAM", value=who["team"])
            if who["ht_id"] not in [v for v, _, _ in select_options(page.doc, "DDPLAYER")]:
                return 0, f"{who['name']} is not in HT's {who['team']} player list"
            page = client.select(page, "DDPLAYER", value=who["ht_id"])
            parsed = P.parse_next_man_up(page.doc)
            if parsed["absent_ht_id"] != who["ht_id"]:
                raise HashtagError(f"HT shows {parsed['absent_name']} instead of {who['name']}")
            mates = [m for m in parsed["teammates"] if m["ht_id"]]
            known = ht_ids.lookup(run.conn, [m["ht_id"] for m in mates])
            unknown = [{"ht_id": m["ht_id"], "name": m["name"], "team": who["team"]} for m in mates
                       if not known.get(m["ht_id"])]  # fmt: skip
            if unknown:
                known.update(ht_ids.map_players(run.conn, unknown, commit=False))
            run.conn.execute(
                "DELETE FROM hashtag_next_man_up WHERE as_of = ? AND season = ? AND absent_ht_id = ?",
                (as_of, parsed["season"], who["ht_id"]),
            )
            run.conn.executemany(
                "INSERT OR REPLACE INTO hashtag_next_man_up (as_of, season, absent_ht_id, absent_espn_id, "
                "teammate_ht_id, teammate_espn_id, teammate_name, gp_without, min_with, min_without, data) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                [
                    (as_of, parsed["season"], who["ht_id"], espn_id, m["ht_id"], known.get(m["ht_id"]), m["name"],
                     m["gp_without"], m["min_with"], m["min_without"],
                     json.dumps({"title": parsed["title"], "stats": m["stats"]}))
                    for m in mates
                ],
            )  # fmt: skip
            counter[0] += len(mates)
            return len(mates), parsed["title"]

        run.step("next_man_up", espn_id, one)


# ------------------------------------------------------------------ public API (contract A)


def _open_client(client: HashtagClient | None) -> tuple[HashtagClient, bool]:
    if client is not None:
        return client, False
    return HashtagClient(), True


def run_import(
    conn: sqlite3.Connection,
    *,
    league: Any | None = None,
    views: Iterable[str] = VIEWS,
    pages: Iterable[str] | None = None,
    weights: dict[str, float] | None = None,
    client: HashtagClient | None = None,
    now: datetime | None = None,
    absent_espn_ids: Iterable[str] | None = None,
    fairness: str = "fair",
    partner: str | None = None,
    league_id: str | None = None,
) -> dict[str, Any]:
    """The daily HT import. Returns {run_id, pages: {page[:view]: {status, rows, detail}}, healthy}.

    pages default: projections (every view), h2h_planner (policy.view.weekly_odds daily; every view on Mondays ET),
    league_sync, waiver (policy.view.stream + policy.view.claim, with `weights`), team_ranks (ROS), notes,
    season_stats; trade_finder only when listed; next_man_up only for `absent_espn_ids`."""
    from fbm.hashtag import health
    from fbm.timeutil import nba_today

    run = _Run(conn, now)
    views = [v.upper() for v in views]
    bad = [v for v in views if v not in VIEWS]
    if bad:
        raise ValueError(f"Unknown HT views {bad}; use {VIEWS}.")
    wanted = list(pages) if pages is not None else list(DEFAULT_PAGES)
    if absent_espn_ids and "next_man_up" not in wanted and pages is None:
        wanted.append("next_man_up")
    unknown = [p for p in wanted if p not in ALL_PAGES]
    if unknown:
        raise ValueError(f"Unknown HT pages {unknown}; use {ALL_PAGES}.")
    page_set = set(wanted)
    et_today = nba_today(run.now())
    as_of = et_today.isoformat()
    lid = league_id or _ht_league_id(league)
    try:
        client, owned = _open_client(client)
    except HashtagSessionError as err:
        run.log(wanted[0] if wanted else "projections", None, "session_expired", 0, str(err), _iso(run.now()))
        return {"run_id": run.run_id, "pages": run.report, "healthy": False}
    try:
        if "projections" in page_set:
            _pull_projections(run, client, views, as_of)
        league_pages = page_set & {"h2h_planner", "league_sync", "waiver", "team_ranks", "trade_finder"}
        if league_pages and not run.stopped:
            if not lid:
                for p in sorted(league_pages):
                    run.log(p, None, "error", 0, "No HT league id: set [hashtag] league_id in settings.toml.",
                            _iso(run.now()))  # fmt: skip
            else:
                if page_set & {"h2h_planner", "league_sync"}:
                    try:
                        client.get(league_path("refresh-yahoo", lid))
                    except HashtagSessionError as err:
                        run.log("league_sync", None, "session_expired", 0, str(err), _iso(run.now()))
                        run.stopped = True
                    except HashtagError:
                        pass  # the refresh is best effort; league_sync below checks the result
                    if league is None:
                        for p in ("h2h_planner", "league_sync"):
                            if p in page_set and not run.stopped:
                                run.log(p, None, "error", 0, "No Yahoo league snapshot to compare with.",
                                        _iso(run.now()))  # fmt: skip
                    elif not run.stopped:
                        odds_view = _policy_view(conn, "policy.view.weekly_odds", "STREAM7")
                        pviews = [odds_view] + ([v for v in VIEWS if v != odds_view] if et_today.weekday() == 0 else [])
                        _pull_planner(run, client, league, lid, pviews, page_set, et_today)
                if "waiver" in page_set and not run.stopped:
                    wviews = list(dict.fromkeys([
                        _policy_view(conn, "policy.view.stream", "STREAM7"),
                        _policy_view(conn, "policy.view.claim", "BLEND14"),
                    ]))  # fmt: skip
                    _pull_waiver(run, client, lid, wviews, weights, as_of)
                if "team_ranks" in page_set and not run.stopped:
                    _pull_team_ranks(run, client, league, lid, _policy_view(conn, "policy.view.ros", "ROS"), as_of)
                if "trade_finder" in page_set and not run.stopped:
                    _trade_finder(run, client, league, lid, weights, fairness, partner, [])
        if "notes" in page_set and not run.stopped:
            _pull_notes(run, client)
        if "season_stats" in page_set and not run.stopped:
            _pull_season_stats(run, client, as_of)
        if "next_man_up" in page_set and absent_espn_ids and not run.stopped:
            _next_man_up(run, client, absent_espn_ids, as_of, [0])
    finally:
        if owned:
            client.close()
    st = health.status(conn, enabled=True, now=run.fixed_now, check_session=False)
    return {"run_id": run.run_id, "pages": run.report, "healthy": st.healthy, "health_reasons": st.reasons}


def run_trade_finder(
    conn: sqlite3.Connection,
    *,
    weights: dict[str, float] | None,
    fairness: str = "fair",
    partner: str | None = None,
    client: HashtagClient | None = None,
    league: Any | None = None,
    now: datetime | None = None,
    league_id: str | None = None,
) -> list[dict]:
    """Run HT's Trade Finder with FBM's multipliers; store and return the ideas (raises on failure)."""
    run = _Run(conn, now)
    lid = league_id or _ht_league_id(league)
    if not lid:
        raise HashtagError("No HT league id: set [hashtag] league_id in settings.toml.")
    client, owned = _open_client(client)
    out: list[dict] = []
    try:
        _trade_finder(run, client, league, lid, weights, fairness, partner, out)
    finally:
        if owned:
            client.close()
    res = run.report.get("trade_finder", {})
    if res.get("status") != "ok":
        raise HashtagError(f"Trade Finder {res.get('status')}: {res.get('detail')}")
    return out


def run_next_man_up(
    conn: sqlite3.Connection, absent_espn_ids: Iterable[str], *, client: HashtagClient | None = None,
    now: datetime | None = None,
) -> int:  # fmt: skip
    """HT Next Man Up for each absent player (ESPN ids); returns teammate rows written."""
    from fbm.timeutil import nba_today

    run = _Run(conn, now)
    client, owned = _open_client(client)
    counter = [0]
    try:
        _next_man_up(run, client, absent_espn_ids, nba_today(run.now()).isoformat(), counter)
    finally:
        if owned:
            client.close()
    return counter[0]
