from datetime import date

from fbm.yahoo import settings as yahoo_settings
from fbm.yahoo import xmlparse
from fbm.yahoo.discovery import team_for_league
from fbm.yahoo.roster import parse_roster

ROSTER_XML = """<?xml version="1.0" encoding="UTF-8"?>
<fantasy_content xmlns="http://fantasysports.yahooapis.com/fantasy/v2/base.rng" copyright="x">
  <team>
    <team_key>466.l.1.t.3</team_key>
    <roster>
      <coverage_type>date</coverage_type>
      <date>2026-10-21</date>
      <is_editable>1</is_editable>
      <players count="1">
        <player>
          <player_key>466.p.6014</player_key>
          <name><full>Test Player</full></name>
          <editorial_team_abbr>BOS</editorial_team_abbr>
          <display_position>PG</display_position>
          <eligible_positions><position>PG</position></eligible_positions>
          <selected_position><coverage_type>date</coverage_type><date>2026-10-21</date>
            <position>BN</position></selected_position>
          <status>GTD</status>
          <is_editable>1</is_editable>
        </player>
      </players>
    </roster>
  </team>
</fantasy_content>"""


def test_single_child_plural_containers_become_lists():
    fc = xmlparse.parse(ROSTER_XML)
    players = fc["team"]["roster"]["players"]["player"]
    assert isinstance(players, list) and len(players) == 1
    assert players[0]["eligible_positions"]["position"] == ["PG"]
    # selected_position.position is a scalar, not a list
    assert players[0]["selected_position"]["position"] == "BN"


def test_parse_roster():
    fc = xmlparse.parse(ROSTER_XML)
    r = parse_roster(fc, "466.l.1.t.3", date(2026, 10, 21))
    assert r.is_editable
    p = r.players[0]
    assert p.name == "Test Player"
    assert p.eligible_positions == ("PG",)
    assert p.selected_position == "BN"
    assert p.status == "GTD"
    assert r.assignments() == {"466.p.6014": "BN"}


def test_error_description():
    xml = (
        '<?xml version="1.0"?><error xmlns:yahoo="http://www.yahooapis.com/v1/base.rng" '
        'yahoo:uri="x"><description>You do not have the appropriate OAuth scope permissions.</description>'
        "<detail/></error>"
    )
    assert "scope" in xmlparse.error_description(xml)


SETTINGS_XML = """<?xml version="1.0"?>
<fantasy_content>
  <league>
    <league_key>466.l.1</league_key><name>Test League</name><season>2026</season><num_teams>12</num_teams>
    <settings>
      <scoring_type>head</scoring_type><waiver_type>R</waiver_type><max_weekly_adds>4</max_weekly_adds>
      <roster_positions>
        <roster_position><position>PG</position><count>1</count><is_starting_position>1</is_starting_position>
        </roster_position>
        <roster_position><position>BN</position><count>3</count><is_starting_position>0</is_starting_position>
        </roster_position>
      </roster_positions>
      <stat_categories><stats>
        <stat><stat_id>9004003</stat_id><enabled>1</enabled><name>FGM/FGA</name><display_name>FGM/A</display_name>
          <sort_order>1</sort_order><is_only_display_stat>1</is_only_display_stat></stat>
        <stat><stat_id>5</stat_id><enabled>1</enabled><name>Field Goal Percentage</name>
          <display_name>FG%</display_name><sort_order>1</sort_order></stat>
        <stat><stat_id>19</stat_id><enabled>1</enabled><name>Turnovers</name><display_name>TO</display_name>
          <sort_order>0</sort_order></stat>
      </stats></stat_categories>
    </settings>
  </league>
</fantasy_content>"""


def test_settings_summary():
    league = xmlparse.parse(SETTINGS_XML)["league"]
    s = yahoo_settings.summarize(league)
    assert s["scoring_type_label"].startswith("H2H Categories")
    assert [c["display_name"] for c in s["scoring_categories"]] == ["FG%", "TO"]
    assert s["scoring_categories"][1]["higher_is_better"] is False
    assert [c["display_name"] for c in s["display_only_stats"]] == ["FGM/A"]
    assert s["roster_slots"] == [
        {"position": "PG", "count": 1, "is_starting": True},
        {"position": "BN", "count": 3, "is_starting": False},
    ]
    assert s["settings"]["max_weekly_adds"] == "4"
    assert "| FG% | 5 | higher |" in yahoo_settings.to_markdown(s)


def test_team_for_league():
    teams = [{"team_key": "466.l.10.t.1", "name": "A"}, {"team_key": "466.l.1.t.3", "name": "B"}]
    assert team_for_league(teams, "466.l.1")["name"] == "B"
    assert team_for_league(teams, "466.l.99") is None
