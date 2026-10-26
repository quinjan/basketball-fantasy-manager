---
name: consult
description: Ad-hoc consultation with the owner — discuss an idea, a player, a trade, strategy, rules, or a change to the tool itself, then act on the outcome.
---

# Consult

1. `context_pack` (once per session).
2. If a named skill fits, follow it: `lineup`, `matchup`, `waivers`, `stream-plan`, `trade-lab`, `player`,
   `scout`, `league-intel`, `playoffs`, `approvals`, `memory`, `settings`, `health`,
   `season-strategy`, `weekly-retro-preview`. Drafts: the owner uses Hashtag Basketball's Draft Tracker (FBM has no
   draft tools). Otherwise restate the owner's question/idea in one line. Pull the relevant numbers with tools before forming a view.
3. Give a clear verdict first, then the justification (2–4 numbers), then risks and the best alternative.
4. Steelman the owner's idea before disagreeing. If they still want it, it's their team: create a proposal and
   run it through review (roster moves), or make the change (rules/config/code).
5. Changes the owner can request here, each confirmed explicitly, journaled, and synced:
   - Strategy → `season-strategy` flow (proposal).
   - Notes about other managers → `memory/managers/<team>.md`.
   - Playbook rules ("never drop X", "don't stream on Sundays") → `memory/playbook.md` under "Owner rules".
   - Proposal expiry/schedules/email preferences → `config/settings.toml` (and `fbm automation` schedules).
   - Engine changes → edit code with tests (`uv run pytest -q`, `uv run ruff check src tests`), then commit.
   - Decision rules (`policy_settings`: HT view per decision, bands, stream thresholds, odds spread scale, ...) →
     only a `policy` proposal backed by replay evidence (`policy_suggestions`) → `reviewer` → owner approval →
     `proposal_steps`. Never edit them directly, even when the owner asks: draft the proposal.
   - Tuning another number (penalties, play probabilities, health thresholds) → `config/constants.toml`
     (see the `constants` tool for keys, defaults, and sources); journal the before/after and the evidence.
6. End with a one-line summary of what changed (if anything) (memory is saved locally).
