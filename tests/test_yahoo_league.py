from datetime import UTC, date, datetime

import pytest

from fbm.config import LeagueConfig
from fbm.league.yahoo_league import assemble, slots_from_summary, to_roster_player
from fbm.yahoo import xmlparse
from fbm.yahoo.ids import EspnPlayer, IdMapper, espn_team, norm_name
from fbm.yahoo.league_reads import (
    banked_stats,
    parse_players,
    parse_scoreboard,
    parse_standings,
    parse_teams,
    parse_transactions,
)
from fbm.yahoo.roster import parse_roster

NS = 'xmlns="http://fantasysports.yahooapis.com/fantasy/v2/base.rng"'

TEAMS_XML = f"""<?xml version="1.0"?>
<fantasy_content {NS}><league><league_key>466.l.1</league_key>
<teams count="2">
 <team><team_key>466.l.1.t.1</team_key><team_id>1</team_id><name>Glass Cleaners</name>
  <is_owned_by_current_login>1</is_owned_by_current_login><waiver_priority>8</waiver_priority>
  <number_of_moves>3</number_of_moves><number_of_trades>1</number_of_trades>
  <roster_adds><coverage_type>week</coverage_type><coverage_value>1</coverage_value><value>2</value></roster_adds>
  <managers><manager><manager_id>1</manager_id><nickname>Owner</nickname><is_current_login>1</is_current_login>
  </manager></managers></team>
 <team><team_key>466.l.1.t.2</team_key><team_id>2</team_id><name>Bench Mob</name>
  <waiver_priority>1</waiver_priority><number_of_moves>0</number_of_moves><number_of_trades>0</number_of_trades>
  <roster_adds><coverage_type>week</coverage_type><coverage_value>1</coverage_value><value>0</value></roster_adds>
  <managers><manager><manager_id>2</manager_id><nickname>Rival</nickname></manager></managers></team>
</teams></league></fantasy_content>"""

STANDINGS_XML = f"""<?xml version="1.0"?>
<fantasy_content {NS}><league><league_key>466.l.1</league_key><standings><teams count="2">
 <team><team_key>466.l.1.t.2</team_key><name>Bench Mob</name><team_standings><rank>1</rank>
  <playoff_seed>1</playoff_seed>
  <outcome_totals><wins>14</wins><losses>4</losses><ties>0</ties><percentage>.778</percentage></outcome_totals>
  <games_back>-</games_back></team_standings></team>
 <team><team_key>466.l.1.t.1</team_key><name>Glass Cleaners</name><team_standings><rank>2</rank>
  <outcome_totals><wins>10</wins><losses>7</losses><ties>1</ties><percentage>.583</percentage></outcome_totals>
  <games_back>3.5</games_back></team_standings></team>
</teams></standings></league></fantasy_content>"""

SCOREBOARD_XML = f"""<?xml version="1.0"?>
<fantasy_content {NS}><league><league_key>466.l.1</league_key><scoreboard><week>2</week><matchups count="1">
 <matchup><week>2</week><week_start>2026-10-27</week_start><week_end>2026-11-02</week_end><status>midevent</status>
  <is_playoffs>0</is_playoffs>
  <stat_winners>
   <stat_winner><stat_id>5</stat_id><winner_team_key>466.l.1.t.1</winner_team_key></stat_winner>
   <stat_winner><stat_id>17</stat_id><is_tied>1</is_tied></stat_winner>
  </stat_winners>
  <teams count="2">
   <team><team_key>466.l.1.t.1</team_key><name>Glass Cleaners</name>
    <team_stats><coverage_type>week</coverage_type><week>2</week><stats>
     <stat><stat_id>9004003</stat_id><value>120/250</value></stat><stat><stat_id>5</stat_id><value>.480</value></stat>
     <stat><stat_id>9007006</stat_id><value>40/50</value></stat><stat><stat_id>8</stat_id><value>.800</value></stat>
     <stat><stat_id>10</stat_id><value>22</value></stat><stat><stat_id>12</stat_id><value>302</value></stat>
     <stat><stat_id>15</stat_id><value>140</value></stat><stat><stat_id>16</stat_id><value>70</value></stat>
     <stat><stat_id>17</stat_id><value>15</value></stat><stat><stat_id>18</stat_id><value>16</value></stat>
     <stat><stat_id>19</stat_id><value>30</value></stat></stats></team_stats>
    <team_points><coverage_type>week</coverage_type><week>2</week><total>5</total></team_points></team>
   <team><team_key>466.l.1.t.2</team_key><name>Bench Mob</name>
    <team_stats><coverage_type>week</coverage_type><week>2</week><stats>
     <stat><stat_id>9004003</stat_id><value>-/-</value></stat><stat><stat_id>12</stat_id><value>-</value></stat>
     </stats></team_stats>
    <team_points><coverage_type>week</coverage_type><week>2</week><total>3</total></team_points></team>
  </teams></matchup>
</matchups></scoreboard></league></fantasy_content>"""

