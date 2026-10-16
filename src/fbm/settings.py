"""Typed access to config/settings.toml."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from fbm.config import CONFIG_DIR

SETTINGS_PATH = CONFIG_DIR / "settings.toml"


@dataclass(frozen=True)
class Settings:
    league_source: str = "demo"
    auto_lineups: bool = True
    auto_il_moves: bool = True
    approval_required: tuple[str, ...] = ("add_drop", "waiver_claim", "trade", "strategy")
    default_expiry_hours: float = 18.0
    gm_model: str = "claude-opus-5.5"
    reviewer_model: str = "gpt-5.6-sol"
    email_enabled: bool = True
    recipients: tuple[str, ...] = field(default_factory=tuple)
    quiet_hours: str = ""
    git_account: str = "quinjan"


def load_settings(path: Path = SETTINGS_PATH) -> Settings:
    if not path.exists():
        return Settings()
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    league, autonomy = raw.get("league", {}), raw.get("autonomy", {})
    review, notify = raw.get("review", {}), raw.get("notify", {})
    default = Settings()
    return Settings(
        league_source=league.get("source", default.league_source),
        auto_lineups=autonomy.get("auto_lineups", default.auto_lineups),
        auto_il_moves=autonomy.get("auto_il_moves", default.auto_il_moves),
        approval_required=tuple(autonomy.get("approval_required", default.approval_required)),
        default_expiry_hours=float(autonomy.get("default_expiry_hours", default.default_expiry_hours)),
        gm_model=review.get("gm_model", default.gm_model),
        reviewer_model=review.get("reviewer_model", default.reviewer_model),
        email_enabled=notify.get("email_enabled", default.email_enabled),
        recipients=tuple(notify.get("recipients", ())),
        quiet_hours=notify.get("quiet_hours", ""),
        git_account=raw.get("git", {}).get("account", default.git_account),
    )
