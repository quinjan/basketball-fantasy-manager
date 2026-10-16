"""Scoring categories. Defaults to standard 9-cat; can be derived from Yahoo league settings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Category:
    key: str  # internal key, e.g. "pts", "fg_pct"
    label: str  # display label, e.g. "PTS", "FG%"
    kind: str  # "count" | "pct"
    stat: str  # per-game stat column for counting cats
    made: str = ""  # pct cats: makes column
    att: str = ""  # pct cats: attempts column
    higher_is_better: bool = True
    yahoo_stat_id: str = ""


FG_PCT = Category("fg_pct", "FG%", "pct", "", "fgm", "fga", True, "5")
FT_PCT = Category("ft_pct", "FT%", "pct", "", "ftm", "fta", True, "8")
TPM = Category("tpm", "3PM", "count", "tpm", yahoo_stat_id="10")
PTS = Category("pts", "PTS", "count", "pts", yahoo_stat_id="12")
REB = Category("reb", "REB", "count", "reb", yahoo_stat_id="15")
AST = Category("ast", "AST", "count", "ast", yahoo_stat_id="16")
STL = Category("stl", "STL", "count", "stl", yahoo_stat_id="17")
BLK = Category("blk", "BLK", "count", "blk", yahoo_stat_id="18")
TOV = Category("tov", "TO", "count", "tov", higher_is_better=False, yahoo_stat_id="19")

NINE_CAT: tuple[Category, ...] = (FG_PCT, FT_PCT, TPM, PTS, REB, AST, STL, BLK, TOV)
_BY_YAHOO_ID = {c.yahoo_stat_id: c for c in NINE_CAT}
_ALIASES = {c.label.lower(): c for c in NINE_CAT} | {c.key: c for c in NINE_CAT} | {"3ptm": TPM, "to": TOV, "st": STL}

BASE_STATS = ("minutes", "pts", "reb", "ast", "stl", "blk", "tov", "tpm", "tpa", "fgm", "fga", "ftm", "fta")


def from_league_settings(summary: dict[str, Any]) -> tuple[Category, ...]:
    """Map Yahoo scoring categories onto supported Category definitions (unknown stats are skipped)."""
    cats = [_BY_YAHOO_ID[c["stat_id"]] for c in summary.get("scoring_categories", []) if c["stat_id"] in _BY_YAHOO_ID]
    return tuple(cats) or NINE_CAT


def parse_category_list(text: str | None) -> tuple[Category, ...]:
    """Parse e.g. 'FT%,TO' into categories (used for punts)."""
    if not text:
        return ()
    out = []
    for token in (t.strip().lower() for t in text.split(",")):
        if not token:
            continue
        if token not in _ALIASES:
            raise ValueError(f"Unknown category '{token}'. Use one of: {', '.join(c.label for c in NINE_CAT)}")
        out.append(_ALIASES[token])
    return tuple(out)