WAIVERS_XML = f"""<?xml version="1.0"?>
<fantasy_content {NS}><league><league_key>466.l.1</league_key><players count="1">
 <player><player_key>466.p.7001</player_key><player_id>7001</player_id>
  <name><full>Nicolas Claxton</full></name><editorial_team_abbr>BKN</editorial_team_abbr>
  <display_position>C</display_position><eligible_positions><position>C</position><position>Util</position>
  </eligible_positions><status>GTD</status>
  <ownership><ownership_type>waivers</ownership_type><waiver_date>2026-10-29</waiver_date></ownership>
  <percent_owned><coverage_type>week</coverage_type><value>41</value><delta>+6</delta></percent_owned></player>
</players></league></fantasy_content>"""

TRANSACTIONS_XML = f"""<?xml version="1.0"?>
<fantasy_content {NS}><league><league_key>466.l.1</league_key><transactions count="2">
 <transaction><transaction_key>466.l.1.tr.12</transaction_key><type>add/drop</type><status>successful</status>
  <timestamp>1793000000</timestamp><players count="2">
   <player><player_key>466.p.7001</player_key><player_id>7001</player_id><name><full>Nicolas Claxton</full></name>
    <editorial_team_abbr>BKN</editorial_team_abbr><transaction_data><type>add</type><source_type>waivers</source_type>
    <destination_type>team</destination_type><destination_team_key>466.l.1.t.2</destination_team_key>
    </transaction_data></player>
   <player><player_key>466.p.7002</player_key><player_id>7002</player_id><name><full>Some Guard</full></name>
    <editorial_team_abbr>NYK</editorial_team_abbr><transaction_data><type>drop</type><source_type>team</source_type>
    <source_team_key>466.l.1.t.2</source_team_key><destination_type>waivers</destination_type></transaction_data>
   </player></players></transaction>
 <transaction><transaction_key>466.l.1.pt.3</transaction_key><type>pending_trade</type><status>proposed</status>
  <timestamp>1793100000</timestamp><trader_team_key>466.l.1.t.2</trader_team_key>
  <tradee_team_key>466.l.1.t.1</tradee_team_key><trade_note>bigs for guards?</trade_note><players count="1">
   <player><player_key>466.p.7003</player_key><player_id>7003</player_id><name><full>Big Man</full></name>
    <editorial_team_abbr>GSW</editorial_team_abbr><transaction_data><type>pending_trade</type>
    <source_type>team</source_type><source_team_key>466.l.1.t.2</source_team_key><destination_type>team</destination_type>
    <destination_team_key>466.l.1.t.1</destination_team_key></transaction_data></player></players></transaction>
</transactions></league></fantasy_content>"""


def roster_xml(team_key: str, players: list[tuple[str, str, str, str, list[str], str]]) -> str:
    rows = "".join(
        f"<player><player_key>466.p.{pid}</player_key><name><full>{name}</full></name>"
        f"<editorial_team_abbr>{team}</editorial_team_abbr><display_position>{elig[0]}</display_position>"
        "<eligible_positions>" + "".join(f"<position>{e}</position>" for e in elig) + "</eligible_positions>"
        f"<selected_position><coverage_type>date</coverage_type><position>{slot}</position></selected_position>"
        f"<status>{status}</status></player>"
        for pid, name, team, status, elig, slot in players
    )
    return (
        f'<?xml version="1.0"?><fantasy_content {NS}><team><team_key>{team_key}</team_key><roster>'
        f'<is_editable>1</is_editable><players count="{len(players)}">{rows}</players></roster></team>'
        "</fantasy_content>"
    )


def test_parse_teams_and_standings():
    teams = parse_teams(xmlparse.parse(TEAMS_XML))
    assert [(t.team_key, t.is_mine, t.waiver_priority, t.week_adds) for t in teams] == [
        ("466.l.1.t.1", True, 8, 2),
        ("466.l.1.t.2", False, 1, 0),
    ]
    assert teams[0].manager == "Owner" and teams[0].trades == 1
    rows = parse_standings(xmlparse.parse(STANDINGS_XML))
    assert [r.name for r in rows] == ["Bench Mob", "Glass Cleaners"]
    assert (rows[1].wins, rows[1].losses, rows[1].ties, rows[1].pct, rows[1].games_back) == (10, 7, 1, 0.583, "3.5")


