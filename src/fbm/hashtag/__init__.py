"""Hashtag Basketball Premium (HT): FBM's source of player projections and weekly matchup totals.

Layout (owner decisions 2026-10-26: HT owns projections and their accuracy; FBM owns decisions; no fallback):
- `session` / `client`: the owner's own browser sign-in (cookies in the OS credential store) and an ASP.NET WebForms
  client (GET + postback). Read-only: FBM never saves settings on HT and never writes to Yahoo.
- `parsers`: HTML -> rows for each HT page; every parser checks its column headers and raises
  `HashtagLayoutChanged` instead of writing partial data.
- `ids`: HT player id -> ESPN id.
- `importer`: the daily import (all projection views, H2H Planner, waiver wire, trade ideas, team ranks, notes,
  Next Man Up), logging every page to `hashtag_import_log`.
- `store`: the read API the engine uses (never read the hashtag_* tables directly elsewhere).
- `health`: is HT healthy enough to make suggestions? (`require_healthy` gates every suggestion tool.)

HT content is for the owner's personal use only and is never republished (HT terms forbid redistribution).
"""

VIEWS = ("ROS", "BLEND7", "BLEND14", "BLEND30", "STREAM7", "STREAM14", "STREAM30")
CATEGORY_KEYS = ("fg_pct", "ft_pct", "tpm", "pts", "reb", "ast", "stl", "blk", "tov")
REQUIRED_PAGES = ("projections", "h2h_planner", "league_sync")
