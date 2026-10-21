"""ESPN public NBA endpoints (no auth) and pure parsers.

Season convention: ESPN `season` is the END year (2027 == 2026-27). Internal code uses the same convention.
NBA.com CDN / stats.nba.com are blocked on the owner's network, so ESPN is the primary NBA source.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from fbm.data.http import Fetcher
from fbm.timeutil import ET

SITE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
WEB = "https://site.web.api.espn.com/apis/common/v3/sports/basketball/nba"
REGULAR_SEASON = 2
POSTSEASON = 3

_ATHLETE_ID_RE = re.compile(r"/id/(\d+)")


@dataclass(frozen=True)
class Team:
    espn_id: str
    abbr: str
    name: str


@dataclass(frozen=True)
class Game:
    event_id: str
    season: int
    season_type: int
    start_utc: datetime
    et_date: date
    home: str
    away: str
    state: str  # pre | in | post
    completed: bool
    neutral: bool
    time_valid: bool


@dataclass(frozen=True)
class BoxLine:
    event_id: str
    espn_id: str
    name: str
    team: str
    starter: bool
    played: bool
    dnp_reason: str
    minutes: float
    pts: int
    fgm: int
    fga: int
    tpm: int
    tpa: int
    ftm: int
    fta: int
    reb: int
    ast: int
    tov: int
    stl: int
    blk: int


@dataclass(frozen=True)
class Injury:
    espn_id: str
    name: str
    team: str
    status: str
    fantasy_status: str
    short_comment: str
    long_comment: str
    reported_at: str
    return_date: str | None
    injury_type: str


@dataclass(frozen=True)
class NewsItem:
    news_id: str
    published: str
    headline: str
    description: str
    athlete_ids: tuple[str, ...]
    link: str


@dataclass(frozen=True)
class SeasonAverage:
    season: int
    espn_id: str
    name: str
    team: str
    position: str
    gp: int
    minutes: float
    pts: float
    fgm: float
    fga: float
    tpm: float
    tpa: float
    ftm: float
    fta: float
    reb: float
    ast: float
    tov: float
    stl: float
    blk: float


def current_season(today: date) -> int:
    """ESPN season end-year for a calendar date (seasons roll over in September)."""
    return today.year + 1 if today.month >= 9 else today.year


def parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def athlete_id_from_links(links: list[dict[str, Any]] | None) -> str:
    for link in links or []:
        match = _ATHLETE_ID_RE.search(link.get("href", ""))
        if match:
            return match.group(1)
    return ""


def parse_teams(payload: dict[str, Any]) -> list[Team]:
    teams = payload["sports"][0]["leagues"][0]["teams"]
    return [Team(t["team"]["id"], t["team"]["abbreviation"], t["team"]["displayName"]) for t in teams]


def parse_roster(payload: dict[str, Any]) -> list[tuple[str, str, str]]:
    """A team's current roster (incl. two-way players) as (espn_id, name, position abbreviation)."""
    return [
        (str(a["id"]), a.get("fullName") or a.get("displayName", ""), (a.get("position") or {}).get("abbreviation", ""))
        for a in payload.get("athletes", [])
        if a.get("id")
    ]


def parse_schedule_events(payload: dict[str, Any]) -> list[Game]:
    games: list[Game] = []
    for ev in payload.get("events", []):
        comp = ev["competitions"][0]
        sides = {c["homeAway"]: c["team"]["abbreviation"] for c in comp["competitors"]}
        status_type = (comp.get("status") or ev.get("status") or {}).get("type", {})
        start = parse_utc(ev["date"])
        season_type = ev.get("seasonType", {})
        games.append(
            Game(
                event_id=str(ev["id"]),
                season=int(ev.get("season", {}).get("year", 0)),
                season_type=int(season_type.get("type", season_type.get("id", 0)) or 0),
                start_utc=start,
                et_date=start.astimezone(ET).date(),
                home=sides.get("home", ""),
                away=sides.get("away", ""),
                state=status_type.get("state", "pre"),
                completed=bool(status_type.get("completed", False)),
                neutral=bool(comp.get("neutralSite", False)),
                time_valid=bool(ev.get("timeValid", comp.get("timeValid", True))),
            )
        )
    return games


def _made_att(value: str) -> tuple[int, int]:
    try:
        made, att = value.split("-", 1)
        return int(made), int(att)
    except (ValueError, AttributeError):
        return 0, 0


