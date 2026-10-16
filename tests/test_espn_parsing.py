from datetime import date

from fbm.data import espn

SCHEDULE = {
    "events": [
        {
            "id": "401909088",
            "date": "2026-10-20T23:30Z",
            "season": {"year": 2027},
            "seasonType": {"id": "2", "type": 2},
            "timeValid": True,
            "competitions": [
                {
                    "neutralSite": False,
                    "competitors": [
                        {"homeAway": "home", "team": {"abbreviation": "DET"}},
                        {"homeAway": "away", "team": {"abbreviation": "BOS"}},
                    ],
                    "status": {"type": {"state": "post", "completed": True}},
                }
            ],
        },
        {
            "id": "401909099",
            # 02:30 UTC on Oct 22 is still Oct 21 in New York.
            "date": "2026-10-22T02:30Z",
            "season": {"year": 2027},
            "seasonType": {"id": "2", "type": 2},
            "competitions": [
                {
                    "competitors": [
                        {"homeAway": "home", "team": {"abbreviation": "LAL"}},
                        {"homeAway": "away", "team": {"abbreviation": "BOS"}},
                    ],
                    "status": {"type": {"state": "pre", "completed": False}},
                }
            ],
        },
    ]
}

KEYS = [
    "minutes",
    "points",
    "fieldGoalsMade-fieldGoalsAttempted",
    "threePointFieldGoalsMade-threePointFieldGoalsAttempted",
    "freeThrowsMade-freeThrowsAttempted",
    "rebounds",
    "assists",
    "turnovers",
    "steals",
    "blocks",
    "offensiveRebounds",
    "defensiveRebounds",
    "fouls",
    "plusMinus",
]
SUMMARY = {
    "header": {"competitions": [{"status": {"type": {"completed": True}}}]},
    "boxscore": {
        "players": [
            {
                "team": {"abbreviation": "DET"},
                "statistics": [
                    {
                        "keys": KEYS,
                        "athletes": [
                            {
                                "athlete": {"id": "6440", "displayName": "Tobias Harris"},
                                "starter": True,
                                "didNotPlay": False,
                                "stats": [
                                    "25",
                                    "8",
                                    "3-5",
                                    "1-1",
                                    "1-3",
                                    "6",
                                    "0",
                                    "1",
                                    "0",
                                    "0",
                                    "1",
                                    "5",
                                    "2",
                                    "+2",
                                ],
                            },
                            {
                                "athlete": {"id": "4397882", "displayName": "Tolu Smith"},
                                "starter": False,
                                "didNotPlay": True,
                                "reason": "COACH'S DECISION",
                                "stats": [],
                            },
                        ],
                    }
                ],
            }
        ]
    },
}

INJURIES = {
    "injuries": [
        {
            "displayName": "Atlanta Hawks",
            "injuries": [
                {
                    "status": "Day-To-Day",
                    "shortComment": "Gilbert (knee) won't play Monday.",
                    "longComment": "Patella tendinitis.",
                    "date": "2026-10-12T21:36Z",
                    "athlete": {
                        "displayName": "Keshon Gilbert",
                        "links": [{"href": "https://www.espn.com/nba/player/_/id/4585618/keshon-gilbert"}],
                        "team": {"abbreviation": "ATL"},
                    },
                    "details": {
                        "fantasyStatus": {"abbreviation": "GTD"},
                        "type": "Knee",
                        "side": "Left",
                        "detail": "Tendinitis",
                        "returnDate": "2026-10-16",
                    },
                }
            ],
        }
    ]
}

NEWS = {
    "articles": [
        {
            "id": 123,
            "headline": "Thompson starts",
            "description": "Amen Thompson will start.",
            "published": "2026-10-16T03:12:00Z",
            "categories": [{"type": "athlete", "athleteId": 4684740}, {"type": "team", "description": "HOU"}],
            "links": {"web": {"href": "https://espn.com/x"}},
        }
    ]
}

