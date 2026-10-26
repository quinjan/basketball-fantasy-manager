"""`fbm migrate`: one-off migrations of the owner's local data. Registered by importing this module from fbm.cli."""

from __future__ import annotations

import json
from datetime import date

import typer

from fbm.cli import app, console

migrate_app = typer.Typer(no_args_is_help=True, help="One-off migrations of your local FBM data (backup first).")
app.add_typer(migrate_app, name="migrate")


@migrate_app.command("overhaul")
def overhaul_cmd(
    league_id: str = typer.Option("", help="Hashtag league id (default: the Yahoo league id from config/league.toml)."),
    enable: bool = typer.Option(True, help="Write [hashtag] enabled = true into config/settings.toml."),
    yahoo: bool = typer.Option(True, help="Cache Yahoo finals + daily lineups and re-grade legacy forecasts."),
) -> None:
    """Migrate to the Hashtag data layer + decision calibration (backup, settings, decision log, Yahoo re-grade)."""
    from fbm import backup
    from fbm.config import RAW_DIR, load_league_config
    from fbm.gm import memory, migrate
    from fbm.store import db

    cfg = load_league_config()
    lid = league_id or cfg.league_key.split(".l.")[-1]
    summary_path = RAW_DIR / "league_settings_summary.json"
    start = None
    if summary_path.exists():
        raw = json.loads(summary_path.read_text(encoding="utf-8")).get("start_date")
        start = date.fromisoformat(raw) if raw else None
    team_key = cfg.team_key
    if not team_key:
        from fbm.gm import services as svc

        team_key = svc.active_league().my_team_key
    sync = regrade = None
    if yahoo:
        from fbm.gm import decision_services, replay

        sync = decision_services.sync_yahoo_results
        regrade = replay.regrade_legacy_predictions
    conn = db.connect()
    report = migrate.overhaul(
        conn,
        team_key=team_key,
        league_id=lid,
        season_start=start,
        enable=enable,
        sync_yahoo=sync,
        regrade=regrade,
        do_backup=backup.backup,
        journal=memory.journal_append,
    )
    console.print_json(json.dumps(report, default=str))
