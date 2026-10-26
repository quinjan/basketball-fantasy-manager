"""Waiver status helpers: when does a player on waivers clear (first ET day he can be added)?

The Yahoo snapshot (`LeagueState.on_waivers`) maps player id -> the ET date he clears waivers. Until then he cannot
be added as a free agent: only claimed (rolling priority), and a claim processes on that date, so either way the
earliest effective day for his stats is the clear date."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import date


def clear_dates(on_waivers: Mapping[str, str], only: Iterable[str] | None = None) -> dict[str, date]:
    """player id -> ET date he clears waivers (entries with an unreadable date are ignored)."""
    keep = None if only is None else set(only)
    out: dict[str, date] = {}
    for pid, raw in on_waivers.items():
        if keep is not None and pid not in keep:
            continue
        try:
            out[pid] = date.fromisoformat(str(raw)[:10])
        except ValueError:
            continue
    return out


def games_from(team: str, days: Sequence[date], playing: Mapping[date, set[str]], first: date | None = None) -> int:
    """Games `team` plays on `days` on/after `first` (all days when None)."""
    return sum(1 for d in days if team in playing.get(d, set()) and (first is None or d >= first))
