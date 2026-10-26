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
5. Hashtag Basketball → `[hashtag]` in `config/settings.toml`: `enabled` (true = HT is the data layer and
   suggestions pause when it is unhealthy), `league_id` (HT's league id = the Yahoo league id). Sign-in:
   `uv run fbm hashtag login` (owner only); check: `fbm hashtag check` / `fbm hashtag status`. Emergency kill switch
   without editing the file: environment variable `FBM_HASHTAG_ENABLED=0` (or `1`) overrides `enabled`.
6. Decision rules (HT view per decision, contested/concede/lock bands, stream thresholds, add reserve, IL hold days,
   odds spread scale, season category multipliers, ...) → `policy_settings` shows them. They change **only** through
   a `policy` proposal (`policy_suggestions` evidence → `create_proposal(kind="policy")` → `reviewer` → owner
   approval → `proposal_steps`), never by editing config.
7. Other tuning → `constants` shows key, value, default, unit, source; override in `config/constants.toml` only
   with evidence (`odds_reliability`, `replay_grade`, `grade_decisions`); journal before/after.
8. `health_check` after the change, `journal_append` (kind `owner`) (memory is saved locally).
