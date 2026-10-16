---
name: settings
description: Change how FBM runs. Use for "Change proposal expiry", "Change my GM models", "Pause the lineup guard", "Turn email on/off", "Tune the waiver threshold", "Show league settings".
---

# Settings

Run as the `gm` agent. Confirm every change with the owner in exact words before making it.

1. League rules come from Yahoo (`uv run fbm settings`); never hard-code them.
2. There is no autonomy to configure: FBM never changes the league (AGENTS.md). Lineups are recommendations;
   add/drop, claims, trades and strategy are proposals that need the owner's approval. Proposal expiry →
   `[proposals] expiry_hours`. Models → `uv run fbm setup` (`--gm-model`, `--reviewer-model`, `--routine-model`).
3. Schedules → `fbm automation install/list/remove` (daily-gm, lineup-guard, weekly-retro-preview, trade-scan, health).
4. Email/notifications → `config/settings.toml`; email is currently disabled (SMTP blocked), reports go to
   `reports/` (`uv run fbm notify test` to check).
5. Tuning → `constants` shows key, value, default, unit, source; override in `config/constants.toml` only with
   evidence (`prediction_calibration`, `grade_decisions`); journal before/after.
6. `health_check` after the change, `journal_append` (kind `owner`) (memory is saved locally).
