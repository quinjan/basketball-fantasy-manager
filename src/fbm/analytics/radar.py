"""Pickup radar scoring: which unowned player just turned valuable?

Pure: callers supply what the engine already knows about each free agent (persisted early signals, minutes trend,
percent-owned movement, rest-of-season value vs my worst rostered player). A candidate earns points per reason and the
total maps to a radar confidence. Every threshold is in the constants registry (`radar.*`).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from fbm.constants import C

SIGNAL_KINDS = ("opportunity", "role_change", "buy_low", "espn_revision")
CONF_ORDER = {"high": 0, "medium": 1, "low": 2}


@dataclass(frozen=True)
class RadarInput:
    signals: Sequence[Mapping[str, Any]] = ()  # persisted feed rows for this player (kind, direction, confidence, note)
    recent_mpg: float | None = None
    minutes_trend: float | None = None  # recent minus season minutes per game
    owned_delta: float | None = None  # percent-owned change over the trend window (else Yahoo's weekly delta)
    owned_pct: float | None = None
    ros_gain: float = 0.0  # ROS value minus my worst rostered player
    worst_name: str = ""


@dataclass
class RadarScore:
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    cautions: list[str] = field(default_factory=list)

    @property
    def confidence(self) -> str:
        if self.score >= float(C("radar.high_score")):
            return "high"
        if self.score >= float(C("radar.medium_score")):
            return "medium"
        return "low"


def signal_points(confidence: str) -> float:
    return float(C(f"radar.points_{confidence}")) if confidence in CONF_ORDER else 0.0


def score(inp: RadarInput) -> RadarScore:
    out = RadarScore()
    ups = [s for s in inp.signals if s.get("kind") in SIGNAL_KINDS and s.get("direction") == "up"]
    downs = [s for s in inp.signals if s.get("direction") == "down"]
    ups.sort(key=lambda s: -signal_points(str(s.get("confidence"))))
    for i, s in enumerate(ups):
        out.score += signal_points(str(s.get("confidence"))) if i == 0 else float(C("radar.points_extra_signal"))
        out.reasons.append(f"{s['kind']} ({s.get('confidence')}): {s.get('note', '')}".rstrip(": "))
    out.cautions = [f"{s['kind']} (down, {s.get('confidence')}): {s.get('note', '')}".rstrip(": ") for s in downs]
    if (
        inp.minutes_trend is not None
        and inp.recent_mpg is not None
        and inp.minutes_trend >= float(C("radar.min_minutes_trend"))
        and inp.recent_mpg >= float(C("radar.min_recent_mpg"))
    ):
        out.score += float(C("radar.points_minutes"))
        out.reasons.append(f"minutes up {inp.minutes_trend:+.1f} mpg vs season (recent {inp.recent_mpg:.1f})")
    if inp.owned_delta is not None and inp.owned_delta >= float(C("radar.min_owned_surge")):
        out.score += float(C("radar.points_ownership"))
        now = f" (now {inp.owned_pct:.0f}%)" if inp.owned_pct is not None else ""
        out.reasons.append(f"ownership surge {inp.owned_delta:+.1f} pts{now}")
    if inp.ros_gain >= float(C("radar.min_ros_gain")):
        out.score += float(C("radar.points_value"))
        who = f" {inp.worst_name}" if inp.worst_name else ""
        out.reasons.append(f"ROS value {inp.ros_gain:+.2f} G above my worst rostered{who}")
    out.score = round(out.score, 2)
    return out


def meets(confidence: str, minimum: str) -> bool:
    return CONF_ORDER.get(confidence, 3) <= CONF_ORDER.get(minimum, 1)