def _int(value: str) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def parse_boxscore(summary: dict[str, Any], event_id: str) -> list[BoxLine]:
    lines: list[BoxLine] = []
    for team_block in (summary.get("boxscore") or {}).get("players", []):
        team = team_block["team"]["abbreviation"]
        for stat_block in team_block.get("statistics", []):
            keys = stat_block.get("keys") or []
            idx = {k: i for i, k in enumerate(keys)}
            for ath in stat_block.get("athletes", []):
                athlete = ath.get("athlete") or {}
                athlete_id = str(athlete.get("id") or athlete_id_from_links(athlete.get("links")))
                if not athlete_id:
                    continue
                stats = ath.get("stats") or []
                played = bool(stats) and not ath.get("didNotPlay", False)

                def get(key: str, _stats: list[str] = stats, _idx: dict[str, int] = idx) -> str:
                    i = _idx.get(key)
                    return _stats[i] if i is not None and i < len(_stats) else "0"

                fgm, fga = _made_att(get("fieldGoalsMade-fieldGoalsAttempted"))
                tpm, tpa = _made_att(get("threePointFieldGoalsMade-threePointFieldGoalsAttempted"))
                ftm, fta = _made_att(get("freeThrowsMade-freeThrowsAttempted"))
                lines.append(
                    BoxLine(
                        event_id=str(event_id),
                        espn_id=athlete_id,
                        name=athlete.get("displayName", ""),
                        team=team,
                        starter=bool(ath.get("starter", False)),
                        played=played,
                        dnp_reason="" if played else (ath.get("reason") or "DNP"),
                        minutes=float(_int(get("minutes"))) if played else 0.0,
                        pts=_int(get("points")) if played else 0,
                        fgm=fgm if played else 0,
                        fga=fga if played else 0,
                        tpm=tpm if played else 0,
                        tpa=tpa if played else 0,
                        ftm=ftm if played else 0,
                        fta=fta if played else 0,
                        reb=_int(get("rebounds")) if played else 0,
                        ast=_int(get("assists")) if played else 0,
                        tov=_int(get("turnovers")) if played else 0,
                        stl=_int(get("steals")) if played else 0,
                        blk=_int(get("blocks")) if played else 0,
                    )
                )
    return lines


def summary_is_final(summary: dict[str, Any]) -> bool:
    comps = (summary.get("header") or {}).get("competitions") or [{}]
    return bool(((comps[0].get("status") or {}).get("type") or {}).get("completed", False))


def parse_injuries(payload: dict[str, Any]) -> list[Injury]:
    out: list[Injury] = []
    for team_block in payload.get("injuries", []):
        for inj in team_block.get("injuries", []):
            ath = inj.get("athlete") or {}
            details = inj.get("details") or {}
            out.append(
                Injury(
                    espn_id=str(ath.get("id") or athlete_id_from_links(ath.get("links"))),
                    name=ath.get("displayName", ""),
                    team=(ath.get("team") or {}).get("abbreviation", ""),
                    status=inj.get("status", ""),
                    fantasy_status=(details.get("fantasyStatus") or {}).get("abbreviation", ""),
                    short_comment=inj.get("shortComment", ""),
                    long_comment=inj.get("longComment", ""),
                    reported_at=inj.get("date", ""),
                    return_date=details.get("returnDate"),
                    injury_type=" ".join(
                        x
                        for x in (details.get("side"), details.get("type"), details.get("detail"))
                        if x and x != "Not Specified"
                    ),
                )
            )
    return out


def parse_news(payload: dict[str, Any]) -> list[NewsItem]:
    items: list[NewsItem] = []
    for art in payload.get("articles", []):
        athletes = tuple(
            str(c.get("athleteId") or (c.get("athlete") or {}).get("id"))
            for c in art.get("categories", [])
            if c.get("type") == "athlete" and (c.get("athleteId") or (c.get("athlete") or {}).get("id"))
        )
        link = ((art.get("links") or {}).get("web") or {}).get("href", "")
        items.append(
            NewsItem(
                news_id=str(art.get("id") or art.get("nowId") or art.get("headline")),
                published=art.get("published", ""),
                headline=art.get("headline", ""),
                description=art.get("description", ""),
                athlete_ids=athletes,
                link=link,
            )
        )
    return items


