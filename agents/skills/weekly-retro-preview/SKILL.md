---
name: weekly-retro-preview
description: Monday run (~10:30 ET, after the fantasy week ends). Grade last week's decisions against outcomes, check simulator calibration, update the playbook with evidence, scout the new opponent, plan the new week, and report.
---

# Weekly retro + preview

Run as the `gm` agent.

## Retro (last week)
1. `context_pack`, `refresh_data`, then `weekly_retro_data` (defaults to the last completed fantasy week). It
   returns: graded completed (`done`) moves (realized production acquired minus given up, in team-week SD units:
   good ≥ +0.5, bad ≤ −0.5, or too early), proposals created with reviewer verdicts and owner quotes, graded
   matchup predictions, season calibration, and the decision-calibration block: decisions logged/graded this
   week, regret by kind (replay on real box scores + Yahoo FINAL results), `odds_reliability` and the `per_view`
   table (each decision kind re-chosen under every HT view: categories won and ROS value per view, `best_view`).
   In the Yahoo league also read `scoreboard` (final category results) and `standings` (record, playoff race).
2. Summarize decisions: what worked, what didn't, and why (compare with the evidence recorded at proposal time).
   Note rejected/expired proposals and the owner's stated reasons.
3. **Decision calibration** (FBM calibrates decisions, not projections — HT owns projection accuracy):
   - `replay_grade` (fills in what was actually chosen, grades every option) and `decisions_log(since=<week start>)`:
     regret in categories by kind (lineup, stream_plan, waiver_claim, drop, il, trade, chase_concede,
     category_weights). Name the decisions with the biggest regret and why.
   - `per_view` table: which HT view would have chosen better for each kind vs the view in force.
   - `odds_reliability` (and `prediction_calibration` for legacy pre-HT forecasts): Brier vs coin flip,
     reliability buckets, expected vs actual categories. If the official odds are off (e.g. our 80% calls win 60%),
     say so and hedge close calls.
   - `policy_suggestions`: changes that would have won more categories on replay (≥ `policy.min_weeks` graded weeks,
     each within its max step). For each worthwhile one, draft a `policy` proposal with the payload it ships
     (`create_proposal(kind="policy", payload_json=..., evidence_json=...)`) → `reviewer` agent → owner approval
     (`approvals`; email kind `approval` if unattended) → `proposal_steps` applies it (status `done`). Never edit
     policy settings any other way; at most 2 policy proposals per retro.
   **Signals:** `signal_grades` (also in `weekly_retro_data`): hit rate per kind and confidence, lead time over HT's
   projection and over % owned, how often we acted, and the replay grade of those decisions. If a kind keeps hitting below 50% over ≥ 20 graded signals,
   propose raising its threshold (or `signals.feed_min_confidence`) in `config/constants.toml` with the numbers.
4. **Playbook:** add or revise lessons in `memory/playbook.md` only with evidence (cite refs and counts). Remove
   lessons contradicted by evidence. Keep it under ~60 lines. Decision-rule changes are `policy` proposals (step 3);
   other registry constants → propose a `config/constants.toml` change to the owner with the numbers.

## Preview (new week)
5. `schedule`, `weekly_plan(trigger="daily-gm")` / `simulate_matchup(record=true)` vs the new opponent (official
   odds; HT's share "probability" is reference only), `streaming_plan` for the week, and
   `league_profile` if the strategy needs a check. Scout the opponent (`scout` skill; `league_intel` /
   `manager_profile` if available) and update `memory/managers/<opponent>.md`.
6. Week plan: swing categories, streaming plan (adds budget), lineup notes, watchlist. Draft proposals only if
   clearly positive (reviewed via the `reviewer` agent).
7. If the evidence says the season strategy is wrong, draft a `strategy` proposal (see `season-strategy`).

## Output
8. Write `reports/weekly/<week-start>.md`, `send_email` kind `retro`, and journal a summary (kind `retro`)
   (memory is saved locally).
