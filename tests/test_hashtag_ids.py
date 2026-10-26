"""HT id -> ESPN id mapping (names + team, overrides) persisted in hashtag_players."""

import pytest

from fbm.hashtag import ids as ht_ids
from fbm.store import db


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "fbm.db")
    c.executemany(
        "INSERT INTO players (espn_id, name, team, updated_at) VALUES (?, ?, ?, '2026-10-01')",
        [
            ("1", "Nikola Jokic", "DEN"),
            ("2", "Alperen Sengun", "HOU"),
            ("3", "Jaren Jackson Jr.", "UTAH"),
            ("4", "Devin Booker", "PHX"),
            ("5", "Marcus Morris", "CLE"),
            ("6", "Markieff Morris", "DAL"),
        ],
    )
    c.commit()
    yield c
    c.close()


def test_map_players_methods_and_persistence(conn, tmp_path):
    overrides = tmp_path / "hashtag_id_overrides.toml"
    overrides.write_text('[players]\n"777" = "6"\n', encoding="utf-8")
    rows = [
        {"ht_id": "9196", "name": "Nikola Jokic", "team": "DEN"},
        {"ht_id": "31045", "name": "Alperen Sengün", "team": "HOU"},  # accent
        {"ht_id": "100", "name": "Jaren Jackson", "team": "UTA"},  # HT team abbr, no suffix
        {"ht_id": "101", "name": "Devin Booker", "team": "PHO"},
        {"ht_id": "777", "name": "Kieff Morris", "team": "DAL"},
        {"ht_id": "999", "name": "Nobody Atall", "team": "DEN"},
    ]
    out = ht_ids.map_players(conn, rows, overrides=ht_ids.load_overrides(overrides))
    assert out == {"9196": "1", "31045": "2", "100": "3", "101": "4", "777": "6", "999": None}
    methods = {r["ht_id"]: r["match_method"] for r in conn.execute("SELECT * FROM hashtag_players")}
    assert methods["9196"] == "exact" and methods["777"] == "override" and methods["999"] == "unmatched"
    assert ht_ids.lookup(conn, ["9196", "999"]) == {"9196": "1", "999": None}
    assert ht_ids.ht_for_espn(conn, "4") == {"ht_id": "101", "name": "Devin Booker", "team": "PHO"}
    assert ht_ids.unmatched_players(conn) == [{"ht_id": "999", "name": "Nobody Atall", "team": "DEN"}]


def test_missing_override_file_is_empty(tmp_path):
    assert ht_ids.load_overrides(tmp_path / "nope.toml") == {}
