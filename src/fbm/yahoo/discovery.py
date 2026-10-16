"""Discover the logged-in user's Yahoo NBA leagues and teams."""

from __future__ import annotations

from typing import Any

from fbm.yahoo.client import YahooClient
from fbm.yahoo.xmlparse import as_list, text


def _user_games(fc: dict[str, Any]) -> list[dict[str, Any]]:
    users = as_list((fc.get("users") or {}).get("user"))
    if not users:
        return []
    return as_list((users[0].get("games") or {}).get("game"))


def list_nba_leagues(client: YahooClient) -> list[dict[str, str]]:
    fc = client.get("users;use_login=1/games;game_keys=nba/leagues")
    leagues: list[dict[str, str]] = []
    for game in _user_games(fc):
        for lg in as_list((game.get("leagues") or {}).get("league")):
            leagues.append(
                {
                    "game_key": text(game.get("game_key"), ""),
                    "season": text(lg.get("season"), "") or text(game.get("season"), ""),
                    "league_key": text(lg.get("league_key"), ""),
                    "name": text(lg.get("name"), ""),
                    "num_teams": text(lg.get("num_teams"), ""),
                    "scoring_type": text(lg.get("scoring_type"), ""),
                    "draft_status": text(lg.get("draft_status"), ""),
                    "current_week": text(lg.get("current_week"), ""),
                    "url": text(lg.get("url"), ""),
                }
            )
    return leagues


def list_my_nba_teams(client: YahooClient) -> list[dict[str, str]]:
    fc = client.get("users;use_login=1/games;game_keys=nba/teams")
    teams: list[dict[str, str]] = []
    for game in _user_games(fc):
        for tm in as_list((game.get("teams") or {}).get("team")):
            teams.append({"team_key": text(tm.get("team_key"), ""), "name": text(tm.get("name"), "")})
    return teams


def team_for_league(teams: list[dict[str, str]], league_key: str) -> dict[str, str] | None:
    prefix = f"{league_key}.t."
    return next((t for t in teams if t["team_key"].startswith(prefix)), None)
