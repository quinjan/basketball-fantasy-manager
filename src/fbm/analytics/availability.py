"""Rest-of-season availability: the expected share of a player's remaining regular-season team games he plays.

Pure helpers plus a loader over the local store (injuries_current + games). Injury comment text is untrusted data:
it is only pattern-matched for "out for the season" wording, never interpreted as instructions.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

import pandas as pd

from fbm.constants import C

SEASON_OUT_PATTERN = re.compile(
    r"(out for the (?:entire |rest of the |remainder of the )?season|season[- ]ending|miss the (?:entire |rest of the "
    r"|remainder of the )?(?:\d{4}-\d{2} )?season|(?:rest|remainder) of the (?:\d{4}-\d{2} )?season)",
    re.IGNORECASE,
)
OUT_STATUSES = {"O", "OUT", "INJ", "IL", "IL+"}
SUSP_STATUSES = {"SUSP", "SUSPENSION"}
SHORT_STATUSES = {"DTD", "DAY-TO-DAY", "GTD", "QUESTIONABLE", "Q"}
DOUBTFUL_STATUSES = {"DOUBTFUL", "D"}


@dataclass(frozen=True)
class Availability:
    share: float
    reason: str


def default_games_missed(status: str) -> float | None:
    """Expected games missed for a status with no usable return date (None = healthy)."""
    key = (status or "").strip().upper()
    if key in OUT_STATUSES:
        return float(C("availability.out_default_games"))
    if key in SUSP_STATUSES:
        return float(C("availability.susp_default_games"))
    if key in DOUBTFUL_STATUSES:
        return float(C("availability.doubtful_default_games"))
    if key in SHORT_STATUSES:
        return float(C("availability.dtd_default_games"))
    return None


def availability_share(
    status: str | None,
    remaining_games: int,
    games_before_return: int | None = None,
    comment: str | None = None,
) -> Availability:
    """Share of the remaining team games the player is expected to play (0..1) and why.

    - fantasy status OFS or comment wording like "out for the season" -> 0;
    - a future return date -> 1 - games_before_return / remaining_games;
    - otherwise a status default (availability.*_default_games) of games missed;
    - healthy -> 1.0. The result is capped to [0, 1].
    """
    key = (status or "").strip().upper()
    if key == "OFS" or (comment and SEASON_OUT_PATTERN.search(comment)):
        return Availability(0.0, "out for the season")
    if remaining_games <= 0:
        return Availability(1.0, "no remaining games scheduled")
    if games_before_return is not None and games_before_return > 0:
        missed, why = float(games_before_return), f"misses ~{games_before_return} games before return date"
    else:
        default = default_games_missed(key)
        if default is None:
            return Availability(1.0, "healthy")
        missed, why = default, f"{key or 'injured'}: assumed {default:g} games missed (no return date)"
    share = min(1.0, max(0.0, 1.0 - missed / remaining_games))
    return Availability(share, why)


def ros_adjust(values: pd.DataFrame, share: Mapping[str, float], pool_size: int) -> pd.DataFrame:
    """Scale each player's G-scores toward replacement level by availability.

    ros = repl + (g - repl) * share per category, where repl is the G-score line of the pool_size-th best player by
    g_total. Below-replacement players are left unchanged (missing games can't make them worse than the waiver
    wire you'd stream instead). g_total is recomputed as the sum of the adjusted category columns.
    """
    out = values.copy()
    cols = [c for c in values.columns if c.startswith("g_") and c != "g_total"]
    if not cols or values.empty:
        return out
    top = values["g_total"].nlargest(max(1, int(pool_size)))
    repl = values.loc[top.index[-1], cols].astype(float)
    a = pd.Series(share, dtype=float).reindex(values.index).fillna(1.0)
    a = a.where(values["g_total"] > float(top.iloc[-1]), 1.0)
    for col in cols:
        out[col] = repl[col] + (values[col] - repl[col]) * a
    out["g_total"] = out[cols].sum(axis=1)
    return out


def load_availability(conn: sqlite3.Connection, team_of: Mapping[str, str], today: date) -> pd.DataFrame:
    """Availability share + reason for every injured player in injuries_current (others are implicitly 1.0)."""
    games = pd.read_sql_query(
        "SELECT et_date, home, away FROM games WHERE season_type = 2 AND et_date >= ?",
        conn,
        params=(today.isoformat(),),
    )
    long = pd.concat(
        [
            games[["et_date", "home"]].rename(columns={"home": "team"}),
            games[["et_date", "away"]].rename(columns={"away": "team"}),
        ]
    )
    rows = {}
    for r in conn.execute(
        "SELECT espn_id, status, fantasy_status, return_date, short_comment, long_comment FROM injuries_current"
    ):
        pid = r["espn_id"]
        team = team_of.get(pid, "")
        sched = long.loc[long["team"] == team, "et_date"]
        remaining = int(len(sched))
        before = None
        if r["return_date"]:
            before = int((sched < str(r["return_date"])[:10]).sum())
        status = (r["fantasy_status"] or "").strip() or (r["status"] or "")
        comment = " ".join(x for x in (r["short_comment"], r["long_comment"]) if x)
        a = availability_share(status, remaining, before, comment)
        rows[pid] = {"availability_ros": round(a.share, 3), "availability_reason": a.reason}
    return pd.DataFrame.from_dict(rows, orient="index", columns=["availability_ros", "availability_reason"])