_AVG_FIELDS = {
    "gp": ("general", "gamesPlayed"),
    "minutes": ("general", "avgMinutes"),
    "reb": ("general", "avgRebounds"),
    "pts": ("offensive", "avgPoints"),
    "fgm": ("offensive", "avgFieldGoalsMade"),
    "fga": ("offensive", "avgFieldGoalsAttempted"),
    "tpm": ("offensive", "avgThreePointFieldGoalsMade"),
    "tpa": ("offensive", "avgThreePointFieldGoalsAttempted"),
    "ftm": ("offensive", "avgFreeThrowsMade"),
    "fta": ("offensive", "avgFreeThrowsAttempted"),
    "ast": ("offensive", "avgAssists"),
    "tov": ("offensive", "avgTurnovers"),
    "stl": ("defensive", "avgSteals"),
    "blk": ("defensive", "avgBlocks"),
}


def _num(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return 0.0


def served_season(payload: dict[str, Any]) -> int | None:
    """The season ESPN actually returned (`requestedSeason.year`); it silently falls back to the previous season
    when the requested one has no stats yet."""
    year = (payload.get("requestedSeason") or {}).get("year")
    return int(year) if year else None


def parse_byathlete(payload: dict[str, Any], season: int) -> list[SeasonAverage]:
    names = {cat["name"]: cat.get("names", []) for cat in payload.get("categories", [])}
    out: list[SeasonAverage] = []
    for entry in payload.get("athletes", []):
        ath = entry["athlete"]
        values: dict[str, dict[str, float]] = {}
        for cat in entry.get("categories", []):
            raw = cat.get("values") or cat.get("totals") or []
            values[cat["name"]] = {n: _num(v) for n, v in zip(names.get(cat["name"], []), raw, strict=False)}

        def pick(field: str, _values: dict[str, dict[str, float]] = values) -> float:
            cat, key = _AVG_FIELDS[field]
            return _values.get(cat, {}).get(key, 0.0)

        out.append(
            SeasonAverage(
                season=season,
                espn_id=str(ath["id"]),
                name=ath.get("displayName", ""),
                team=ath.get("teamShortName", ""),
                position=(ath.get("position") or {}).get("abbreviation", ""),
                gp=int(pick("gp")),
                **{f: pick(f) for f in _AVG_FIELDS if f != "gp"},
            )
        )
    return out


class EspnClient:
    def __init__(self, fetcher: Fetcher | None = None):
        self.fetcher = fetcher or Fetcher()

    def teams(self) -> list[Team]:
        return parse_teams(self.fetcher.get_json(f"{SITE}/teams", ttl=7 * 86400))

    def team_schedule(
        self, team_id: str, season: int, season_type: int = REGULAR_SEASON, ttl: float = 3 * 3600
    ) -> list[Game]:
        payload = self.fetcher.get_json(
            f"{SITE}/teams/{team_id}/schedule", params={"season": season, "seasontype": season_type}, ttl=ttl
        )
        return parse_schedule_events(payload)

    def team_roster(self, team_id: str, ttl: float = 3 * 3600) -> list[tuple[str, str, str]]:
        return parse_roster(self.fetcher.get_json(f"{SITE}/teams/{team_id}/roster", ttl=ttl))

    def summary(self, event_id: str) -> dict[str, Any]:
        return self.fetcher.get_json(f"{SITE}/summary", params={"event": event_id}, cache=False)

    def injuries(self) -> list[Injury]:
        return parse_injuries(self.fetcher.get_json(f"{SITE}/injuries", ttl=0, cache=False))

    def news(self, limit: int = 50) -> list[NewsItem]:
        return parse_news(self.fetcher.get_json(f"{SITE}/news", params={"limit": limit}, ttl=0, cache=False))

    def season_averages(
        self, season: int, season_type: int = REGULAR_SEASON, page_size: int = 100, ttl: float = 6 * 3600
    ) -> list[SeasonAverage]:
        out: list[SeasonAverage] = []
        page, pages = 1, 1
        while page <= pages:
            payload = self.fetcher.get_json(
                f"{WEB}/statistics/byathlete",
                params={
                    "region": "us",
                    "lang": "en",
                    "contentorigin": "espn",
                    "isqualified": "false",
                    "page": page,
                    "limit": page_size,
                    "sort": "general.gamesPlayed:desc",
                    "season": season,
                    "seasontype": season_type,
                },
                ttl=ttl,
            )
            pages = int((payload.get("pagination") or {}).get("pages", 1))
            served = served_season(payload)
            if served is not None and served != season:
                return []  # ESPN fell back to another season (e.g. before it has stats for this one)
            out.extend(parse_byathlete(payload, season))
            page += 1
        return out
