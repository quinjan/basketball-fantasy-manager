---
name: weekly-retro-preview
description: Monday run (~15:00 UTC+8, after the fantasy week ends). Grade last week's decisions against outcomes, update the playbook with evidence, scout the new opponent, plan the new week, and email the retro.
---

# Weekly retro + preview

Run as the `gm` agent.

## Retro (last week)
1. `context_pack`, `refresh_data`. Read last week's journal entries and executed proposals
   (`list_proposals(status="executed")`).
2. For each executed move: compare expectation (evidence at proposal time) vs outcome — the added player's actual
   games and category contributions since (`player_card` last games) vs the dropped player's. Grade:
   **Good / Neutral / Bad / Too early**, with numbers.
3. Matchup result by category (when Yahoo data is live) vs the pre-week simulation. Which categories surprised?
4. **Playbook:** add or revise lessons in `memory/playbook.md` only when supported by evidence; each lesson records
   "Evidence: …" and a count of supporting cases. Remove lessons contradicted by evidence. Keep it under ~60 lines.

## Preview (new week)
5. `schedule` (games per NBA team this week), `simulate_matchup` vs the new opponent, `league_profile` if the
   strategy needs a check. Update `memory/managers/<opponent>.md` with what you know about them.
6. Week plan: target swing categories, streaming plan (days with few games for my roster, add budget), lineup
   notes, players to watch. Draft proposals only if clearly positive (reviewed via the `reviewer` agent).
7. If the evidence says the season strategy is wrong, draft a `strategy` proposal (see `season-strategy`).

## Output
8. Write `reports/weekly/<week-start>.md`, `send_email` kind `retro`, journal a summary (kind `retro`), and
   `uv run fbm sync-memory -m "weekly retro <date>"`.
