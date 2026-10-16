---
name: settings
description: Change how FBM runs. Use for "Change my autonomy", "Pause the lineup guard", "Turn email on/off", "Tune the waiver threshold", "Show league settings".
---

# Settings

Run as the `gm` agent. Confirm every change with the owner in exact words before making it.

1. League rules come from Yahoo (`uv run fbm settings`); never hard-code them.
2. Autonomy: only lineups and IL moves are auto-allowed; add/drop, claims, trades and strategy always need
   approval (AGENTS.md). Yahoo writes are out of scope (`[yahoo] api_writes = false`) — moves are manual steps.
3. Schedules → `fbm automation install/list/remove` (daily-gm, lineup-guard, weekly-retro-preview, trade-scan, health).
4. Email/notifications → `config/settings.toml`; email is currently disabled (SMTP blocked), reports go to
   `reports/` (`uv run fbm notify test` to check).
5. Tuning → `constants` shows key, value, default, unit, source; override in `config/constants.toml` only with
   evidence (`prediction_calibration`, `grade_decisions`); journal before/after.
6. `health_check` after the change, `journal_append` (kind `owner`) (memory is saved locally).