BYATHLETE = {
    "categories": [
        {"name": "general", "names": ["gamesPlayed", "avgMinutes", "avgRebounds"]},
        {
            "name": "offensive",
            "names": [
                "avgPoints",
                "avgFieldGoalsMade",
                "avgFieldGoalsAttempted",
                "avgThreePointFieldGoalsMade",
                "avgThreePointFieldGoalsAttempted",
                "avgFreeThrowsMade",
                "avgFreeThrowsAttempted",
                "avgAssists",
                "avgTurnovers",
            ],
        },
        {"name": "defensive", "names": ["avgSteals", "avgBlocks"]},
    ],
    "athletes": [
        {
            "athlete": {
                "id": "3945274",
                "displayName": "Luka Doncic",
                "teamShortName": "LAL",
                "position": {"abbreviation": "G"},
            },
            "categories": [
                {"name": "general", "values": [64, 35.8, 7.7]},
                {
                    "name": "offensive",
                    "totals": ["33.5", "10.8", "22.8", "4.0", "11.0", "7.9", "9.6", "8.3", "4.1"],
                },
                {"name": "defensive", "values": [1.6, 0.5]},
            ],
        }
    ],
}


def test_parse_schedule_et_dates_and_status():
    games = espn.parse_schedule_events(SCHEDULE)
    assert [g.event_id for g in games] == ["401909088", "401909099"]
    g0, g1 = games
    assert (g0.home, g0.away, g0.season, g0.season_type) == ("DET", "BOS", 2027, 2)
    assert g0.completed and g0.state == "post"
    assert g1.et_date == date(2026, 10, 21) and not g1.completed


def test_parse_boxscore_handles_dnp():
    lines = espn.parse_boxscore(SUMMARY, "401")
    played, dnp = lines
    assert (played.fgm, played.fga, played.tpm, played.ftm, played.fta, played.reb, played.tov) == (
        3,
        5,
        1,
        1,
        3,
        6,
        1,
    )
    assert played.played and played.starter and played.minutes == 25
    assert not dnp.played and dnp.dnp_reason == "COACH'S DECISION" and dnp.pts == 0
    assert espn.summary_is_final(SUMMARY)


def test_parse_boxscore_athlete_without_id():
    import copy

    summary = copy.deepcopy(SUMMARY)
    athletes = summary["boxscore"]["players"][0]["statistics"][0]["athletes"]
    athletes[0]["athlete"] = {"displayName": "Linked", "links": [{"href": "https://espn.com/nba/player/_/id/777/x"}]}
    athletes[1]["athlete"] = {"displayName": "No Id"}
    assert [ln.espn_id for ln in espn.parse_boxscore(summary, "401")] == ["777"]


def test_parse_injuries_extracts_id_from_link():
    (inj,) = espn.parse_injuries(INJURIES)
    assert inj.espn_id == "4585618"
    assert (inj.status, inj.fantasy_status, inj.team, inj.return_date) == (
        "Day-To-Day",
        "GTD",
        "ATL",
        "2026-10-16",
    )
    assert inj.injury_type == "Left Knee Tendinitis"


def test_parse_news_athletes():
    (item,) = espn.parse_news(NEWS)
    assert item.athlete_ids == ("4684740",) and item.link == "https://espn.com/x"


def test_parse_byathlete_values_and_totals():
    (avg,) = espn.parse_byathlete(BYATHLETE, 2026)
    assert (avg.espn_id, avg.team, avg.position, avg.gp) == ("3945274", "LAL", "G", 64)
    assert avg.pts == 33.5 and avg.fga == 22.8 and avg.reb == 7.7 and avg.blk == 0.5 and avg.tov == 4.1


def test_current_season():
    assert espn.current_season(date(2026, 10, 16)) == 2027
    assert espn.current_season(date(2027, 4, 1)) == 2027
    assert espn.current_season(date(2027, 8, 1)) == 2027