def test_parse_scoreboard_and_banked_stats():
    (m,) = parse_scoreboard(xmlparse.parse(SCOREBOARD_XML))
    assert (m.week, m.week_start, m.week_end, m.status) == (2, date(2026, 10, 27), date(2026, 11, 2), "midevent")
    assert m.opponent_of("466.l.1.t.1").team_key == "466.l.1.t.2"
    assert m.opponent_of("466.l.1.t.9") is None
    assert m.stat_winners == {"5": "466.l.1.t.1", "17": None}
    mine = banked_stats(m.team("466.l.1.t.1").stats)
    assert mine == {
        "fgm": 120.0, "fga": 250.0, "ftm": 40.0, "fta": 50.0, "tpm": 22.0,
        "pts": 302.0, "reb": 140.0, "ast": 70.0, "stl": 15.0, "blk": 16.0, "tov": 30.0,
    }  # fmt: skip
    assert banked_stats(m.team("466.l.1.t.2").stats) == {"fgm": 0.0, "fga": 0.0}  # "-" = nothing yet


def test_parse_waiver_players():
    (p,) = parse_players(xmlparse.parse(WAIVERS_XML))
    assert (p.player_id, p.name, p.nba_team, p.ownership_type, p.waiver_date) == (
        "7001",
        "Nicolas Claxton",
        "BKN",
        "waivers",
        date(2026, 10, 29),
    )
    assert p.eligible == ("C", "Util") and p.percent_owned == 41.0 and p.percent_owned_delta == 6.0


def test_parse_transactions_and_pending_trade():
    add_drop, offer = parse_transactions(xmlparse.parse(TRANSACTIONS_XML))
    assert add_drop.type == "add/drop" and add_drop.timestamp == datetime.fromtimestamp(1793000000, UTC)
    assert [(p.name, p.action, p.source_type, p.destination_team_key) for p in add_drop.players] == [
        ("Nicolas Claxton", "add", "waivers", "466.l.1.t.2"),
        ("Some Guard", "drop", "team", ""),
    ]
    assert (offer.status, offer.trader_team_key, offer.tradee_team_key, offer.trade_note) == (
        "proposed",
        "466.l.1.t.2",
        "466.l.1.t.1",
        "bigs for guards?",
    )


ESPN = [
    EspnPlayer("e1", "Nic Claxton", "BKN"),
    EspnPlayer("e2", "Shai Gilgeous-Alexander", "OKC"),
    EspnPlayer("e3", "Jalen Williams", "OKC"),
    EspnPlayer("e4", "Jalen Williams", "DEN"),
    EspnPlayer("e5", "Nikola Jokić", "DEN"),
    EspnPlayer("e6", "Kenyon Martin Jr.", "UTAH"),
    EspnPlayer("e7", "Herbert Jones", "NO"),
]


def test_names_and_teams_normalize():
    assert norm_name("Kenyon Martin Jr.") == "kenyon martin"
    assert norm_name("Shai Gilgeous-Alexander") == "shai gilgeous alexander"
    assert norm_name("Nikola Jokić") == "nikola jokic"
    assert (espn_team("GSW"), espn_team("Uta"), espn_team("pho"), espn_team("BOS")) == ("GS", "UTAH", "PHX", "BOS")


def test_id_mapper_methods():
    m = IdMapper(ESPN, overrides={"9": "e3"})
    assert m.match("1", "Shai Gilgeous-Alexander", "OKC").method == "exact"
    assert m.match("2", "Nikola Jokic", "DEN").espn_id == "e5"
    assert (m.match("3", "Jalen Williams", "DEN").espn_id, m.match("3", "Jalen Williams", "DEN").method) == (
        "e4",
        "exact+team",
    )
    assert m.match("4", "Jalen Williams", "BOS").espn_id is None  # ambiguous, no team match
    assert m.match("5", "Nicolas Claxton", "BKN").method == "initial+team"
    assert m.match("6", "Kenyon Martin", "UTA").espn_id == "e6"
    assert m.match("7", "Herb Jones", "NOP").espn_id == "e7"
    assert m.match("9", "Anyone", "LAL").method == "override"
    assert m.match("8", "Nobody Known", "LAL").method == "unmatched"


