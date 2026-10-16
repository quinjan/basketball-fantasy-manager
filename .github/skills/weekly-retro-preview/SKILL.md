---
name: weekly-retro-preview
description: Monday run (~15:30 UTC+8, after the fantasy week ends). Grade last week's decisions against outcomes, check simulator calibration, update the playbook with evidence, scout the new opponent, plan the new week, and report.
---

# Weekly retro + preview

Run as the `gm` agent.

## Retro (last week)
1. `context_pack`, `refresh_data`, then `weekly_retro_data` (defaults to the last completed fantasy week). It
   returns: graded executed moves (realized production acquired minus given up, in team-week SD units:
   good ≥ +0.5, bad ≤ −0.5, or too early), proposals created with reviewer verdicts and owner quotes, graded
   matchup predictions, and season calibration.
2. Summarize decisions: what worked, what didn't, and why (compare with the evidence recorded at proposal time).
   Note rejected/expired proposals and the owner's stated reasons.
3. **Calibration:** `prediction_calibration`. If Brier is not clearly better than the coin-flip baseline, or
   reliability buckets are off (e.g. our 80% calls win 60%), say so and be more cautious with close calls.
4. **Playbook:** add or revise lessons in `memory/playbook.md` only with evidence (cite refs and counts). Remove
   lessons contradicted by evidence. Keep it under ~60 lines.

## Preview (new week)
5. `schedule`, `simulate_matchup(record=true)` vs the new opponent, `streaming_plan` for the week, and
   `league_profile` if the strategy needs a check. Update `memory/managers/<opponent>.md`.
6. Week plan: swing categories, streaming plan (adds budget), lineup notes, watchlist. Draft proposals only if
   clearly positive (reviewed via the `reviewer` agent).
7. If the evidence says the season strategy is wrong, draft a `strategy` proposal (see `season-strategy`).

## Output
8. Write `reports/weekly/<week-start>.md`, `send_email` kind `retro`, journal a summary (kind `retro`), and
   `uv run fbm sync-memory -m "weekly retro <date>"`.
