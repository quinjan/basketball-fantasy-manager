"""Typed access to config/settings.toml (created per install by `fbm setup` from config/settings.example.toml)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from fbm.config import CONFIG_DIR

SETTINGS_PATH = CONFIG_DIR / "settings.toml"
EXAMPLE_PATH = CONFIG_DIR / "settings.example.toml"

AGENTS = ("copilot", "claude-code", "codex", "cursor", "gemini", "generic")
AUTOMATION_MODES = ("none", "agent-app", "scheduler", "engine-only")
JOBS = ("data-sync", "lineup-guard", "daily-gm", "weekly-retro", "trade-scan", "health")


@dataclass(frozen=True)
class Settings:
    league_source: str = "demo"
    # FBM never changes the league: it produces insights, recommendations and proposals; the owner makes every move.
    default_expiry_hours: float = 18.0  # unapproved proposals expire ([proposals] expiry_hours)
    # Owner + AI agent (chosen at setup; nothing here assumes a particular agent or model vendor).
    owner_timezone: str = "UTC"  # IANA name, e.g. "America/Chicago"; Yahoo game days are always US/Eastern
    agent: str = "generic"  # one of AGENTS
    gm_model: str = ""  # decision runs (a frontier model)
    reviewer_model: str = ""  # independent review (ideally a different model family than the GM)
    routine_model: str = ""  # cheap routine runs (lineup guard, health)
    automation_mode: str = "none"  # one of AUTOMATION_MODES
    automation_jobs: tuple[str, ...] = field(default_factory=tuple)  # subset of JOBS
    email_enabled: bool = False
    recipients: tuple[str, ...] = field(default_factory=tuple)
    quiet_hours: str = ""


def load_settings(path: Path = SETTINGS_PATH) -> Settings:
    if not path.exists():
        return Settings()
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    league = raw.get("league", {})
    proposals = raw.get("proposals", {}) or raw.get("autonomy", {})  # [autonomy] is the pre-1.0 name
    agent, review, notify = raw.get("agent", {}), raw.get("review", {}), raw.get("notify", {})
    automation = raw.get("automation", {})
    default = Settings()
    return Settings(
        league_source=league.get("source", default.league_source),
        default_expiry_hours=float(
            proposals.get("expiry_hours", proposals.get("default_expiry_hours", default.default_expiry_hours))
        ),
        owner_timezone=raw.get("owner", {}).get("timezone", default.owner_timezone),
        agent=agent.get("name", default.agent),
        # [review] is the pre-1.0 location of the model names; [agent] wins.
        gm_model=agent.get("gm_model", review.get("gm_model", default.gm_model)),
        reviewer_model=agent.get("reviewer_model", review.get("reviewer_model", default.reviewer_model)),
        routine_model=agent.get("routine_model", default.routine_model),
        automation_mode=automation.get("mode", default.automation_mode),
        automation_jobs=tuple(automation.get("jobs", default.automation_jobs)),
        email_enabled=notify.get("email_enabled", default.email_enabled),
        recipients=tuple(notify.get("recipients", ())),
        quiet_hours=notify.get("quiet_hours", ""),
    )
