"""ESPN fantasy metadata parsing (eligibility slots, ADP, ranks)."""

from fbm.data.espn_fantasy import eligibility_from_slots, parse_players


def test_espn_fantasy_parsing():
    assert eligibility_from_slots([4, 9, 10, 11, 12, 13]) == ("C", "UTIL")
    assert eligibility_from_slots([0, 1, 5, 11]) == ("PG", "SG", "G", "UTIL")
    payload = {
        "players": [
            {
                "id": 3112335,
                "player": {
                    "fullName": "Nikola Jokic",
                    "eligibleSlots": [4, 9, 11],
                    "ownership": {"averageDraftPosition": 1.42},
                    "draftRanksByRankType": {"STANDARD": {"rank": 1}, "ROTO": {"rank": 1}},
                },
            }
        ]
    }
    (m,) = parse_players(payload)
    assert (m.espn_id, m.adp, m.rank_roto, m.eligible) == ("3112335", 1.42, 1, ("C", "UTIL"))