SUMMARY = {
    "name": "Real League",
    "season": "2026",
    "current_week": "2",
    "settings": {"scoring_type": "head", "max_weekly_adds": "4"},
    "roster_slots": [
        {"position": "PG", "count": 1, "is_starting": True},
        {"position": "C", "count": 1, "is_starting": True},
        {"position": "Util", "count": 1, "is_starting": True},
        {"position": "BN", "count": 2, "is_starting": False},
        {"position": "IL", "count": 1, "is_starting": False},
    ],
    "scoring_categories": [{"stat_id": s} for s in ("5", "8", "10", "12", "15", "16", "17", "18", "19")],
}


def test_assemble_league_state():
    cfg = LeagueConfig("466", "2026", "466.l.1", "Real League", "466.l.1.t.1", "Glass Cleaners")
    rosters = {
        "466.l.1.t.1": parse_roster(
            xmlparse.parse(
                roster_xml(
                    "466.l.1.t.1",
                    [
                        ("10", "Shai Gilgeous-Alexander", "OKC", "", ["PG", "SG", "G", "Util"], "PG"),
                        ("11", "Nikola Jokic", "DEN", "INJ", ["C", "Util", "IL"], "IL"),
                        ("12", "Mystery Rookie", "LAL", "", ["SF", "Util"], "BN"),
                    ],
                )
            ),
            "466.l.1.t.1",
            date(2026, 10, 28),
        ),
        "466.l.1.t.2": parse_roster(
            xmlparse.parse(
                roster_xml("466.l.1.t.2", [("13", "Jalen Williams", "OKC", "GTD", ["SG", "SF", "Util"], "Util")])
            ),
            "466.l.1.t.2",
            date(2026, 10, 28),
        ),
    }
    league, matches = assemble(
        cfg,
        SUMMARY,
        parse_teams(xmlparse.parse(TEAMS_XML)),
        rosters,
        parse_scoreboard(xmlparse.parse(SCOREBOARD_XML)),
        parse_standings(xmlparse.parse(STANDINGS_XML)),
        parse_players(xmlparse.parse(WAIVERS_XML)),
        IdMapper(ESPN),
        datetime(2026, 10, 28, 7, 0, tzinfo=UTC),
    )
    assert league.source == "yahoo" and league.name == "Real League" and league.week == 2
    assert (league.week_start, league.week_end) == (date(2026, 10, 27), date(2026, 11, 2))
    assert league.max_weekly_adds == 4 and league.opponent_key == "466.l.1.t.2"
    assert [s.position for s in league.slots] == ["PG", "C", "UTIL", "BN", "IL"]
    assert league.starting_slots() == ["PG", "C", "UTIL"] and league.roster_size() == 5
    me = league.my_team
    assert [p.player_id for p in me.players] == ["e2", "e5", "yahoo:12"]
    jokic = me.players[1]
    assert (jokic.selected, jokic.status, jokic.nba_team, "IL" in jokic.eligible) == ("IL", "INJ", "DEN", True)
    assert league.team("466.l.1.t.2").players[0].player_id == "e3"  # OKC Jalen Williams by team
    assert league.waiver_priority == {"466.l.1.t.1": 8, "466.l.1.t.2": 1}
    assert league.adds_used == {"466.l.1.t.1": 2, "466.l.1.t.2": 0}
    assert league.banked["466.l.1.t.1"]["pts"] == 302.0
    assert league.on_waivers == {"e1": "2026-10-29"}
    assert league.standings[0]["name"] == "Bench Mob"
    assert league.fetched_at == "2026-10-28T07:00:00+00:00"
    assert {m.method for m in matches} >= {"exact", "exact+team", "unmatched", "initial+team"}


def test_slots_fall_back_and_roster_player_positions():
    assert slots_from_summary({})[0].position == "PG"
    rp = parse_roster(
        xmlparse.parse(roster_xml("t", [("1", "X", "GSW", "", ["PG", "Util"], "Util")])), "t", date(2026, 1, 1)
    ).players[0]
    from fbm.yahoo.ids import Match

    p = to_roster_player(rp, Match("1", None, "unmatched", "X", "GS"))
    assert (p.player_id, p.nba_team, p.selected, p.eligible, p.yahoo_key) == (
        "yahoo:1",
        "GS",
        "UTIL",
        ("PG", "UTIL"),
        "466.p.1",
    )


@pytest.mark.parametrize("value", ["", "-"])
def test_banked_stats_handles_empty_values(value):
    assert banked_stats({"12": value}) == {}
