from datetime import date

import pytest

from fbm.data import espn, queries, sync
from fbm.data.http import FetchError
from fbm.store import db
from tests.test_espn_parsing import BYATHLETE, INJURIES, NEWS, SCHEDULE, SUMMARY


class FakeEspn:
    def __init__(self):
        self.summary_calls = []
        self.injury_payload = INJURIES

    def teams(self):
        return [espn.Team("2", "BOS", "Boston Celtics"), espn.Team("8", "DET", "Detroit Pistons")]

    def team_schedule(self, team_id, season, season_type=2, ttl=0):
        return espn.parse_schedule_events(SCHEDULE)

    def summary(self, event_id):
        self.summary_calls.append(event_id)
        return SUMMARY

    def injuries(self):
        return espn.parse_injuries(self.injury_payload)

    def news(self, limit=50):
        return espn.parse_news(NEWS)

    def season_averages(self, season):
        return espn.parse_byathlete(BYATHLETE, season)

    def team_roster(self, team_id):
        return {"2": [("6440", "Tyrese Haliburton", "PG")], "8": []}.get(team_id, [])


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def test_daily_sync_end_to_end(conn):
    fake = FakeEspn()
    results = sync.daily_sync(conn, fake, date(2026, 10, 22))
    assert [r.job for r in results] == [
        "schedule 2027",
        "boxscores 2027",
        "season averages 2027",
        "injuries",
        "news",
        "rosters",
    ]
    # Only the completed game is box-synced; re-running does not refetch it.
    assert fake.summary_calls == ["401909088"]
    sync.daily_sync(conn, fake, date(2026, 10, 22))
    assert fake.summary_calls == ["401909088"]

    lines = queries.player_lines(conn, "6440", 2027)
    assert len(lines) == 1 and lines[0]["fga"] == 5 and lines[0]["et_date"] == "2026-10-20"
    assert queries.player_lines(conn, "4397882", 2027) == []  # DNP excluded by default

    counts = queries.team_game_counts(conn, date(2026, 10, 20), date(2026, 10, 26))
    assert counts["BOS"] == 2 and counts["DET"] == 1 and counts["LAL"] == 1
    assert len(queries.games_on(conn, date(2026, 10, 21))) == 1
    assert queries.injury(conn, "4585618")["status"] == "Day-To-Day"


def test_injury_changes_detected(conn):
    fake = FakeEspn()
    _, first = sync.sync_injuries(conn, fake)
    assert first[0]["old_status"] == "Healthy" and first[0]["new_status"] == "Day-To-Day"
    _, none = sync.sync_injuries(conn, fake)
    assert none == []
    fake.injury_payload = {"injuries": []}
    _, cleared = sync.sync_injuries(conn, fake)
    assert cleared[0]["new_status"] == "Cleared"
    assert conn.execute("SELECT COUNT(*) FROM injury_events").fetchone()[0] == 2


def test_player_team_not_overwritten_by_older_game(conn):
    db.upsert_season_averages(conn, espn.parse_byathlete(BYATHLETE, 2027), as_of=date(2026, 11, 1))
    old_game = {"event_id": "1", "et_date": "2026-10-20", "season": 2027}
    line = espn.BoxLine("1", "3945274", "Luka Doncic", "DAL", True, True, "", 30, 30, 10, 20, 3, 8, 7, 8, 8, 9, 4, 1, 0)
    conn.execute(
        "INSERT INTO games(event_id, season, season_type, start_utc, et_date, home, away, state, "
        "completed, neutral, time_valid, updated_at) VALUES ('1',2027,2,'x','2026-10-20','DAL','X','post',"
        "1,0,1,'x')"
    )
    db.store_boxscore(conn, old_game, [line])
    assert conn.execute("SELECT team FROM players WHERE espn_id='3945274'").fetchone()[0] == "LAL"


def _roster_conn(conn):
    db.upsert_teams(conn, [espn.Team(str(i), f"T{i}", f"Team {i}") for i in range(30)])
    conn.executemany(
        "INSERT INTO players(espn_id, name, team, team_as_of, position, updated_at) VALUES (?,?,?,?,?,?)",
        [("1", "Signed Guy", "T0", "2026-06-30", "G", "x"), ("2", "Waived Guy", "T1", "2026-06-30", "G", "x")],
    )


class RosterEspn:
    def __init__(self, fail_team=None, per_team=13):
        self.fail_team, self.per_team = fail_team, per_team

    def team_roster(self, team_id):
        if team_id == self.fail_team:
            raise FetchError("roster", 500, "boom")
        filler = [(f"{team_id}-{k}", f"P{team_id}-{k}", "F") for k in range(self.per_team - (team_id == "5"))]
        return filler + ([("1", "Signed Guy", "G")] if team_id == "5" else [])


def test_sync_rosters_marks_unrostered_players_without_team(conn):
    _roster_conn(conn)
    res = sync.sync_rosters(conn, RosterEspn(), date(2026, 10, 21))
    team = dict(conn.execute("SELECT espn_id, team FROM players WHERE espn_id IN ('1','2')").fetchall())
    assert team == {"1": "T5", "2": db.NO_NBA_TEAM}
    assert res.failures == 0 and "1 without an NBA team" in res.notes[0]


def test_sync_rosters_partial_fetch_marks_nobody(conn):
    _roster_conn(conn)
    res = sync.sync_rosters(conn, RosterEspn(fail_team="7"), date(2026, 10, 21))
    assert res.failures == 1
    assert conn.execute("SELECT team FROM players WHERE espn_id='2'").fetchone()[0] == "T1"


def test_sync_rosters_small_response_marks_nobody(conn):
    _roster_conn(conn)
    sync.sync_rosters(conn, RosterEspn(per_team=5), date(2026, 10, 21))
    assert conn.execute("SELECT team FROM players WHERE espn_id='2'").fetchone()[0] == "T1"


def test_later_box_score_overrides_no_team(conn):
    _roster_conn(conn)
    sync.sync_rosters(conn, RosterEspn(), date(2026, 10, 21))
    conn.execute(
        "INSERT INTO games(event_id, season, season_type, start_utc, et_date, home, away, state, "
        "completed, neutral, time_valid, updated_at) VALUES ('9',2027,2,'x','2026-10-23','T3','X','post',"
        "1,0,1,'x')"
    )
    line = espn.BoxLine("9", "2", "Waived Guy", "T3", False, True, "", 10, 2, 1, 3, 0, 1, 0, 0, 1, 1, 0, 0, 0)
    db.store_boxscore(conn, {"event_id": "9", "et_date": "2026-10-23", "season": 2027}, [line])
    assert conn.execute("SELECT team, name FROM players WHERE espn_id='2'").fetchone()[:] == ("T3", "Waived Guy")


def test_empty_season_averages_purge_stale_rows(conn):
    db.upsert_season_averages(conn, espn.parse_byathlete(BYATHLETE, 2027), as_of=date(2026, 10, 21))
    fake = FakeEspn()
    fake.season_averages = lambda season: []
    res = sync.sync_season_averages(conn, fake, 2027, date(2026, 10, 21))
    assert res.count == 0
    assert conn.execute("SELECT COUNT(*) FROM season_averages WHERE season = 2027").fetchone()[0] == 0
